"""Bounded metadata suggestions for the current task, without running the CLI."""
from __future__ import annotations

from collections import deque
from collections import OrderedDict
import copy
import hashlib
import os
from pathlib import Path
import re
import stat
import time
import threading
import unicodedata

from .artifacts import DOCUMENT_TYPES, EXCLUDED
from .windows_paths import redirects_path, workspace_path

MAX_ATTACHMENTS = 12
MAX_SAVED_ATTACHMENTS = 200
MAX_COMMANDS = 1000
# Referencing source text is separate from opening a result in an external app.
# This list only permits metadata suggestions and does not change that app's
# document/executable opening policy.
REFERENCE_FILE_TYPES = frozenset(DOCUMENT_TYPES) | frozenset({
    '.py', '.pyi', '.pyw', '.js', '.mjs', '.cjs', '.ts', '.mts', '.cts',
    '.tsx', '.jsx', '.css', '.scss', '.sass', '.less', '.json', '.jsonc',
    '.yaml', '.yml', '.toml', '.xml', '.sql', '.sh', '.bash', '.zsh',
    '.ps1', '.psm1', '.psd1', '.bat', '.cmd', '.cs', '.java', '.go',
    '.rs', '.c', '.h', '.cc', '.cpp', '.cxx', '.hpp', '.hxx', '.ini',
    '.cfg', '.conf', '.properties', '.log', '.ipynb', '.r', '.rmd',
    '.rb', '.php', '.vue', '.svelte', '.swift', '.kt', '.kts', '.dart',
    '.lua', '.pl', '.ex', '.exs', '.fs', '.fsx', '.vb', '.vbs', '.tex',
    '.rst', '.adoc', '.graphql', '.gql', '.proto',
})
_COMMAND = re.compile(r'[\w][\w.:-]{0,199}', re.UNICODE)
_WORDS = re.compile(r'[\W_]+', re.UNICODE)
_COMMAND_DESCRIPTION = '현재 업무 연결에서 보고한 명령'
_AMBIGUOUS_COMMAND_DESCRIPTION = _COMMAND_DESCRIPTION + ' · 같은 호출명은 하나로 표시'
# These entries explain only known terminal/dialog commands if a CLI version
# reports them. Never classify every built-in as terminal-only: the SDK exposes
# /clear, /compact, /context, /usage and argument forms of other commands, and
# the live runtime list remains the source of available invocations. Checked:
# https://code.claude.com/docs/en/agent-sdk/slash-commands
# https://code.claude.com/docs/en/headless and /docs/en/commands (2026-09-29).
_TERMINAL_COMMANDS = {
    'login': '로그인은 기존 Claude 터미널에서 진행해 주세요.',
    'logout': '로그아웃은 기존 Claude 터미널에서 진행해 주세요.',
    'permissions': '이번 연결의 승인 방식은 상단 승인 모드에서 선택할 수 있습니다.',
    'allowed-tools': '이번 연결의 승인 방식은 상단 승인 모드에서 선택할 수 있습니다.',
    'theme': '이 앱의 화면은 Workspace 디자인을 사용합니다.',
    'terminal-setup': '앱에서는 Shift+Enter로 줄을 바꿀 수 있습니다.',
    'keybindings': '터미널 키 설정은 기존 Claude 터미널에서 변경해 주세요.',
}


class CompletionDiscovery:
    """Short-lived, bounded metadata cache; never starts Claude or a plugin.

    A separate key for each exact folder prevents an earlier task's project
    skills from leaking into the home composer. The lock coalesces overlapping
    keystrokes while an uncached inventory is being read.
    """
    def __init__(self, client, *, ttl=5., max_contexts=8):
        self.client, self.ttl, self.max_contexts = client, ttl, max_contexts
        self._cache, self._lock = OrderedDict(), threading.Lock()

    def inventory(self, workspace=None):
        key = os.path.normcase(str(workspace)) if workspace else None
        with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.ttl:
                self._cache.move_to_end(key)
                return copy.deepcopy(cached[1])
            try:
                snapshot = self.client.completion_inventory(workspace)
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get('skills'), list):
                    raise ValueError('Invalid completion inventory')
            except (ValueError, OSError, UnicodeError, TypeError):
                snapshot = {'skills': [], 'skillsLimited': True}
            self._cache[key] = (time.monotonic(), snapshot)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_contexts:
                self._cache.popitem(last=False)
            return copy.deepcopy(snapshot)


