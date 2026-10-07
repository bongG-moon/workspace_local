"""Install verified app files without replacing a running application.

Only a successfully initialized new server promotes the durable launcher pointer.
Starting the launcher delegates all busy-job and unsent-draft handling to the existing
cooperative launcher; this module never closes or terminates the old application.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import uuid
import zipfile

from .windows_process import powershell_path

MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = MAX_PACKAGE_BYTES  # Individual extracted files share the total bound.
MAX_FILES = 500
MAX_MANIFEST_BYTES = 256 * 1024
_VERSION = re.compile(r'(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_LOCK = threading.RLock()


def _version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError('앱 업데이트 버전을 확인하지 못했습니다.')
    return tuple(map(int, value.split('.')))


def _safe(path):
    path = Path(path)
    if not path.is_absolute() or any(part in {'.', '..'} for part in path.parts):
        raise ValueError('앱 업데이트 저장 위치를 확인하지 못했습니다.')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('연결된 앱 업데이트 경로는 사용하지 않습니다.')
    return path


def _root(state):
    root = _safe(Path(state) / 'updates')
    root.mkdir(parents=True, exist_ok=True)
    return _safe(root)


def _relative(name):
    if (not isinstance(name, str) or not 0 < len(name) <= 240 or '\\' in name
            or any(ord(character) < 32 for character in name)):
        raise ValueError('업데이트 파일 경로를 확인하지 못했습니다.')
    for part in name.split('/'):
        if (not part or part in {'.', '..'} or part.endswith(('.', ' '))
                or any(character in '<>:"|?*' for character in part)
                or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part)
                or part.lower() in {'runtime', 'updates', '.git', '.env', '__pycache__',
                                    'runtime.json', 'upgrade-drafts.json', 'upgrade-launch.lock'}):
            raise ValueError('업데이트 파일 경로를 확인하지 못했습니다.')
    return name


def _manifest(version, digest, files):
    _version(version)
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ValueError('앱 업데이트 파일 검증 정보를 확인하지 못했습니다.')
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('업데이트 파일 목록을 확인하지 못했습니다.')
    seen = set()
    for name, checksum in files.items():
        key = _relative(name).casefold()
        if key in seen or not isinstance(checksum, str) or not _HASH.fullmatch(checksum):
            raise ValueError('업데이트 파일 목록을 확인하지 못했습니다.')
        seen.add(key)
    if not {'deploy/Start-CompanyWorkspace.ps1', 'local_app/server.py'} <= files.keys():
        raise ValueError('앱 업데이트 시작 파일이 없습니다.')
    return {'schema': 1, 'version': version,
            'root': f'versions/{version}/Company-Workspace', 'sha256': digest, 'files': files}


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('중복된 앱 업데이트 정보를 사용하지 않습니다.')
        value[key] = item
    return value


def _read_manifest(path):
    with _safe(path).open('rb') as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('앱 업데이트 정보의 크기를 확인하지 못했습니다.')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'root', 'sha256', 'files'}
            or type(value['schema']) is not int or value['schema'] != 1):
        raise ValueError('앱 업데이트 정보의 형식을 확인하지 못했습니다.')
    expected = _manifest(value['version'], value['sha256'], value['files'])
    if value != expected:
        raise ValueError('앱 업데이트 실행 위치를 확인하지 못했습니다.')
    return value


def _digest(path):
    with _safe(path).open('rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= MAX_FILE_BYTES:
            raise ValueError('앱 업데이트 실행 파일의 크기를 확인하지 못했습니다.')
        digest, count = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_FILE_BYTES:
                raise ValueError('앱 업데이트 실행 파일이 너무 큽니다.')
            digest.update(chunk)
    return digest.hexdigest()


def _write_new(path, raw):
    path = _safe(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        for offset in range(0, len(raw), 1024 * 1024):
            stream.write(raw[offset:offset + 1024 * 1024])
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_manifest(path, value):
    raw = json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError('앱 업데이트 정보가 너무 큽니다.')
    temporary = _safe(path.parent / ('.manifest-' + uuid.uuid4().hex + '.tmp'))
    try:
        _write_new(temporary, raw)
        _safe(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            _safe(temporary).unlink()


def _discard_pending(path, expected):
    try:
        if _read_manifest(path) == expected:
            _safe(path).unlink()
    except (OSError, ValueError):
        pass


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError('앱 업데이트 실행이 취소되었습니다.')


def _package(raw):
    """Validate the complete archive before creating any extracted file."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries, seen, total = [], set(), 0
        if len(archive.infolist()) > MAX_FILES:
            raise ValueError('업데이트 파일이 너무 많습니다.')
        for item in archive.infolist():
            # Windows Compress-Archive historically emits backslash separators.
            # Normalize both spellings before duplicate/traversal validation.
            original = item.orig_filename.replace('\\', '/')
            normalized = item.filename.replace('\\', '/')
            if original != normalized or '\x00' in original:
                raise ValueError('업데이트 파일 이름을 확인하지 못했습니다.')
            is_directory = normalized.endswith('/')
            name = normalized[:-1] if is_directory else normalized
            _relative(name)
            if name != 'Company-Workspace' and not name.startswith('Company-Workspace/'):
                raise ValueError('업데이트 압축 파일의 루트가 다릅니다.')
            if name.casefold() in seen or item.flag_bits & 1:
                raise ValueError('중복되거나 암호화된 업데이트 파일입니다.')
            seen.add(name.casefold())
            mode = item.external_attr >> 16
            if (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or item.external_attr & 0x400):
                raise ValueError('연결된 업데이트 파일은 사용하지 않습니다.')
            if is_directory:
                continue
            if not name.startswith('Company-Workspace/'):
                raise ValueError('업데이트 압축 파일의 루트가 다릅니다.')
            total += item.file_size
            if item.file_size < 0 or total > MAX_PACKAGE_BYTES:
                raise ValueError('업데이트 파일이 너무 큽니다.')
            entries.append((item, name[len('Company-Workspace/'):]))
        files = {}
        for item, name in entries:
            with archive.open(item) as stream:
                data = stream.read(item.file_size + 1)
            if len(data) != item.file_size:
                raise ValueError('업데이트 파일 크기가 올바르지 않습니다.')
            files[name] = data
        return files


