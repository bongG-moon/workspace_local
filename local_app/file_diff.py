"""Finite, app-owned text captures for review; never restore or alter originals.

The caller must enforce session/workspace access and capture only after its
workspace trust decision. Reviewing previously captured app-owned records does
not execute workspace settings. This module adds path checks and treats partial
observations as unknown, not evidence a file was created or removed by Claude.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import time
import uuid


TEXT_TYPES = frozenset({'.txt', '.md', '.csv', '.tsv', '.html', '.htm', '.css', '.js',
    '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.py', '.json', '.yaml', '.yml', '.toml',
    '.xml', '.sql', '.ps1', '.sh', '.bat', '.cmd', '.ini', '.cfg', '.log', '.svg'})
METADATA_TYPES = frozenset({'.pdf', '.pptx', '.docx', '.xlsx', '.xls', '.ppt', '.doc',
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.zip'})
EXCLUDED = frozenset({'node_modules', 'venv', '__pycache__', 'build', 'dist'})
SECRET_NAME = re.compile(r'(^|[._-])(secret|secrets|credential|credentials|password|passwords|'
                         r'token|tokens|auth|apikey|api_key|private)([._-]|$)', re.I)
RUN_ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
STORE_NAME = re.compile(r'[a-f0-9]{64}\.json\Z')
TEMP_NAME = re.compile(r'\.capture-[a-f0-9]{32}\.tmp\Z')
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
        if not isinstance(data, dict) or data.get('version') != 1:
            raise ValueError('변경 비교 기록의 형식이 올바르지 않습니다.')
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

    def _capture(self, root):
        started = time.monotonic()
        result = {'files': {}, 'directories': ['.'], 'complete': [], 'limited': False,
                  'errors': 0, 'scanned': 0, 'textBytes': 0, 'capturedAt': time.time()}
        try:
            _safe_path(root, directory=True)
        except (OSError, ValueError):
            result['errors'] = 1
            return result
        pending = [(root, 0)]
        entries = 0
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
                                result['directories'].append(name)
                                if len(pending) <= self.max_directories:
                                    pending.append((path, 1))
                                else:
                                    result['limited'] = True
                            elif (stat.S_ISREG(info.st_mode) and info.st_nlink <= 1
                                  and path.suffix.lower() in TEXT_TYPES | METADATA_TYPES):
                                _safe_path(path)
                                # Windows directory enumeration may omit the
                                # hard-link count; lstat queries the file itself.
                                checked = path.lstat()
                                if checked.st_nlink > 1 or _signature(checked) != _signature(info):
                                    continue
                                row = {'id': _file_id(name), 'name': name,
                                       'signature': _signature(info), 'size': info.st_size,
                                       'textStatus': 'unsupported'}
                                result['files'][name] = row
                                result['scanned'] += 1
                                if path.suffix.lower() not in TEXT_TYPES:
                                    continue
                                if info.st_size > self.file_bytes:
                                    row['textStatus'] = 'file_limit'
                                    continue
                                if result['textBytes'] + info.st_size > self.capture_bytes:
                                    row['textStatus'] = 'capture_limit'
                                    continue
                                row['textStatus'] = 'read_failed'
                                try:
                                    raw = self._read_source(path, info)
                                    if result['textBytes'] + len(raw) > self.capture_bytes:
                                        row['textStatus'] = 'capture_limit'
                                        continue
                                    text, encoding = _decode(raw)
                                    row.update(text=text, encoding=encoding, textStatus='captured')
                                    result['textBytes'] += len(raw)
                                except UnicodeError:
                                    row['textStatus'] = 'encoding_or_binary'
                                except (OSError, ValueError):
                                    result['errors'] += 1
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

    def start(self, workspace, run_id):
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
                self._reserve(target)
                self._write(target, data)
                return {'status': 'captured', 'runId': run_id,
                        'capture': self._capture_summary(before), 'limits': self.limits()}
        except (OSError, ValueError, UnicodeError, KeyError, TypeError, RecursionError):
            return {'status': 'unavailable', 'runId': run_id, 'reason': 'capture_failed'}

    def finish(self, workspace, run_id):
        root = Path(workspace).absolute()
        try:
            with self.lock:
                self._ensure_store()
                data = self._read(self._target(root, run_id))
                if data.get('workspace') != str(root) or data.get('runId') != run_id:
                    raise ValueError('capture mismatch')
                if data.get('after') is not None:
                    return {'status': 'already_captured', 'runId': run_id}
                data['after'] = self._capture(root)
                data['finishedAt'] = time.time()
                self._write(self._target(root, run_id), data)
                return {'status': 'captured', 'runId': run_id,
                        'capture': self._capture_summary(data['after']),
                        'changeCount': len(self._changes(data)), 'limits': self.limits()}
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

    def list(self, workspace, run_id=None):
        root = _safe_path(Path(workspace).absolute(), directory=True)
        with self.lock:
            self._ensure_store()
            paths = ([self._target(root, run_id)] if run_id is not None else
                     [path for path, _ in reversed(self._entries())][:self.max_runs])
            rows, errors = [], 0
            for path in paths:
                try:
                    data = self._read(path)
                    if data.get('workspace') != str(root):
                        continue
                    rows.append({'runId': data['runId'], 'startedAt': data.get('startedAt'),
                                 'finishedAt': data.get('finishedAt'),
                                 'status': 'complete' if data.get('after') is not None else 'capturing',
                                 'before': self._capture_summary(data['before']),
                                 'after': self._capture_summary(data['after']) if data.get('after') else None,
                                 'files': self._changes(data)})
                except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                    errors += 1
            return {'runs': rows, 'errors': errors, 'limits': self.limits()}

    def get(self, workspace, run_id, file_id):
        root = _safe_path(Path(workspace).absolute(), directory=True)
        if not isinstance(file_id, str) or not re.fullmatch(r'[a-f0-9]{24}', file_id):
            raise ValueError('변경 비교 파일 식별자가 올바르지 않습니다.')
        with self.lock:
            self._ensure_store()
            data = self._read(self._target(root, run_id))
            if data.get('workspace') != str(root) or data.get('runId') != run_id:
                raise ValueError('변경 비교 기록을 확인할 수 없습니다.')
            row = next((row for row in self._changes(data) if row['id'] == file_id), None)
            if row is None:
                raise ValueError('변경 비교 기록에 없는 파일입니다.')
            result = dict(row, runId=run_id, startedAt=data.get('startedAt'),
                          finishedAt=data.get('finishedAt'), version='captured',
                          currentFileRead=False, diff='', truncated=False, limits=self.limits())
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
            a, b = left.splitlines(keepends=True), right.splitlines(keepends=True)
            if len(a) > self.diff_input_lines or len(b) > self.diff_input_lines:
                return dict(result, status='metadata_only', reason='diff_input_limit')
            if len(a) * len(b) > self.diff_work:
                return dict(result, status='metadata_only', reason='diff_work_limit')
            lines, size = [], 0
            for line in difflib.unified_diff(a, b, fromfile='before/' + name, tofile='after/' + name, n=3):
                # Ensure records are distinct when either input lacks a final LF.
                line = line if line.endswith('\n') else line + '\n'
                cost = len(line.encode('utf-8'))
                if len(lines) >= self.diff_lines or size + cost > self.diff_bytes:
                    result['truncated'] = True
                    break
                lines.append(line)
                size += cost
            return dict(result, status='text', diff=''.join(lines))