def _text(value, limit):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return None
    try:
        value.encode('utf-8')
    except UnicodeError:
        return None
    return value


def _identity(kind, value):
    return kind + '-' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def _fold(value):
    # Match composed and decomposed Korean text alike, without changing the
    # exact path or invocation returned to the caller.
    return unicodedata.normalize('NFC', value.replace('\\', '/')).casefold()


def _match_rank(row, query):
    label = _fold(row['label'])
    slash = 'invocation' in row
    value = label.removeprefix('/') if slash else label
    name = value.rsplit(':' if slash else '/', 1)[-1]
    if not query or value == query or (not slash and name == query):
        return 0
    if value.startswith(query) or name.startswith(query):
        return 1
    if any(word.startswith(query) for word in _WORDS.split(value) if word):
        return 2
    if query in value:
        return 3
    # Search descriptions only after invocation matches. Generic UI copy and
    # conflicting descriptions are not command metadata to search against.
    description = row['description']
    if (slash and description not in {_COMMAND_DESCRIPTION, _AMBIGUOUS_COMMAND_DESCRIPTION}
            and query in _fold(description)):
        return 4
    return None


def _rank(row, query):
    label = _fold(row['label'])
    return (_match_rank(row, query), label, row['id'])


def _absolute(value):
    if not _text(value, 32767):
        raise ValueError('파일의 전체 경로를 확인해 주세요.')
    candidate = Path(value)
    if not candidate.is_absolute() or '..' in candidate.parts:
        raise ValueError('파일의 전체 경로를 확인해 주세요.')
    # Reject redirection before resolve can erase evidence of a junction/link.
    workspace_path(candidate)
    return candidate.resolve(strict=True)


def _file_row(path, root):
    if path.is_relative_to(root):
        label = path.relative_to(root).as_posix()
        description = '업무 폴더의 파일'
    else:
        label = path.name
        description = '직접 선택한 파일 · ' + str(path.parent)
    return {'id': _identity('file', os.path.normcase(str(path))), 'label': label,
            'description': description, 'path': str(path), 'supported': True}


def _files(item, request, query, *, safe_suffixes, max_items, max_entries,
           max_directories, max_depth, deadline):
    root = _absolute(item.get('workspace'))
    if not root.is_dir():
        raise ValueError('현재 업무 폴더를 확인해 주세요.')
    suffixes = frozenset(safe_suffixes) & REFERENCE_FILE_TYPES
    rows, limited = {}, False

    def add(path, info):
        if (not stat.S_ISREG(info.st_mode) or redirects_path(info)
                or path.suffix.lower() not in suffixes or not _text(str(path), 32767)):
            return
        row = _file_row(path, root)
        if _match_rank(row, query) is not None:
            rows[row['id']] = row

    # Explicit attachments may be outside the workspace, but their siblings are
    # never enumerated. Prefer this small set before spending the scan budget.
    saved = item.get('attachments', [])
    if not isinstance(saved, list):
        saved, limited = [], True
    if len(saved) > MAX_SAVED_ATTACHMENTS:
        limited = True
    selected = request.get('attachments', []) + saved[-MAX_SAVED_ATTACHMENTS:]
    seen = set()
    for value in selected:
        if time.monotonic() >= deadline:
            limited = True
            break
        if not isinstance(value, str) or value in seen:
            continue
        seen.add(value)
        try:
            path = _absolute(value)
            add(path, path.lstat())
        except (OSError, ValueError, RuntimeError):
            limited = True  # Deleted or no longer safely accessible.

    pending = deque([(root, 0)])
    directories, entries = 1, 0
    while pending:
        if time.monotonic() >= deadline:
            limited = True
            break
        directory, depth = pending.popleft()
        try:
            # Recheck a queued directory before enumerating it. File selection
            # and send independently revalidate paths; this reads no contents.
            if _absolute(str(directory)) != directory:
                limited = True
                continue
            with os.scandir(directory) as children:
                for child in children:
                    if entries >= max_entries or time.monotonic() >= deadline:
                        return _finish(rows.values(), query, max_items, True)
                    entries += 1
                    if child.name.startswith('.') or child.name.casefold() in EXCLUDED:
                        continue
                    try:
                        info = child.stat(follow_symlinks=False)
                        if redirects_path(info):
                            continue
                        path = Path(child.path)
                        if stat.S_ISDIR(info.st_mode):
                            if depth < max_depth and directories < max_directories:
                                pending.append((path, depth + 1))
                                directories += 1
                            else:
                                limited = True
                        elif (stat.S_ISREG(info.st_mode) and path.suffix.lower() in suffixes
                              and path.resolve(strict=True).parent == directory):
                            add(path, info)
                    except (OSError, ValueError, RuntimeError):
                        limited = True
        except (OSError, ValueError, RuntimeError):
            limited = True
    return _finish(rows.values(), query, max_items, limited)