def _verify_files(directory, files):
    _safe(directory)
    total = 0
    for name, digest in files.items():
        path = _safe(directory / _relative(name))
        total += path.stat().st_size
        if total > MAX_PACKAGE_BYTES or _digest(path) != digest:
            raise ValueError('저장한 업데이트 파일을 검증하지 못했습니다.')
    expected, visited = {name.casefold() for name in files}, 0
    stack = [directory]
    while stack:
        for path in stack.pop().iterdir():
            visited += 1
            _safe(path)
            if visited > MAX_FILES * 2:
                raise ValueError('업데이트 파일이 너무 많습니다.')
            if path.is_dir():
                stack.append(path)
            elif path.relative_to(directory).as_posix().casefold() not in expected:
                raise ValueError('검증 목록에 없는 업데이트 파일이 있습니다.')


def _clean_staging(directory):
    # Only the fresh UUID staging tree created by this call, never user state.
    _safe(directory)
    if (not re.fullmatch(r'\.stage-[a-f0-9]{32}', directory.name)
            or directory.parent.name != 'versions' or directory.parent.parent.name != 'updates'):
        raise ValueError('업데이트 임시 저장 위치가 아닙니다.')
    if not directory.exists():
        return
    for path in sorted(directory.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        _safe(path)
        path.rmdir() if path.is_dir() else path.unlink()
    directory.rmdir()


def stage_and_launch(state, current_version, version, package_bytes, sha256, demo=False, *,
                     cancel=None, no_browser=False, launcher_bytes=None):
    """Stage immutable app files and start their same-user verified launcher.

    ``state`` is the effective runtime directory (including ``demo`` in demo
    mode). No current pointer changes until ``confirm_running_update`` succeeds.
    """
    _cancelled(cancel)
    if _version(version) <= _version(current_version):
        raise ValueError('현재 앱보다 새로운 버전만 설치할 수 있습니다.')
    if (not isinstance(package_bytes, bytes) or not 0 < len(package_bytes) <= MAX_PACKAGE_BYTES
            or hashlib.sha256(package_bytes).hexdigest() != sha256):
        raise ValueError('다운로드한 앱 업데이트 파일을 검증하지 못했습니다.')
    try:
        payload = _package(package_bytes)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise ValueError('앱 업데이트 압축 파일을 확인하지 못했습니다.') from exc
    value = _manifest(version, sha256, {name: hashlib.sha256(raw).hexdigest() for name, raw in payload.items()})
    with _LOCK:
        root = _root(state)
        current = root / 'current.json'
        if current.exists() and _version(_read_manifest(current)['version']) >= _version(version):
            raise ValueError('이미 설치한 앱과 같거나 이전 버전입니다.')
        versions = _safe(root / 'versions')
        versions.mkdir(exist_ok=True)
        directory = _safe(versions / version)
        application = _safe(root / value['root'])
        temporary = _safe(versions / ('.stage-' + uuid.uuid4().hex))
        if not directory.exists():
            temporary.mkdir()
            try:
                for name, raw in payload.items():
                    _cancelled(cancel)
                    staged = _safe(temporary / 'Company-Workspace' / name)
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    _write_new(staged, raw)
                _verify_files(temporary / 'Company-Workspace', value['files'])
                _safe(directory)
                try:
                    os.rename(temporary, directory)
                except OSError:
                    if not directory.is_dir():
                        raise
                    # A competing verified install may have won; never replace it.
            finally:
                if temporary.exists():
                    _clean_staging(temporary)
        _verify_files(application, value['files'])
        _cancelled(cancel)
        if launcher_bytes is not None:
            from .managed_launcher import stage
            from .update_source import load_source
            staged_source = load_source(application)
            if staged_source.error:
                raise ValueError('실행기 업데이트의 배포 위치를 확인하지 못했습니다.')
            stage(state, version, launcher_bytes, pending=True, cancel=cancel, source_identity=staged_source.identity)
        pending = root / 'pending.json'
        _atomic_manifest(pending, value)
        try:
            launch_root = _safe(Path(state).parent if demo else Path(state))
            arguments = [powershell_path(), '-NoLogo', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                         '-File', str(application / 'deploy/Start-CompanyWorkspace.ps1'),
                         '-StateRoot', str(launch_root), '-PythonCommand', sys.executable]
            if demo:
                arguments.append('-Demo')
            if no_browser:
                arguments.append('-NoBrowser')
            _cancelled(cancel)
            environment = os.environ.copy()
            environment['PYTHONDONTWRITEBYTECODE'] = '1'
            # Keep one no-console mode: adding DETACHED_PROCESS made Windows
            # PowerShell exit zero before executing -File in the native probe.
            process = subprocess.Popen(arguments, cwd=application, shell=False, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                                      | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)))
        except (OSError, ValueError, subprocess.SubprocessError):
            _discard_pending(pending, value)
            raise
    # Private process evidence is consumed by the update coordinator, never JSON.
    # Exit zero only acknowledges a detached handoff; it is not startup success.
    return {'status': 'launching', 'version': version, 'launched': True, '_process': process}


