"""Offline release descriptions carried with each source ZIP.

Bundled entries are maintained during release preparation, not fetched from
GitHub at publisher startup. Local commit subjects are an explicitly labelled
fallback for older Git checkouts; they are not GitHub Release descriptions.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from .config import PublisherError, atomic_json, checked_version, safe_path


RELEASE_FILE = 'deploy/workspace-release-notes.json'
MAX_DATA = 256 * 1024
MAX_HISTORY = 30
MAX_TITLE = 300
MAX_NOTES = 32768
MAX_COMMITS = 20
MAX_GIT_OUTPUT = 64 * 1024


def _version_key(version):
    return tuple(int(part) for part in checked_version(version).split('.'))


def _text(value, limit, *, required=False):
    if (not isinstance(value, str) or len(value.encode('utf-8')) > limit
            or any(ord(char) < 32 and char not in '\n\r\t' for char in value)
            or '\x7f' in value or required and not value.strip()):
        raise ValueError('invalid release text')
    return value


def _unique_object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError('duplicate JSON field')
        value[name] = item
    return value


def _validated(document, version):
    if (not isinstance(document, dict)
            or set(document) != {'schema', 'currentVersion', 'releases'}
            or type(document['schema']) is not int or document['schema'] != 1
            or document['currentVersion'] != version
            or not isinstance(document['releases'], list)
            or not 1 <= len(document['releases']) <= MAX_HISTORY):
        raise ValueError('invalid release document')
    entries, seen = [], set()
    for item in document['releases']:
        if not isinstance(item, dict) or set(item) != {'version', 'title', 'notes'}:
            raise ValueError('invalid release entry')
        entry_version = checked_version(item['version'])
        if entry_version in seen or _version_key(entry_version) > _version_key(version):
            raise ValueError('duplicate or future release')
        seen.add(entry_version)
        entries.append({'version': entry_version,
                        'title': _text(item['title'], MAX_TITLE, required=True).strip(),
                        'notes': _text(item['notes'], MAX_NOTES, required=True).strip()})
    if version not in seen:
        raise ValueError('current release is missing')
    return sorted(entries, key=lambda item: _version_key(item['version']), reverse=True)


def _read_document(path):
    # A bounded read also handles a file growing after an initial size check.
    with safe_path(path).open('rb') as stream:
        raw = stream.read(MAX_DATA + 1)
    if not raw or len(raw) > MAX_DATA:
        raise ValueError('release data size')
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)


def _git_notes(repo_root):
    marker = repo_root / '.git'
    # An extracted ZIP may be inside an unrelated Git checkout. Never read its
    # parent's history or try to restore Git metadata for the ZIP.
    if not marker.exists():
        return ''
    safe_path(marker)
    try:
        # No author, email, commit body, diff, remote URL or network request.
        # The file-backed capture and bounded read keep memory use bounded even
        # when a local commit subject is unexpectedly large.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ['git', '--no-pager', 'log', '--no-show-signature',
                 '--format=%<(512,trunc)%s', f'--max-count={MAX_COMMITS}', 'HEAD', '--'],
                cwd=repo_root, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode:
                return ''
            output.seek(0)
            raw = output.read(MAX_GIT_OUTPUT + 1)
        if len(raw) > MAX_GIT_OUTPUT:
            return ''
        subjects = []
        for line in raw.decode('utf-8', errors='replace').splitlines()[:MAX_COMMITS]:
            subject = ''.join(char for char in line if ord(char) >= 32 and ord(char) != 127).strip()
            if subject:
                subjects.append('- ' + subject[:512])
        if not subjects:
            return ''
        notes = '최근 개발 커밋 (정식 릴리스 설명 아님)\n' + '\n'.join(subjects)
        return _text(notes, MAX_NOTES)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ''


def load_notes(repo_root, version):
    """Return current defaults and newest-first history without network access.

    A corrupt or version-mismatched bundled file raises PublisherError instead
    of silently showing an older release as the current one. Older source ZIPs
    with no bundled file return empty notes and history. An older Git checkout
    can instead return labelled local development commit subjects.
    """
    version = checked_version(version)
    repo_root = safe_path(Path(repo_root).absolute())
    path = safe_path(repo_root / RELEASE_FILE)
    if path.exists():
        try:
            entries = _validated(_read_document(path), version)
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise PublisherError('소스에 포함된 릴리스 기록을 읽지 못했거나 현재 앱 버전과 다릅니다. 해당 버전의 소스 ZIP을 다시 확인해 주세요.') from exc
        return {**entries[0], 'history': entries, 'source': 'bundled'}
    title = f'AX Workspace {version}'
    notes = _git_notes(repo_root)
    current = {'version': version, 'title': title, 'notes': notes}
    return {**current, 'history': [current] if notes else [], 'source': 'git' if notes else 'empty'}


def record_release(repo_root, version, title, notes):
    """Maintain the tracked source record during external release preparation.

    Notes are explicitly supplied by the release author. This never promotes
    local commit subjects to an official release description automatically.
    """
    version = checked_version(version)
    repo_root = safe_path(Path(repo_root).absolute())
    path = safe_path(repo_root / RELEASE_FILE)
    try:
        previous = []
        if path.exists():
            document = _read_document(path)
            previous = _validated(document, checked_version(document['currentVersion']))
            if _version_key(version) < _version_key(document['currentVersion']):
                raise ValueError('cannot move current release backwards')
        entry = {'version': version, 'title': title, 'notes': notes}
        entries = [entry, *(item for item in previous if item['version'] != version)][:MAX_HISTORY]
        document = {'schema': 1, 'currentVersion': version, 'releases': entries}
        document['releases'] = _validated(document, version)
        if len(json.dumps(document, ensure_ascii=False, indent=2).encode('utf-8')) + 1 > MAX_DATA:
            raise ValueError('release data size')
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise PublisherError('릴리스 기록의 버전·제목·변경 내용 또는 파일 형식을 확인해 주세요. 이전 버전으로 기록을 되돌릴 수 없습니다.') from exc
    atomic_json(path, document)
    return path
