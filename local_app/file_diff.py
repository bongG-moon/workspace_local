"""Finite, app-owned text captures for review; never restore or alter originals.

The caller must enforce session/workspace access and capture only after its
workspace trust decision. Reviewing previously captured app-owned records does
not execute workspace settings. This module adds path checks and treats partial
observations as unknown, not evidence a file was created or removed by Claude.
"""
from __future__ import annotations

import difflib
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import time
import uuid

from .file_preview import CODE_LANGUAGES


CODE_REVIEW_TYPES = frozenset(CODE_LANGUAGES) | {'.md', '.bat', '.cmd', '.ini', '.cfg', '.svg'}
TEXT_TYPES = CODE_REVIEW_TYPES | {'.txt', '.csv', '.tsv', '.html', '.htm', '.log'}
METADATA_TYPES = frozenset({'.pdf', '.pptx', '.docx', '.xlsx', '.xls', '.ppt', '.doc',
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.zip'})
EXCLUDED = frozenset({'node_modules', 'venv', '__pycache__', 'build', 'dist'})
SECRET_NAME = re.compile(r'(^|[._-])(secret|secrets|credential|credentials|password|passwords|'
                         r'token|tokens|auth|apikey|api_key|private)([._-]|$)', re.I)
RUN_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
STORE_NAME = re.compile(r'[a-f0-9]{64}\.json\Z')
TEMP_NAME = re.compile(r'\.capture-[a-f0-9]{32}\.tmp\Z')
HUNK_HEADER = re.compile(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\n\Z')
SOURCE_LINE = re.compile(r'[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z')
DIFF_TOKEN = re.compile(r'\w+|\s+|[^\w\s]')
MAX_RUN_BYTES = 4 * 1024 * 1024
_STORE_LOCK = threading.RLock()


def _redirected(info):
    # Do not hydrate Cloud Files or follow any other reparse tag during capture.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _safe_path(path: Path, *, directory=False):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('실제 폴더의 절대 경로가 필요합니다.')
    for part in (path, *path.parents):
        if _redirected(part.lstat()):
            raise ValueError('연결된 경로는 변경 비교에서 제외합니다.')
    if directory and not path.is_dir():
        raise ValueError('업무 폴더를 확인할 수 없습니다.')
    return path


def _hidden_or_secret(name):
    lowered = name.casefold()
    return (lowered.startswith('.') or lowered in EXCLUDED or bool(SECRET_NAME.search(lowered))
            or lowered.startswith(('id_rsa', 'id_ed25519'))
            or Path(name).suffix.lower() in {'.pem', '.key', '.pfx', '.p12'})


def _file_id(name):
    return hashlib.sha256(name.encode('utf-8')).hexdigest()[:24]


def _signature(info):
    return [info.st_mtime_ns, info.st_size]


def _replacement_candidates(removed, added, budget):
    """Prefer shared words and numbers over common indentation or punctuation."""
    tokens = [[list(DIFF_TOKEN.finditer(row['text'])) if len(row['text']) <= 512 else []
               for row in rows] for rows in (removed, added)]
    before, after = tokens
    candidates = []
    for i, old in enumerate(before):
        for j, new in enumerate(after):
            work = len(old) * len(new)
            if not work or work > min(4096, budget):
                continue
            budget -= work
            matcher = difflib.SequenceMatcher(None, [m[0] for m in old], [m[0] for m in new], autojunk=False)
            blocks = matcher.get_matching_blocks()
            # A few shared code delimiters don't establish that two lines correspond.
            shared = sum(len(token[0]) for start, _, count in blocks for token in old[start:start+count]
                         if token[0][0].isalnum() or token[0][0] == '_')
            total = sum(len(token[0]) for line in (old, new) for token in line
                        if token[0][0].isalnum() or token[0][0] == '_')
            score = 2 * shared / max(1, total)
            if score >= .36:
                candidates.append((score, i, j, matcher.get_opcodes()))
    return candidates, before, after, budget


def _mark_inline_edits(patch):
    """On-demand comparison. Span offsets count Unicode code points in the stored text."""
    budget = 120_000
    for hunk in patch['hunks']:
        for edited, group in groupby(hunk['rows'], key=lambda row: row['kind'] != 'context'):
            if not edited:
                continue
            changed = list(group)
            removed = [row for row in changed if row['kind'] == 'remove']
            added = [row for row in changed if row['kind'] == 'add']
            if not removed or not added or len(removed) * len(added) > 400 or budget == 0:
                continue
            candidates, before, after, budget = _replacement_candidates(removed, added, budget)
            # Anchor the closest matches first, without pairing a line twice or crossing an anchor.
            anchors = []
            for _, i, j, ops in sorted(candidates, key=lambda pair: (-pair[0], abs(pair[1]-pair[2]))):
                if any((i-old) * (j-new) <= 0 for old, new in anchors):
                    continue
                anchors.append((i, j))
                added[j]['pairedBeforeLine'] = removed[i]['beforeLine']
                for op, a, b, c, d in ops:
                    if op == 'equal':
                        continue
                    if a < b:
                        removed[i].setdefault('highlights', []).append([before[i][a].start(), before[i][b-1].end()])
                    if c < d:
                        added[j].setdefault('highlights', []).append([after[j][c].start(), after[j][d-1].end()])


def _decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16'), 'utf-16'
    if b'\0' in raw:
        raise UnicodeError('binary')
    return raw.decode('utf-8-sig'), 'utf-8'


def _open_source(path):
    if os.name != 'nt':
        return os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    # Windows' os.open has no O_NOFOLLOW. Open the reparse point itself rather
    # than a newly substituted target, then validate the descriptor before read.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 7, None, 3, 0x00200080, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise


class FileDiffStore:
    """Capture only at run boundaries, and decode/diff only finite text files.

    Up to eight runs (including unfinished ones) share the disk budget. Old
    completed runs expire first. No text cache, watcher, Git call, or background
    worker is kept. Calling finish after an expired start reports unavailable.
    """
    def __init__(self, directory: Path, *, max_runs=8, max_files=200,
                 max_entries=2000, max_directories=30, seconds=.5,
                 file_bytes=128 * 1024, capture_bytes=512 * 1024,
                 diff_lines=500, diff_bytes=64 * 1024, diff_input_lines=2000,
                 diff_work=250000):
        self.directory = Path(directory).absolute()
        self.max_runs = max(1, min(int(max_runs), 8))
        self.max_files = max(1, min(int(max_files), 200))
        self.max_entries = max(1, min(int(max_entries), 2000))
        self.max_directories = max(0, min(int(max_directories), 30))
        self.seconds = max(.001, min(float(seconds), .5))
        self.file_bytes = max(1, min(int(file_bytes), 128 * 1024))
        self.capture_bytes = max(1, min(int(capture_bytes), 512 * 1024))
        self.diff_lines = max(1, min(int(diff_lines), 500))
        self.diff_bytes = max(128, min(int(diff_bytes), 64 * 1024))
        self.diff_input_lines = max(1, min(int(diff_input_lines), 2000))
        self.diff_work = max(1, min(int(diff_work), 250000))
        # The local app has one server process. Sharing a finite global lock
        # also makes independently constructed stores safe within that process.
        self.lock = _STORE_LOCK

    def limits(self):
        return {'scope': 'workspace-top-two-levels', 'maxRuns': self.max_runs,
                'maxFiles': self.max_files, 'fileBytes': self.file_bytes,
                'captureBytes': self.capture_bytes, 'maxRunBytes': MAX_RUN_BYTES,
                'captureSeconds': self.seconds, 'diffLines': self.diff_lines,
                'diffBytes': self.diff_bytes, 'diffInputLines': self.diff_input_lines,
                'diffWork': self.diff_work, 'originalsModified': False}

    def _ensure_store(self):
        # Validate ancestry before mkdir as well as after it. Never write through
        # a pre-existing junction or link in the application's state directory.
        for part in (self.directory, *self.directory.parents):
            try:
                if _redirected(part.lstat()):
                    raise ValueError('변경 비교 저장 위치를 확인할 수 없습니다.')
            except FileNotFoundError:
                pass
        self.directory.mkdir(parents=True, exist_ok=True)
        _safe_path(self.directory, directory=True)
        # An interrupted atomic write must not accumulate extra retained copies.
        # Only this module's unguessable temporary filenames are eligible; never
        # recurse or remove a link, directory, or arbitrary state file.
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries):
                if count >= 256:
                    raise ValueError('변경 비교 저장 폴더가 제한을 초과했습니다.')
                if TEMP_NAME.fullmatch(entry.name):
                    info = entry.stat(follow_symlinks=False)
                    if not _redirected(info) and stat.S_ISREG(info.st_mode):
                        Path(entry.path).unlink()

    def _target(self, root, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError('변경 비교 실행 식별자가 올바르지 않습니다.')
        key = hashlib.sha256((str(root) + '\0' + run_id).encode('utf-8')).hexdigest()
        return self.directory / (key + '.json')

    def _read(self, path):
        info = path.lstat()
        if _redirected(info) or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RUN_BYTES:
            raise ValueError('변경 비교 기록을 확인할 수 없습니다.')
        with path.open('rb') as handle:
            raw = handle.read(MAX_RUN_BYTES + 1)
        if len(raw) > MAX_RUN_BYTES:
            raise ValueError('변경 비교 기록이 제한을 초과했습니다.')
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict) or data.get('version') not in {1, 2}:
            raise ValueError('변경 비교 기록의 형식이 올바르지 않습니다.')
        observed_fields = {'observedCandidates', 'primaryNames', 'observedLimited'}
        if ((data['version'] == 1 and observed_fields.intersection(data))
                or (data['version'] == 2 and not observed_fields.issubset(data))):
            raise ValueError('명령 실행 변경 기록의 형식이 올바르지 않습니다.')
        if not isinstance(data.get('workspace'), str) or not RUN_ID.fullmatch(str(data.get('runId', ''))):
            raise ValueError('변경 비교 기록의 식별자가 올바르지 않습니다.')
        for capture in (data.get('before'), data.get('after')):
            if capture is None:
                continue
            if not isinstance(capture, dict) or not isinstance(capture.get('files'), dict):
                raise ValueError('변경 비교 기록의 형식이 올바르지 않습니다.')
            if len(capture['files']) > 200:
                raise ValueError('변경 비교 기록이 제한을 초과했습니다.')
            for name, row in capture['files'].items():
                parts = PurePosixPath(name).parts
                if (not isinstance(row, dict) or not 1 <= len(parts) <= 2
                        or PurePosixPath(name).is_absolute() or any(p in {'.', '..'} for p in parts)
                        or '\\' in name or ':' in name or row.get('name') != name
                        or not isinstance(row.get('text', ''), str)):
                    raise ValueError('변경 비교 파일 기록의 형식이 올바르지 않습니다.')
            for key in ('complete', 'directories'):
                if not isinstance(capture.get(key), list) or len(capture[key]) > 2001:
                    raise ValueError('변경 비교 폴더 기록의 형식이 올바르지 않습니다.')
        if not isinstance(data.get('before'), dict):
            raise ValueError('변경 전 기록을 확인할 수 없습니다.')
        if 'observedCandidates' in data:
            candidates = data['observedCandidates']
            primary = data.get('primaryNames')
            if (not isinstance(candidates, dict) or len(candidates) > 200
                    or not isinstance(primary, list) or len(primary) > 400
                    or not all(isinstance(name, str) for name in primary)):
                raise ValueError('명령 실행 변경 기록의 형식이 올바르지 않습니다.')
            names = data['before']['files'].keys() | (data.get('after') or {}).get('files', {}).keys()
            for file_id, row in candidates.items():
                if (not isinstance(row, dict) or row.get('name') not in names
                        or file_id != _file_id(row['name'])
                        or row.get('decision') not in {'pending', 'include', 'dismiss'}
                        or not isinstance(row.get('conflict'), bool)):
                    raise ValueError('명령 실행 변경 기록의 형식이 올바르지 않습니다.')
        return data

    def _entries(self):
        # Only this module's fixed filenames are considered. Bounded reads keep
        # a damaged state folder from becoming an unbounded memory operation.
        rows = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries):
                if count >= 256:
                    raise ValueError('변경 비교 저장 폴더가 제한을 초과했습니다.')
                if not STORE_NAME.fullmatch(entry.name):
                    continue
                info = entry.stat(follow_symlinks=False)
                if not _redirected(info) and stat.S_ISREG(info.st_mode):
                    rows.append((Path(entry.path), info.st_mtime_ns))
        return sorted(rows, key=lambda row: row[1])

    def _write(self, target, data):
        raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_RUN_BYTES:
            raise ValueError('변경 비교 저장 한도를 초과했습니다.')
        if target.exists() and _redirected(target.lstat()):
            raise ValueError('연결된 변경 비교 기록은 사용할 수 없습니다.')
        temporary = self.directory / ('.capture-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _reserve(self, target):
        entries = [(path, stamp) for path, stamp in self._entries() if path != target]
        if len(entries) < self.max_runs:
            return
        ranked = []
        for path, stamp in entries:
            try:
                data = self._read(path)
                active = data.get('after') is None
            except (OSError, ValueError, UnicodeError, RecursionError):
                active = False
            ranked.append((active, stamp, path))
        for _, _, path in sorted(ranked)[:len(entries) - self.max_runs + 1]:
            path.unlink()

    def _capture_file(self, path, info, result, name):
        _safe_path(path)
        # Windows directory enumeration may omit the hard-link count.
        checked = path.lstat()
        if checked.st_nlink > 1 or _signature(checked) != _signature(info):
            return
        row = {'id': _file_id(name), 'name': name, 'signature': _signature(info),
               'size': info.st_size, 'textStatus': 'unsupported'}
        result['files'][name] = row
        result['scanned'] += 1
        if path.suffix.lower() not in TEXT_TYPES:
            return
        if info.st_size > self.file_bytes:
            row['textStatus'] = 'file_limit'
            return
        if result['textBytes'] + info.st_size > self.capture_bytes:
            row['textStatus'] = 'capture_limit'
            return
        row['textStatus'] = 'read_failed'
        try:
            raw = self._read_source(path, info)
            if result['textBytes'] + len(raw) > self.capture_bytes:
                row['textStatus'] = 'capture_limit'
                return
            text, encoding = _decode(raw)
            row.update(text=text, encoding=encoding, textStatus='captured')
            result['textBytes'] += len(raw)
        except UnicodeError:
            row['textStatus'] = 'encoding_or_binary'
        except (OSError, ValueError):
            result['errors'] += 1

    def _capture(self, root, allowed_names=None, *, prioritize_names=()):
        started = time.monotonic()
        result = {'files': {}, 'directories': ['.'], 'complete': [], 'limited': False,
                  'errors': 0, 'scanned': 0, 'textBytes': 0, 'capturedAt': time.time()}
        if allowed_names is not None and not allowed_names:
            return result
        allowed_dirs = None if allowed_names is None else {os.path.dirname(name) for name in allowed_names}
        try:
            _safe_path(root, directory=True)
        except (OSError, ValueError):
            result['errors'] = 1
            return result
        pending = [(root, 0)]
        queued = {'.'}
        entries = 0
        # Preserve exact Write/Edit captures when a command also ran. These
        # reads consume the same time, entries, directories, files and text
        # budgets as the later observation scan; no extra capture is added.
        for name in prioritize_names:
            entries += 1
            if (entries > self.max_entries or len(result['files']) >= self.max_files
                    or time.monotonic() - started >= self.seconds):
                result['limited'] = True
                return result
            try:
                parts = Path(name).parts
                if (not 1 <= len(parts) <= 2 or Path(name).is_absolute()
                        or any(part in {'.', '..'} or ':' in part or _hidden_or_secret(part) for part in parts)):
                    result['limited'] = True
                    continue
                path = root.joinpath(*parts)
                parent = path.parent.relative_to(root).as_posix()
                if parent not in queued:
                    if len(pending) > self.max_directories:
                        result['limited'] = True
                        continue
                    _safe_path(path.parent, directory=True)
                    queued.add(parent)
                    result['directories'].append(parent)
                    pending.append((path.parent, 1))
                info = path.lstat()
                if (not _redirected(info) and not getattr(info, 'st_file_attributes', 0) & 2
                        and stat.S_ISREG(info.st_mode) and info.st_nlink <= 1
                        and path.suffix.lower() in TEXT_TYPES | METADATA_TYPES):
                    self._capture_file(path, info, result, path.relative_to(root).as_posix())
            except (FileNotFoundError, NotADirectoryError):
                pass  # Full directory completion is still needed to prove absence.
            except (OSError, ValueError):
                result['errors'] += 1
        for directory, depth in pending:
            if time.monotonic() - started >= self.seconds:
                result['limited'] = True
                break
            errors_before = result['errors']
            try:
                _safe_path(directory, directory=True)
                with os.scandir(directory) as children:
                    for child in children:
                        entries += 1
                        if (entries > self.max_entries or len(result['files']) >= self.max_files
                                or time.monotonic() - started >= self.seconds):
                            result['limited'] = True
                            return result
                        if _hidden_or_secret(child.name):
                            continue
                        try:
                            info = child.stat(follow_symlinks=False)
                            if _redirected(info) or getattr(info, 'st_file_attributes', 0) & 2:
                                continue
                            path = Path(child.path)
                            if path == self.directory or path in self.directory.parents:
                                continue
                            name = path.relative_to(root).as_posix()
                            if stat.S_ISDIR(info.st_mode) and depth == 0:
                                if allowed_dirs is not None and os.path.normcase(name) not in allowed_dirs:
                                    continue
                                if name in queued:
                                    continue
                                result['directories'].append(name)
                                if len(pending) <= self.max_directories:
                                    queued.add(name)
                                    pending.append((path, 1))
                                else:
                                    result['limited'] = True
                            elif (stat.S_ISREG(info.st_mode) and info.st_nlink <= 1
                                  and path.suffix.lower() in TEXT_TYPES | METADATA_TYPES):
                                if allowed_names is not None and os.path.normcase(name) not in allowed_names:
                                    continue
                                if name in result['files']:
                                    continue
                                self._capture_file(path, info, result, name)
                        except (OSError, ValueError):
                            result['errors'] += 1
                    if result['errors'] == errors_before:
                        result['complete'].append(directory.relative_to(root).as_posix())
            except (OSError, ValueError):
                result['errors'] += 1
        return result

    def _read_source(self, path, expected):
        # Open without following symlinks where supported, then compare the
        # opened object and path again. Never use the result of a changed file.
        descriptor = _open_source(path)
        with os.fdopen(descriptor, 'rb') as handle:
            actual = os.fstat(handle.fileno())
            if (_redirected(actual) or not stat.S_ISREG(actual.st_mode) or actual.st_nlink > 1
                    or _signature(actual) != _signature(expected)
                    or (actual.st_ino and expected.st_ino and actual.st_ino != expected.st_ino)):
                raise ValueError('capture changed')
            raw = handle.read(self.file_bytes + 1)
            final = os.fstat(handle.fileno())
        _safe_path(path)
        if (len(raw) > self.file_bytes or _signature(final) != _signature(expected)
                or _signature(path.stat()) != _signature(expected)):
            raise ValueError('capture changed')
        return raw

    @staticmethod
    def _capture_summary(capture):
        return {key: capture.get(key) for key in ('limited', 'errors', 'scanned', 'textBytes', 'capturedAt')}

    def start(self, workspace, run_id, *, session_id=None):
        root = Path(workspace).absolute()
        try:
            with self.lock:
                self._ensure_store()
                target = self._target(root, run_id)
                if target.exists():
                    return {'status': 'already_captured', 'runId': run_id}
                before = self._capture(root)
                data = {'version': 1, 'workspace': str(root), 'runId': run_id,
                        'startedAt': time.time(), 'before': before, 'after': None}
                if session_id is not None:
                    data['sessionId'] = session_id
                self._reserve(target)
                self._write(target, data)
                return {'status': 'captured', 'runId': run_id,
                        'capture': self._capture_summary(before), 'limits': self.limits()}
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, RecursionError):
            return {'status': 'unavailable', 'runId': run_id, 'reason': 'capture_failed'}

    def finish(self, workspace, run_id, *, allowed_paths=None,
               observe_commands=False, conflict_paths=()):
        root = Path(workspace).absolute()
        try:
            with self.lock:
                self._ensure_store()
                data = self._read(self._target(root, run_id))
                if data.get('workspace') != str(root) or data.get('runId') != run_id:
                    raise ValueError('capture mismatch')
                if data.get('after') is not None:
                    return {'status': 'already_captured', 'runId': run_id}
                if observe_commands:
                    # A shell completion proves execution, not which files it
                    # touched. Keep boundary deltas separate until the user
                    # explicitly assigns a candidate to this task.
                    allowed = {os.path.normcase(name) for name in (allowed_paths or ())}
                    conflicts = {os.path.normcase(name) for name in conflict_paths}
                    # Older readers reject version 2 rather than interpreting
                    # review-only candidates as exact tool-attributed changes.
                    data['version'] = 2
                    data['after'] = self._capture(root, prioritize_names=allowed_paths or ())
                    all_changes = self._changes(data)
                    data['primaryNames'] = [row['name'] for row in all_changes
                                            if os.path.normcase(row['name']) in allowed
                                            and row['change'] != 'deleted']
                    pending = [row for row in all_changes
                               if (os.path.normcase(row['name']) not in allowed or row['change'] == 'deleted')
                               and row['change'] in {'created', 'modified', 'deleted'}]
                    data['observedCandidates'] = {
                        row['id']: {'name': row['name'], 'decision': 'pending',
                                    'conflict': os.path.normcase(row['name']) in conflicts}
                        for row in pending[:self.max_files]}
                    data['observedLimited'] = len(pending) > self.max_files
                elif 'sessionId' in data or allowed_paths is not None:
                    # A missing write receipt on a scoped run means no attributable
                    # change. Never fall back to all changes in a shared directory.
                    allowed = {os.path.normcase(name) for name in (allowed_paths or ())}
                    old = data['before']['files']
                    data['before']['files'] = {name: row for name, row in old.items()
                                               if os.path.normcase(name) in allowed}
                    data['after'] = self._capture(root, allowed)
                else:
                    data['after'] = self._capture(root)
                data['finishedAt'] = time.time()
                self._write(self._target(root, run_id), data)
                files = self._visible_changes(data)
                return {'status': 'captured', 'runId': run_id,
                        'capture': self._capture_summary(data['after']),
                        'changeCount': len(files), 'limits': self.limits(),
                        'lineCounts': self._small_code_counts(data, files) if 'sessionId' in data else {},
                        'observedCount': len(data.get('observedCandidates', {})),
                        'observedLimited': data.get('observedLimited', False)}
        except FileNotFoundError:
            return {'status': 'unavailable', 'runId': run_id, 'reason': 'baseline_not_available'}
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, RecursionError):
            return {'status': 'unavailable', 'runId': run_id, 'reason': 'capture_failed'}

    @staticmethod
    def _absence_known(capture, name):
        parent = Path(name).parent.as_posix()
        return (parent in capture.get('complete', []) or
                (parent != '.' and '.' in capture.get('complete', [])
                 and parent not in capture.get('directories', [])))

    def _small_code_counts(self, data, files):
        # Summaries use already-captured text, never a fresh file read. Larger
        # comparisons remain on demand so completion stays cheap across tasks.
        counts = {}
        attempts = 0
        for file in files:
            name = file['name']
            if not file['textAvailable'] or Path(name).suffix.lower() not in CODE_REVIEW_TYPES:
                continue
            sides = [data[phase]['files'].get(name, {}).get('text', '') for phase in ('before', 'after')]
            if sum(map(len, sides)) > 8192:
                continue
            old, new = (SOURCE_LINE.findall(side) for side in sides)
            if max(len(old), len(new)) > 128 or len(old)*len(new) > min(8192, self.diff_work):
                continue
            attempts += 1
            comparison = self._bounded_diff(old, new, name)
            if not comparison['truncated']:
                rows = [line['kind'] for hunk in comparison['hunks'] for line in hunk['rows']]
                counts[os.path.normcase(name)] = {'added': rows.count('add'), 'removed': rows.count('remove')}
            if attempts == 3:
                break
        return counts

    def _changes(self, data):
        before, after = data.get('before'), data.get('after')
        if not isinstance(before, dict) or not isinstance(after, dict):
            return []
        rows = []
        old, new = before.get('files', {}), after.get('files', {})
        for name in sorted(old.keys() | new.keys()):
            left, right = old.get(name), new.get(name)
            if left and right:
                captured = left.get('textStatus') == right.get('textStatus') == 'captured'
                if (left.get('text') == right.get('text') if captured
                        else left.get('signature') == right.get('signature')):
                    continue
                change = 'modified'
            elif right:
                change = 'created' if self._absence_known(before, name) else 'unconfirmed'
            else:
                change = 'deleted' if self._absence_known(after, name) else 'unconfirmed'
            rows.append({'id': _file_id(name), 'name': name, 'change': change,
                         'beforeSize': left.get('size') if left else None,
                         'afterSize': right.get('size') if right else None,
                         'beforeStatus': left.get('textStatus') if left else 'absent',
                         'afterStatus': right.get('textStatus') if right else 'absent',
                         'textAvailable': change != 'unconfirmed' and
                            all(row is None or row.get('textStatus') == 'captured' for row in (left, right))})
        return rows

    def _visible_changes(self, data, candidate_id=None):
        rows = self._changes(data)
        candidates = data.get('observedCandidates')
        if candidates is None:
            return [] if candidate_id is not None else rows
        if candidate_id is not None:
            candidate = candidates.get(candidate_id)
            return [row for row in rows if row['id'] == candidate_id] if (
                candidate and candidate['decision'] == 'pending') else []
        primary = set(data['primaryNames'])
        included = {row['name'] for row in candidates.values() if row['decision'] == 'include'}
        return [dict(row, attribution='confirmed', runId=data['runId'],
                     conflict=candidates[row['id']]['conflict']) if row['name'] in included
                else dict(row, attribution='tool')
                for row in rows if row['name'] in primary | included]

    @staticmethod
    def _candidate_id(candidate_id):
        if not isinstance(candidate_id, str) or not re.fullmatch(r'[a-f0-9]{24}', candidate_id):
            raise ValueError('명령 실행 변경 파일 식별자가 올바르지 않습니다.')
        return candidate_id

    @staticmethod
    def _candidate_metadata(data, row, candidate, *, attribution='observed'):
        return dict(row, path=row['name'], runId=data['runId'], attribution=attribution,
                    conflict=candidate['conflict'], createdAt=data.get('finishedAt'))

    def candidates(self, workspace, run_id=None, *, session_id=None, legacy_runs=()):
        root = _safe_path(Path(workspace).absolute(), directory=True)
        with self.lock:
            self._ensure_store()
            paths = ([self._target(root, run_id)] if run_id is not None else
                     [path for path, _ in reversed(self._entries())][:self.max_runs])
            rows = []
            for path in paths:
                try:
                    data = self._read(path)
                    if (data['workspace'] != str(root)
                            or not self._belongs_to(data, session_id, legacy_runs)):
                        continue
                    pending = data.get('observedCandidates', {})
                    for row in self._changes(data):
                        candidate = pending.get(row['id'])
                        if candidate and candidate['decision'] == 'pending':
                            rows.append(self._candidate_metadata(data, row, candidate))
                except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                    continue
            return rows

    def resolve_candidate(self, workspace, run_id, candidate_id, *, session_id, action):
        self._candidate_id(candidate_id)
        if (not isinstance(action, str) or action not in {'include', 'dismiss'}
                or not isinstance(session_id, str) or not session_id):
            raise ValueError('명령 실행 변경 확인 요청이 올바르지 않습니다.')
        root = _safe_path(Path(workspace).absolute(), directory=True)
        with self.lock:
            self._ensure_store()
            target = self._target(root, run_id)
            data = self._read(target)
            if (data['workspace'] != str(root) or data['runId'] != run_id
                    or data.get('sessionId') != session_id):
                raise ValueError('이 업무의 명령 실행 변경 기록이 아닙니다.')
            candidate = data.get('observedCandidates', {}).get(candidate_id)
            if candidate is None or candidate['decision'] not in {'pending', action}:
                raise ValueError('이미 처리했거나 확인할 수 없는 변경 기록입니다.')
            row = next((row for row in self._changes(data) if row['id'] == candidate_id
                        and row['change'] in {'created', 'modified', 'deleted'}), None)
            if row is None:
                raise ValueError('확인할 수 없는 변경 기록입니다.')
            candidate['decision'] = action
            self._write(target, data)
            result = self._candidate_metadata(data, row, candidate,
                                             attribution='confirmed' if action == 'include' else 'observed')
            if action == 'include':
                counts = self._small_code_counts(data, [row]).get(os.path.normcase(row['name']))
                if counts is not None:
                    result['lineCounts'] = counts
            return result

    @staticmethod
    def _belongs_to(data, session_id, legacy_runs):
        if session_id is None:
            return True
        if 'sessionId' in data:
            return data['sessionId'] == session_id
        # Older versions did not record a session ID on the capture. Only the
        # owning task's saved run history can connect it to this view.
        return data['runId'] in legacy_runs

    def list(self, workspace, run_id=None, *, session_id=None, legacy_runs=(), candidate_id=None,
             include_observed=False):
        if candidate_id is not None:
            self._candidate_id(candidate_id)
            if run_id is None:
                raise ValueError('명령 실행 변경의 요청 기록을 선택하세요.')
        root = _safe_path(Path(workspace).absolute(), directory=True)
        with self.lock:
            self._ensure_store()
            paths = ([self._target(root, run_id)] if run_id is not None else
                     [path for path, _ in reversed(self._entries())][:self.max_runs])
            rows, observed, errors = [], [], 0
            for path in paths:
                try:
                    data = self._read(path)
                    if (data.get('workspace') != str(root) or (run_id is not None and run_id != data['runId'])
                            or not self._belongs_to(data, session_id, legacy_runs)):
                        continue
                    files = self._visible_changes(data, candidate_id)
                    if include_observed:
                        pending = data.get('observedCandidates', {})
                        for row in self._changes(data):
                            candidate = pending.get(row['id'])
                            if (candidate and candidate['decision'] == 'pending'
                                    and (candidate_id is None or candidate_id == row['id'])):
                                observed.append(self._candidate_metadata(data, row, candidate))
                    if candidate_id is not None and not files:
                        continue
                    rows.append({'runId': data['runId'], 'startedAt': data.get('startedAt'),
                                 'attribution': ('observed' if candidate_id is not None else
                                                 'tool' if 'sessionId' in data else 'workspace'),
                                 'finishedAt': data.get('finishedAt'),
                                 'status': 'complete' if data.get('after') is not None else 'capturing',
                                 'before': self._capture_summary(data['before']),
                                 'after': self._capture_summary(data['after']) if data.get('after') else None,
                                 'files': files})
                except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                    errors += 1
            result = {'runs': rows, 'errors': errors, 'limits': self.limits()}
            if include_observed:
                result['observedChanges'] = observed
            return result

    def get(self, workspace, run_id, file_id, *, session_id=None, legacy_runs=(), candidate_id=None):
        root = _safe_path(Path(workspace).absolute(), directory=True)
        if not isinstance(file_id, str) or not re.fullmatch(r'[a-f0-9]{24}', file_id):
            raise ValueError('변경 비교 파일 식별자가 올바르지 않습니다.')
        if candidate_id is not None and self._candidate_id(candidate_id) != file_id:
            raise ValueError('명령 실행 변경 파일이 일치하지 않습니다.')
        with self.lock:
            self._ensure_store()
            data = self._read(self._target(root, run_id))
            if (data.get('workspace') != str(root) or data.get('runId') != run_id
                    or not self._belongs_to(data, session_id, legacy_runs)):
                raise ValueError('변경 비교 기록을 확인할 수 없습니다.')
            row = next((row for row in self._visible_changes(data, candidate_id) if row['id'] == file_id), None)
            if row is None:
                raise ValueError('변경 비교 기록에 없는 파일입니다.')
            result = dict(row, runId=run_id, startedAt=data.get('startedAt'),
                          finishedAt=data.get('finishedAt'), version='captured',
                          currentFileRead=False, diff='', truncated=False, limits=self.limits(),
                          attribution='observed' if candidate_id is not None else row.get('attribution',
                              'tool' if 'sessionId' in data else 'workspace'))
            if not row['textAvailable']:
                return dict(result, status='metadata_only', reason=(
                    'observation_incomplete' if row['change'] == 'unconfirmed' else 'text_not_captured'))
            name = row['name']
            left = data['before']['files'].get(name, {}).get('text', '')
            right = data['after']['files'].get(name, {}).get('text', '')
            # This product bound prevents quadratic matcher work even for
            # repetitive/adversarial text. Long lines count as work as well.
            if (left.count('\n') > self.diff_input_lines or right.count('\n') > self.diff_input_lines):
                return dict(result, status='metadata_only', reason='diff_input_limit')
            # Code editor line numbers count CRLF, CR or LF. Unicode separators
            # and form feeds inside a source line must stay with that line.
            a, b = SOURCE_LINE.findall(left), SOURCE_LINE.findall(right)
            if len(a) > self.diff_input_lines or len(b) > self.diff_input_lines:
                return dict(result, status='metadata_only', reason='diff_input_limit')
            if len(a) * len(b) > self.diff_work:
                return dict(result, status='metadata_only', reason='diff_work_limit')
            patch = self._bounded_diff(a, b, name)
            _mark_inline_edits(patch)
            return dict(result, status='text', **patch)

    def _bounded_diff(self, before, after, name):
        """Project the same finite patch into legacy text and numbered source rows."""
        lines, hunks = [], []
        size = old_line = new_line = 0
        truncated = False
        for index, raw in enumerate(difflib.unified_diff(
                before, after, fromfile='before/' + name, tofile='after/' + name, n=3)):
            line = raw if raw.endswith('\n') else raw + '\n'
            cost = len(line.encode('utf-8'))
            if len(lines) >= self.diff_lines or size + cost > self.diff_bytes:
                truncated = True
                break
            size += cost
            lines.append(line)
            if index < 2:  # Filenames occupy the first two records even if source begins with +++ or ---.
                continue
            header = HUNK_HEADER.fullmatch(raw)
            if header:
                old_line, new_line = int(header[1]), int(header[3])
                hunks.append({'beforeStart': old_line, 'beforeCount': int(header[2] or 1),
                              'afterStart': new_line, 'afterCount': int(header[4] or 1), 'rows': []})
                continue
            marker, original = raw[0], raw[1:]
            content = original.rstrip('\r\n')
            hunks[-1]['rows'].append({
                'kind': {' ': 'context', '+': 'add', '-': 'remove'}[marker],
                'beforeLine': old_line if marker != '+' else None,
                'afterLine': new_line if marker != '-' else None,
                'text': content, 'ending': original[len(content):]})
            old_line += marker != '+'
            new_line += marker != '-'
        return {'diff': ''.join(lines), 'hunks': hunks, 'truncated': truncated}