def confirm_running_update(state, current_version, *, application_root=None, demo=False):
    """Promote only a matching, reverified pending install after server readiness.

    Missing or other-version pending data returns False. An invalid pending
    install raises ValueError for a nonfatal startup warning; the previous
    successful launcher remains selected.
    """
    try:
        _version(current_version)
        with _LOCK:
            root = _safe(Path(state) / 'updates')
            pending = root / 'pending.json'
            if not pending.exists():
                return False
            value = _read_manifest(pending)
            if value['version'] != current_version:
                return False
            application = _safe(root / value['root'])
            running = _safe(Path(application_root) if application_root is not None else Path(__file__).parents[1])
            # An independently opened copy of the same release cannot confirm
            # that the staged application was actually used successfully.
            if running.resolve() != application.resolve():
                return False
            _verify_files(application, value['files'])
            current = root / 'current.json'
            if current.exists() and _version(_read_manifest(current)['version']) > _version(current_version):
                return False
            _atomic_manifest(current, value)
            from .managed_launcher import confirm_pending
            confirm_pending(state, current_version, demo=demo)
            _discard_pending(pending, value)
            return True
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
        raise ValueError('새 앱은 실행했지만 다음 실행에 사용할 업데이트 정보를 저장하지 못했습니다. 기존 실행 파일은 유지됩니다.') from exc