def _finish(rows, query, max_items, limited):
    ordered = sorted(rows, key=lambda row: _rank(row, query))
    return {'items': ordered[:max_items], 'limited': limited or len(ordered) > max_items}


def _slash(item, query, max_items, deadline):
    bridge, connection = item.get('bridge'), item.get('connection')
    if (bridge is None or getattr(bridge, 'closed', True)
            or not getattr(bridge, 'process', None) or bridge.process.poll() is not None):
        return {'items': [], 'limited': True, 'connectRequired': True}
    if not isinstance(connection, dict):
        return {'items': [], 'limited': True}
    reported = connection.get('reported')
    raw = connection.get('slashCommands')
    if (not isinstance(reported, dict) or reported.get('commands') is not True
            or not isinstance(raw, list)):
        return {'items': [], 'limited': True}
    rows, limited = {}, len(raw) > MAX_COMMANDS
    for value in raw[:MAX_COMMANDS]:
        if time.monotonic() >= deadline:
            limited = True
            break
        entry = {'name': value} if isinstance(value, str) else value
        if not isinstance(entry, dict):
            limited = True
            continue
        name = _text(entry.get('name'), 201)
        name = name.removeprefix('/') if name else ''
        if not _COMMAND.fullmatch(name):
            limited = True
            continue
        invocation = '/' + name
        # The exact runtime name is the invocation. Arbitrary invocation fields
        # or descriptions cannot inject arguments or another command.
        reason = _TERMINAL_COMMANDS.get(name.casefold())
        description = _text(entry.get('description', ''), 800) or _COMMAND_DESCRIPTION
        row = {'id': _identity('slash', invocation), 'label': invocation,
               'description': description, 'invocation': invocation,
               'supported': reason is None, 'source': 'runtime',
               'scope': 'folder', 'availability': 'reported'}
        if reason:
            row['reason'] = '이 앱의 대화 입력에서는 지원하지 않습니다. ' + reason
        old = rows.get(invocation)
        if old and old['description'] != description:
            # A single CLI invocation cannot choose between identically named
            # sources. Do not present an arbitrary source as the selected one.
            row['description'] = _AMBIGUOUS_COMMAND_DESCRIPTION
        rows[invocation] = row
    # Inspect the bounded runtime list before trimming. A later exact match
    # must not be crowded out by the first matching descriptions or names.
    matches = (row for row in rows.values() if _match_rank(row, query) is not None)
    return _finish(matches, query, max_items, limited)


def _discovered_slash(snapshot, query, max_items, *, workspace=None):
    """Installed names are insertion candidates, not runtime availability claims."""
    rows = {}
    raw = snapshot.get('skills', [])
    limited = snapshot.get('skillsLimited') is True or len(raw) > MAX_COMMANDS
    for entry in raw[:MAX_COMMANDS]:
        if not isinstance(entry, dict) or entry.get('userInvocable') is not True:
            continue
        name = _text(entry.get('invocation'), 201)
        name = name.removeprefix('/') if name else ''
        if not _COMMAND.fullmatch(name):
            continue  # Internal/library references cannot invent slash names.
        storage = entry.get('storageScope')
        if storage not in {'personal', 'company', 'project'} or (not workspace and storage == 'project'):
            continue
        invocation = '/' + name
        row = {'id': _identity('slash', invocation), 'label': invocation,
               'description': _text(entry.get('description', ''), 800) or '설치 메타데이터에서 발견한 항목',
               'invocation': invocation, 'supported': True, 'source': 'installed',
               'scope': 'folder' if storage == 'project' else 'common',
               'availability': 'discovered'}
        if invocation in rows:
            row['description'] = '같은 호출명의 설치 항목이 있습니다. 실제 연결에서 적용되는 항목을 확인합니다.'
            row['scope'] = 'folder' if workspace else 'common'
        rows[invocation] = row
    matches = (row for row in rows.values() if _match_rank(row, query) is not None)
    return {**_finish(matches, query, max_items, limited), 'discovery': True}


