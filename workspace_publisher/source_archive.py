"""Content-verified source archives; a manifest is not Git provenance or a signature."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .config import PublisherError, active, checked_version, safe_path

MANIFEST = 'workspace-source-manifest.json'
MAX_FILES = 20000
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
# Only folders created locally by the publisher, build scripts or test/runtime
# tools may sit alongside a downloaded source archive. None are ever copied.
LOCAL_ROOTS = {'build', 'dist', '.pytest_cache'}
PROHIBITED_SUFFIXES = {'.exe', '.dll', '.msi', '.msix', '.appx', '.com', '.scr',
                       '.so', '.dylib', '.pyd', '.zip', '.nupkg', '.7z', '.rar',
                       '.tar', '.tgz', '.whl', '.pyc', '.pyo', '.gitbundle', '.bundle'}


def source_path(value):
    if (not isinstance(value, str) or not value or len(value) > 1024
            or '\\' in value or value.startswith('/') or any(ord(c) < 32 for c in value)):
        raise PublisherError('소스 목록에 안전하지 않은 파일 경로가 있습니다.')
    parts = value.split('/')
    if (any(not part or part in {'.', '..'} or any(c in part for c in ':<>"|?*') or part.endswith((' ', '.'))
            or re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
            for part in parts)
            or any(part.casefold() in {'.git', '__pycache__'} for part in parts)
            or parts[0].casefold() in LOCAL_ROOTS or value.casefold() == MANIFEST.casefold()):
        raise PublisherError('소스 목록에 빌드용 파일이나 안전하지 않은 경로가 있습니다.')
    return value


def source_bytes(name, raw):
    """Keep native executables/SDK archives out of source-only transfer files."""
    if len(raw) > MAX_FILE:
        raise PublisherError('소스 파일 하나가 확인할 수 있는 크기를 넘었습니다.')
    if (Path(name).suffix.casefold() in PROHIBITED_SUFFIXES
            or raw.startswith((b'MZ', b'\x7fELF', b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08',
                               b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe'))):
        raise PublisherError('소스 목록에 실행 파일이나 SDK 압축 파일이 포함되어 있습니다. 소스 전용 ZIP으로 다시 준비해 주세요.')
    if name.casefold().endswith('.gz'):
        # Existing upstream font assets are allowed, but compressed executable
        # payloads cannot be renamed as font assets to enter the source archive.
        try:
            import io
            if not name.casefold().endswith('.ttf.gz'):
                raise ValueError()
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                font = stream.read(MAX_FILE + 1)
            if len(font) > MAX_FILE or not font.startswith((b'\x00\x01\x00\x00', b'OTTO', b'ttcf')):
                raise ValueError()
        except (OSError, EOFError, ValueError) as exc:
            raise PublisherError('압축된 소스 자료는 확인된 글꼴 파일만 포함할 수 있습니다.') from exc


def manifest_bytes(files):
    """Generate deterministic bytes from exact Git index blob contents."""
    entries, seen, total = [], set(), 0
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise PublisherError('소스 파일 목록의 개수가 올바르지 않습니다.')
    for name, raw in sorted(files.items()):
        source_path(name)
        if name.casefold() in seen or not isinstance(raw, bytes):
            raise PublisherError('소스 파일 목록에 중복되거나 올바르지 않은 항목이 있습니다.')
        seen.add(name.casefold())
        source_bytes(name, raw)
        total += len(raw)
        if total > MAX_TOTAL:
            raise PublisherError('소스의 전체 크기가 확인 범위를 넘었습니다.')
        entries.append({'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    try:
        text = files['local_app/server.py'].decode('utf-8-sig')
        versions = re.findall(r'^WORKSPACE_VERSION = [\"\']([^\"\']+)[\"\']\s*$', text, re.M)
        if len(versions) != 1:
            raise ValueError()
        version = checked_version(versions[0])
    except (KeyError, UnicodeError, ValueError) as exc:
        raise PublisherError('소스 목록에서 앱 버전을 확인하지 못했습니다.') from exc
    raw = (json.dumps({'schema': 1, 'version': version, 'files': entries},
                      ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if len(raw) > MAX_MANIFEST:
        raise PublisherError('소스 목록이 확인할 수 있는 크기를 넘었습니다.')
    return raw


def _read(path, limit):
    path = safe_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise PublisherError('소스 파일이 일반 파일이 아니거나 확인 크기를 넘었습니다.')
    with path.open('rb') as stream:
        # Allocate for the already bounded actual file size, not MAX_FILE for
        # each tiny source file. The extra byte still detects growth mid-read.
        raw = stream.read(info.st_size + 1)
    safe_path(path)
    if len(raw) > limit or len(raw) != info.st_size:
        raise PublisherError('확인 중 소스 파일 크기가 바뀌었습니다. 다시 받아 주세요.')
    return raw


def _manifest(root):
    try:
        raw = _read(root / MANIFEST, MAX_MANIFEST)
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'files'}
                or type(value['schema']) is not int or value['schema'] != 1):
            raise ValueError()
        version = checked_version(value['version'])
        entries = value['files']
        if not isinstance(entries, list) or not 0 < len(entries) <= MAX_FILES:
            raise ValueError()
        expected, seen, total = {}, set(), 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
                raise ValueError()
            name = source_path(entry['path'])
            if (name.casefold() in seen or type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_FILE
                    or not isinstance(entry['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])):
                raise ValueError()
            seen.add(name.casefold())
            total += entry['size']
            if total > MAX_TOTAL:
                raise ValueError()
            expected[name] = entry
        if 'local_app/server.py' not in expected or '.gitignore' not in expected:
            raise ValueError()
        return raw, version, expected
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise PublisherError('다운로드 ZIP의 소스 확인 목록이 없거나 올바르지 않습니다. 최신 Code → Download ZIP을 다시 받아 압축을 풀어 주세요.') from exc


def _walk(root, cancel):
    found = set()
    count = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        active(cancel)
        folder = safe_path(Path(folder))
        retained = []
        for name in directories:
            count += 1
            if count > MAX_FILES * 2:
                raise PublisherError('다운로드 소스 항목 수가 확인 범위를 넘었습니다.')
            path = safe_path(folder / name)
            if name == '__pycache__' or folder == root and name in LOCAL_ROOTS:
                continue
            # An archive may not quietly adopt a nested checkout or submodule.
            if name.casefold() == '.git':
                raise PublisherError('다운로드 소스 안에 예상하지 않은 Git 폴더가 있습니다.')
            retained.append(name)
        directories[:] = retained
        for name in files:
            path = safe_path(folder / name)
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST:
                source_path(relative)
                found.add(relative)
                count += 1
                if count > MAX_FILES * 2:
                    raise PublisherError('다운로드 소스 파일 수가 확인 범위를 넘었습니다.')
    return found


def verified(root, *, cancel=None):
    root = safe_path(Path(root).absolute())
    try:
        raw, version, expected = _manifest(root)
        if _walk(root, cancel) != set(expected):
            raise PublisherError('다운로드 소스에 빠진 파일이나 추가된 파일이 있습니다. 새 폴더에 ZIP을 다시 풀어 주세요. build·dist·Python 캐시만 별도로 둘 수 있습니다.')
        source_version = None
        for name, entry in expected.items():
            active(cancel)
            content = _read(root / name, MAX_FILE)
            source_bytes(name, content)
            if len(content) != entry['size'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
                raise PublisherError('다운로드 소스 파일이 원본 목록과 다릅니다. 소스를 수정하지 않은 새 ZIP으로 다시 준비해 주세요.')
            if name == 'local_app/server.py':
                versions = re.findall(r'^WORKSPACE_VERSION = [\"\']([^\"\']+)[\"\']\s*$', content.decode('utf-8-sig'), re.M)
                source_version = versions[0] if len(versions) == 1 else None
        if source_version != version:
            raise PublisherError('다운로드 소스 버전과 확인 목록 버전이 다릅니다.')
        return raw, version, expected
    except (OSError, UnicodeError) as exc:
        raise PublisherError('다운로드 소스 파일을 확인하지 못했습니다. 압축 해제 위치와 파일 권한을 확인해 주세요.') from exc


def inspect(root, *, cancel=None):
    raw, version, _ = verified(root, cancel=cancel)
    return {'repo': str(Path(root).absolute()), 'commit': '', 'branch': '', 'version': version,
            'clean': True, 'releaseTag': '', 'sourceKind': 'archive',
            'sourceId': hashlib.sha256(raw).hexdigest(), 'canSync': False}


def copy_verified(root, target, source_id, *, cancel=None):
    root, target = safe_path(Path(root).absolute()), safe_path(Path(target).absolute())
    raw, _, expected = verified(root, cancel=cancel)
    if hashlib.sha256(raw).hexdigest() != source_id:
        raise PublisherError('빌드 준비 중 다운로드 소스 목록이 바뀌었습니다. 다시 빌드해 주세요.')
    target.mkdir()
    for name, entry in expected.items():
        active(cancel)
        content = _read(root / name, MAX_FILE)
        if len(content) != entry['size'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise PublisherError('별도 폴더로 복사하는 동안 소스가 바뀌었습니다. 다시 빌드해 주세요.')
        destination = safe_path(target / name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(content)
    with (target / MANIFEST).open('xb') as stream:
        stream.write(raw)
    if inspect(target, cancel=cancel)['sourceId'] != source_id or inspect(root, cancel=cancel)['sourceId'] != source_id:
        raise PublisherError('소스를 복사한 뒤 원본 확인 결과가 바뀌었습니다.')
    return target
