"""Read-only, bounded previews of the current Claude configuration's sessions.

The JSONL format is internal, so this is deliberately not a transcript decoder
for the model. Claude itself resumes the original UUID; only plain user and
assistant text is mirrored into the UI. No tool payloads, authentication,
permissions, trust decisions or personal settings are imported.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .history import safe
from .windows_paths import workspace_path

MAX_PROJECTS = 1000
MAX_FILES = 5000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024
PREVIEW_BYTES = 256 * 1024
MAX_RECORDS = 50000
MAX_MESSAGES = 150
MAX_TEXT = 500000
_UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]')
_ANSI = re.compile(r'\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])')


class SessionImportError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def session_uuid(value):
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise SessionImportError('invalid_id', 'Claude 세션 ID는 UUID 형식으로 입력해 주세요.')
    return str(uuid.UUID(value))


def _text(value, limit=100000):
    return _CONTROL.sub('', _ANSI.sub('', value))[:limit] if isinstance(value, str) else ''


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OSError, OverflowError):
        return None


def _visible(record):
    role = record.get('type')
    if role not in ('user', 'assistant') or record.get('isMeta') is True:
        return None
    message = record.get('message')
    if not isinstance(message, dict) or message.get('role', role) != role:
        return None
    content = message.get('content')
    if isinstance(content, str):
        text = _text(content)
    elif isinstance(content, list):
        text = '\n'.join(_text(block.get('text')) for block in content
                         if isinstance(block, dict) and block.get('type') == 'text')
    else:
        return None
    # CLI-generated command/hook envelopes are not a human conversation turn.
    if role == 'user' and text.lstrip().startswith(('<local-command-', '<command-name>',
                                                  '<system-reminder>', '<task-notification>')):
        return None
    return {'role': role, 'text': text[:100000]} if text.strip() else None


class SessionImporter:
    """The caller supplies its resolved config root, never an HTTP path input.

    Importing grants no workspace trust and does not start a CLI. Use the stable
    ``id`` to deduplicate in the application's own store, then require its normal
    workspace confirmation before passing ``sessionId`` to bridge --resume.
    """
    def __init__(self, config_root, *, seconds=5.0):
        self.root = Path(config_root).expanduser().absolute()
        self.seconds = seconds

    def _projects(self, deadline):
        root = safe(self.root / 'projects')
        if not root.exists():
            return [], False
        if not root.is_dir():
            raise SessionImportError('invalid_store', '현재 Claude 설정의 세션 저장 위치를 확인해 주세요.')
        result, truncated = [], False
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_PROJECTS or time.monotonic() > deadline:
                    truncated = True
                    break
                try:
                    path = safe(Path(entry.path))
                    if entry.is_dir(follow_symlinks=False):
                        result.append(path)
                except (OSError, ValueError):
                    continue
        return result, truncated

    def _paths(self, deadline, sid=None):
        projects, truncated = self._projects(deadline)
        result = []
        checked = 0
        for project in projects:
            if time.monotonic() > deadline:
                truncated = True
                break
            # Exact-ID lookup does not need to enumerate every transcript.
            if sid is not None:
                candidates = [project / (sid + '.jsonl')]
            else:
                try:
                    with os.scandir(safe(project)) as entries:
                        candidates = []
                        for entry in entries:
                            checked += 1
                            if checked > MAX_FILES or time.monotonic() > deadline:
                                truncated = True
                                break
                            if _UUID.fullmatch(Path(entry.name).stem) and Path(entry.name).suffix == '.jsonl':
                                candidates.append(Path(entry.path))
                except (OSError, ValueError):
                    continue
            for path in candidates:
                try:
                    info = safe(path).stat()
                    if stat.S_ISREG(info.st_mode):
                        result.append((path, info))
                except (OSError, ValueError):
                    continue
            if checked > MAX_FILES:
                break
        return result, truncated

    def _records(self, path, sid, deadline, *, preview=False):
        before = safe(path).stat()
        if before.st_size > MAX_FILE_BYTES and not preview:
            raise SessionImportError('too_large', '세션 기록이 32 MiB 확인 범위를 넘습니다. 원본 Claude Code에서 이어가 주세요.')
        rows, skipped = [], False
        with safe(path).open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SessionImportError('changed', '세션 기록이 변경됐습니다. 다시 확인해 주세요.')
            if preview and before.st_size > 2 * PREVIEW_BYTES:
                # Parse complete lines only, never stitch two partial JSON rows.
                head = stream.read(PREVIEW_BYTES).rsplit(b'\n', 1)[0]
                stream.seek(-PREVIEW_BYTES, os.SEEK_END)
                tail = stream.read(PREVIEW_BYTES).split(b'\n', 1)[-1]
                lines = (head + b'\n' + tail).splitlines()
                skipped = True
            else:
                lines = iter(lambda: stream.readline(MAX_LINE_BYTES + 1), b'')
            total = 0
            for index, line in enumerate(lines):
                total += len(line)
                if time.monotonic() > deadline or index >= MAX_RECORDS or total > MAX_FILE_BYTES:
                    raise SessionImportError('read_limit', '세션 확인 범위를 넘었습니다. 원본 Claude Code에서 이어가 주세요.')
                if len(line) > MAX_LINE_BYTES:
                    raise SessionImportError('line_limit', '세션의 일부 기록이 확인 범위를 넘습니다. 원본 Claude Code에서 이어가 주세요.')
                try:
                    value = json.loads(line.decode('utf-8-sig'))
                except (ValueError, UnicodeDecodeError, RecursionError):
                    skipped = True
                    continue
                if not isinstance(value, dict) or value.get('isSidechain') is True:
                    continue
                # Never mix a sibling/agent session or metadata with no identity.
                if value.get('sessionId') != sid:
                    continue
                rows.append(value)
        after = safe(path).stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SessionImportError('changed', '세션 기록이 갱신 중입니다. 기존 Claude 작업을 마친 뒤 다시 확인해 주세요.')
        return rows, skipped, before

    def _read(self, path, sid, deadline, *, preview=False):
        records, truncated, info = self._records(path, sid, deadline, preview=preview)
        nodes = OrderedDict()
        leaf = None
        for record in records:
            key = record.get('uuid')
            if isinstance(key, str) and len(key) <= 160:
                nodes[key] = record
                if record.get('type') in ('user', 'assistant'):
                    leaf = key
        if leaf is None:
            raise SessionImportError('no_messages', '표시할 대화가 있는 Claude 세션을 확인하지 못했습니다.')
        chain, visited = [], set()
        while leaf and leaf in nodes and leaf not in visited:
            visited.add(leaf)
            node = nodes[leaf]
            chain.append(node)
            leaf = node.get('parentUuid')
            if leaf is not None and not isinstance(leaf, str):
                truncated = True
                break
        if leaf:
            truncated = True
        chain.reverse()
        cwd = next((r.get('cwd') for r in reversed(chain) if isinstance(r.get('cwd'), str)), None)
        if (not cwd or len(cwd) > 8192 or any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in cwd)
                or not Path(cwd).is_absolute() or cwd.startswith(('\\\\', '//'))):
            raise SessionImportError('workspace_unknown', '세션의 원래 작업 폴더를 확인하지 못했습니다. 원본 Claude Code에서 확인해 주세요.')
        workspace = Path(cwd).absolute()
        try:
            available = workspace_path(workspace).is_dir()
        except (OSError, ValueError):
            available = False
        messages, last_key = [], None
        for record in chain:
            visible = _visible(record)
            if visible is None:
                continue
            message_id = record.get('message', {}).get('id')
            key = (visible['role'], message_id) if visible['role'] == 'assistant' and isinstance(message_id, str) else None
            if key and key == last_key:
                previous, new = messages[-1]['text'], visible['text']
                if new.startswith(previous):
                    messages[-1]['text'] = new
                elif not previous.endswith(new):
                    messages[-1]['text'] = (previous + '\n' + new)[:100000]
            else:
                messages.append(visible)
            last_key = key
        if not messages:
            raise SessionImportError('no_visible_messages', '일반 대화 텍스트가 없는 세션입니다. 원본 Claude Code에서 확인해 주세요.')
        title = next((m['text'].strip().splitlines()[0][:120] for m in messages if m['role'] == 'user'), 'Claude 대화')
        stamps = [value for row in chain if (value := _stamp(row.get('timestamp'))) is not None]
        total = sum(len(m['text']) for m in messages)
        while len(messages) > MAX_MESSAGES or total > MAX_TEXT:
            total -= len(messages.pop(0)['text'])
            truncated = True
        warnings = []
        if truncated:
            warnings.append('화면에는 확인 가능한 대화 일부만 표시합니다. 실제 재개 기록은 Claude가 읽습니다.')
        if not available:
            warnings.append('원래 작업 폴더가 없거나 사용할 수 없습니다. 폴더를 복구한 뒤 이어가 주세요.')
        scope = os.path.normcase(str(self.root))
        result = {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'workspace-claude:' + scope + ':' + sid)),
                  'sessionId': sid, 'workspace': str(workspace), 'workspaceAvailable': available,
                  'title': title, 'created': min(stamps) if stamps else info.st_mtime,
                  'updated': max(stamps) if stamps else info.st_mtime,
                  'truncated': truncated, 'warnings': warnings, 'source': 'claude-code',
                  'activeStatus': 'unknown', 'trusted': False}
        if not preview:
            result['messages'] = messages
        return result

    def discover(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('세션 목록 개수는 1~100으로 지정해 주세요.')
        deadline = time.monotonic() + self.seconds
        paths, truncated = self._paths(deadline)
        counts = {}
        for path, _ in paths:
            counts[path.stem.lower()] = counts.get(path.stem.lower(), 0) + 1
        sessions, omitted = [], 0
        for path, _ in sorted(paths, key=lambda value: value[1].st_mtime, reverse=True):
            if len(sessions) >= limit or time.monotonic() > deadline:
                truncated = True
                break
            if counts[path.stem.lower()] > 1:
                omitted += 1
                continue
            try:
                sessions.append(self._read(path, session_uuid(path.stem), deadline, preview=True))
            except (OSError, ValueError):
                omitted += 1
        warnings = []
        if truncated:
            warnings.append('현재 설정 폴더의 세션 일부만 확인했습니다. 세션 ID로 직접 가져올 수 있습니다.')
        if omitted:
            warnings.append('중복·변경 중·지원하지 않는 기록은 목록에서 제외했습니다.')
        return {'sessions': sessions, 'truncated': truncated, 'warnings': warnings}

    def load(self, session_id):
        sid = session_uuid(session_id)
        deadline = time.monotonic() + self.seconds
        paths, truncated = self._paths(deadline, sid)
        if truncated:
            raise SessionImportError('scan_limit', '세션 위치 확인 범위를 넘었습니다. 다른 기록으로 대신 연결하지 않습니다.')
        if not paths:
            raise SessionImportError('not_found', '현재 Claude 설정 폴더에서 해당 세션을 찾지 못했습니다.')
        if len(paths) != 1:
            raise SessionImportError('ambiguous', '같은 세션 ID가 여러 프로젝트에 있습니다. 원본 Claude Code에서 중복 기록을 확인해 주세요.')
        return self._read(paths[0][0], sid, deadline)