def _session_controls(result, connection, query, max_items):
    """Expose the app's session control only when this connection supports it."""
    capabilities = connection.get('capabilities')
    if not isinstance(capabilities, dict) or capabilities.get('setEffort') is not True:
        return result
    row = {'id': _identity('slash', '/effort'), 'label': '/effort', 'invocation': '/effort',
           'description': '현재 연결의 Effort 선택 · /effort high처럼 입력 · auto는 기존 설정 상속',
           'supported': True, 'source': 'workspace', 'scope': 'session', 'availability': 'local'}
    rows = [item for item in result['items'] if item.get('invocation') != '/effort']
    if _match_rank(row, query) is not None:
        rows.append(row)
    return {**result, **_finish(rows, query, max_items, result.get('limited', False))}


def complete(item, request, *, safe_suffixes=REFERENCE_FILE_TYPES, max_items=40,
             max_entries=3000, max_directories=128, max_depth=4, seconds=.1,
             discovery=None):
    """Return metadata from a validated task snapshot, without a persistent cache.

    The server owns authentication and task/workspace validation. Deadline checks
    bound traversal between filesystem operations; they cannot interrupt an OS
    filesystem call. A supplied discovery provider reads only bounded metadata
    when runtime commands have not been reported. It never starts a subprocess.
    """
    if not isinstance(item, dict) or not isinstance(request, dict):
        raise ValueError('추천할 업무와 입력 내용을 확인해 주세요.')
    kind = request.get('kind')
    if kind not in {'file', 'slash', 'command'}:
        raise ValueError('파일 또는 명령 추천을 선택해 주세요.')
    query = _text(request.get('query', ''), 512)
    if query is None:
        raise ValueError('검색어는 줄바꿈 없이 512자 이내로 입력해 주세요.')
    attachments = request.get('attachments', [])
    if (not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS
            or any(not _text(value, 32767) for value in attachments)):
        raise ValueError('파일은 전체 경로로 한 번에 12개까지 선택할 수 있습니다.')
    query = _fold(query.strip().removeprefix('@' if kind == 'file' else '/'))
    max_items = max(1, min(40, int(max_items)))
    deadline = time.monotonic() + max(0., min(.5, float(seconds)))
    if kind in {'slash', 'command'}:
        result = _slash(item, query, max_items, deadline)
        bridge = item.get('bridge')
        live = (bridge is not None and not getattr(bridge, 'closed', True)
                and getattr(bridge, 'process', None) and bridge.process.poll() is None)
        connection = item.get('connection')
        connection = connection if isinstance(connection, dict) else {}
        reported = connection.get('reported')
        reported = reported if isinstance(reported, dict) else {}
        authoritative = (live and reported.get('commands') is True
                         and isinstance(connection.get('slashCommands'), list))
        if discovery is not None and not authoritative:
            workspace = item.get('workspace')
            fallback = _discovered_slash(discovery.inventory(workspace), query, max_items,
                                         workspace=workspace)
            if result.get('connectRequired'):
                fallback['connectRequired'] = True
            return _session_controls(fallback, connection, query, max_items) if live else fallback
        return _session_controls(result, connection, query, max_items) if live else result
    # Query text is only a filter, never a path to traverse. In particular, no
    # '..', absolute path, wildcard, or catalog folder widens the task scope.
    return _files(item, request, query, safe_suffixes=safe_suffixes,
                  max_items=max_items, max_entries=max(1, min(3000, int(max_entries))),
                  max_directories=max(1, min(128, int(max_directories))),
                  max_depth=max(0, min(4, int(max_depth))), deadline=deadline)
