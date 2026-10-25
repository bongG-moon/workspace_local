"""Optional installed extensions; discovery never starts code or makes requests.

The host grants a narrow UI/RPC boundary, not an OS sandbox for installed Python
code. Only explicitly installed, trusted packages belong in this directory.
Worker jobs are finite, independently serialized and own their descendants.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import threading
import time

from .owned_process import CancelledError, run_owned

HOST_API = 1
MAX_EXTENSIONS = 16
MAX_MANIFEST = 128 * 1024
MAX_FILES = 512
MAX_PACKAGE = 64 * 1024 * 1024
MAX_UI = 1024 * 1024
MAX_RESULT = 2 * 1024 * 1024
MAX_PAYLOAD = 240 * 1024
ACTION_TIMEOUT = 120
TICKET_TTL = 30
DIRECTORY_TTL = 15 * 60
ID = re.compile(r'[a-z0-9][a-z0-9.-]{0,62}[a-z0-9]|[a-z0-9]')
ACTION = re.compile(r'[a-z][a-zA-Z0-9_.-]{0,63}')
SHA = re.compile(r'[0-9a-f]{64}')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?')
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
UI_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
          "img-src data:; connect-src 'none'; font-src 'none'; object-src 'none'; "
          "frame-src 'none'; frame-ancestors 'self'; form-action 'none'; base-uri 'none'")


class ExtensionError(ValueError):
    def __init__(self, message, *, code='extension_error', status=400, verification_required=False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.verification_required = verification_required


def no_links(path):
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ExtensionError('확장 설치 경로에 링크가 있습니다. 확장을 다시 설치해 주세요.')
    return path


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value) > 220 or '\\' in value
            or ':' in value or any(ord(c) < 32 for c in value)):
        raise ExtensionError('확장 파일 경로를 확인할 수 없습니다.')
    parts = value.split('/')
    if any(p in {'', '.', '..'} or p.endswith((' ', '.')) or p.split('.')[0].casefold() in RESERVED for p in parts):
        raise ExtensionError('확장 파일 경로를 확인할 수 없습니다.')
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ExtensionError('확장 파일 경로를 확인할 수 없습니다.')
    return value


def _label(value, maximum):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def worker_environment():
    # Do not inherit Claude credentials, app capabilities, Python injection,
    # arbitrary environment tokens or unrelated service credentials.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'TEMP', 'TMP',
               'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA',
               'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'OS',
               'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL'}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


class ExtensionHost:
    def __init__(self, state, *, runner=run_owned, clock=time.monotonic):
        self.root = Path(state).absolute().parent / 'extensions'
        self.data_root = Path(state).absolute().parent / 'extension-data'
        self.runner = runner
        self.clock = clock
        self._catalog = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active = set()
        self._closing = False
        self._cancel = threading.Event()
        self._tickets = {}
        self._directories = {}

    def _manifest(self, extension_id):
        if (not isinstance(extension_id, str) or not ID.fullmatch(extension_id) or '..' in extension_id
                or extension_id.split('.')[0] in RESERVED):
            raise ExtensionError('확장 ID를 확인해 주세요.')
        directory = no_links(self.root / extension_id)
        path = no_links(directory / 'extension.json')
        try:
            if not path.is_file() or path.stat().st_size > MAX_MANIFEST:
                raise ExtensionError('확장 등록 정보를 읽을 수 없습니다.')
            with path.open('rb') as source:
                raw = source.read(MAX_MANIFEST + 1)
            if len(raw) > MAX_MANIFEST:
                raise ExtensionError('확장 등록 정보가 너무 큽니다.')
            value = json.loads(raw.decode('utf-8-sig'))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ExtensionError('확장 등록 정보를 읽을 수 없습니다.') from None
        if (not isinstance(value, dict) or type(value.get('schemaVersion')) is not int
                or value.get('schemaVersion') != 1 or value.get('hostApi') != HOST_API
                or type(value.get('hostApi')) is not int or value.get('id') != extension_id
                or not _label(value.get('name'), 60) or not _label(value.get('version'), 50)
                or not VERSION.fullmatch(value['version'])):
            raise ExtensionError('현재 앱과 호환되지 않거나 올바르지 않은 확장입니다.', code='extension_incompatible')
        actions = value.get('actions')
        files = value.get('files')
        if (not isinstance(actions, list) or not 1 <= len(actions) <= 48
                or any(not isinstance(v, str) or not ACTION.fullmatch(v) for v in actions)
                or len(set(actions)) != len(actions) or not isinstance(files, dict)
                or not 2 <= len(files) <= MAX_FILES):
            raise ExtensionError('확장 동작과 파일 선언이 올바르지 않습니다.')
        for name, checksum in files.items():
            relative_path(name)
            if name.casefold() == 'extension.json' or not isinstance(checksum, str) or not SHA.fullmatch(checksum):
                raise ExtensionError('확장 파일의 검증 정보를 확인할 수 없습니다.')
        if len({name.casefold() for name in files}) != len(files):
            raise ExtensionError('확장에 중복된 파일 이름이 있습니다.')
        if any(name.startswith('packages/') for name in files):
            prefixes = {'/'.join(name.split('/')[:2]) for name in files}
            if len(prefixes) != 1 or not re.fullmatch(r'packages/[a-f0-9]{64}', next(iter(prefixes))):
                raise ExtensionError('확장 파일은 하나의 검증된 설치 버전에 있어야 합니다.')
        entrypoint, ui = relative_path(value.get('entrypoint')), relative_path(value.get('ui'))
        if entrypoint not in files or ui not in files or not entrypoint.endswith('.py') or not ui.endswith('.html'):
            raise ExtensionError('확장 시작 파일을 확인할 수 없습니다.')
        return directory, value

    def snapshot(self, *, refresh=False):
        with self._lock:
            if self._catalog is not None and not refresh:
                return deepcopy(self._catalog)
        rows, warnings = [], []
        try:
            # An absent directory is one cheap existence check, without mkdir,
            # recursive walks, worker imports, thread creation or network I/O.
            if self.root.exists():
                no_links(self.root)
                with os.scandir(self.root) as entries:
                    candidates = list(itertools.islice(entries, MAX_EXTENSIONS + 1))
                if len(candidates) > MAX_EXTENSIONS:
                    raise ExtensionError('설치된 확장 수가 지원 한도를 넘었습니다.')
                for entry in candidates:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    try:
                        _, item = self._manifest(entry.name)
                        rows.append({key: deepcopy(item[key]) for key in ('id', 'name', 'version', 'hostApi', 'actions')})
                    except ExtensionError:
                        warnings.append('호환되지 않거나 손상된 확장 하나를 불러오지 않았습니다.')
        except (OSError, ExtensionError):
            warnings.append('확장 설치 위치를 확인하지 못했습니다. 기존 채팅은 계속 사용할 수 있습니다.')
        result = {'extensions': sorted(rows, key=lambda row: (row['name'], row['id'])), 'warnings': warnings}
        with self._lock:
            self._catalog = result
        return deepcopy(result)

    def _verified(self, extension_id):
        directory, manifest = self._manifest(extension_id)
        versioned = all(name.startswith('packages/') for name in manifest['files'])
        # The installer atomically switches the manifest to an immutable package.
        # Previous versions and its ownership marker remain inert outside that
        # one declared subtree; running workers keep their exact source path.
        package_directory = directory.joinpath(*manifest['entrypoint'].split('/')[:2]) if versioned else directory
        expected = set(manifest['files']) | (set() if versioned else {'extension.json'})
        discovered, count, size = set(), 0, 0
        pending = [package_directory]
        while pending:
            current = no_links(pending.pop())
            with os.scandir(current) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_FILES * 2:
                        raise ExtensionError('확장 파일 수가 지원 한도를 넘었습니다.')
                    path = no_links(Path(entry.path))
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        name = path.relative_to(directory).as_posix()
                        discovered.add(name)
                        if name not in expected:
                            raise ExtensionError('확장 설치 파일이 변경되었습니다. 확장을 다시 설치해 주세요.')
                        length = path.stat().st_size
                        size += length
                        if size > MAX_PACKAGE:
                            raise ExtensionError('확장 파일 크기가 지원 한도를 넘었습니다.')
                        if name != 'extension.json':
                            digest = hashlib.sha256()
                            consumed = 0
                            with path.open('rb') as source:
                                while block := source.read(65536):
                                    consumed += len(block)
                                    if consumed > length:
                                        raise ExtensionError('확장 파일이 검증 중 변경되었습니다. 다시 열어 주세요.')
                                    digest.update(block)
                            if digest.hexdigest() != manifest['files'][name]:
                                raise ExtensionError('확장 파일 검증에 실패했습니다. 확장을 다시 설치해 주세요.')
                    else:
                        raise ExtensionError('지원하지 않는 확장 파일 형식입니다.')
        if discovered != expected:
            raise ExtensionError('필요한 확장 파일이 없습니다. 확장을 다시 설치해 주세요.')
        return directory, manifest

    def view(self, extension_id):
        try:
            directory, manifest = self._verified(extension_id)
            path = no_links(directory / manifest['ui'])
            if path.stat().st_size > MAX_UI:
                raise ExtensionError('확장 화면이 너무 큽니다.')
            with path.open('rb') as source:
                html = source.read(MAX_UI + 1)
            if len(html) > MAX_UI or hashlib.sha256(html).hexdigest() != manifest['files'][manifest['ui']]:
                raise ExtensionError('확장 화면이 변경되었습니다. 확장을 다시 설치해 주세요.')
            html.decode('utf-8')
        except (OSError, UnicodeError):
            raise ExtensionError('확장 화면을 읽지 못했습니다.') from None
        now = self.clock()
        with self._lock:
            if self._closing:
                raise ExtensionError('앱 종료 중에는 확장을 열 수 없습니다.', status=409)
            self._tickets = {key: value for key, value in self._tickets.items() if value[0] > now}
            # One live view ticket per extension; reopening invalidates the old
            # unconsumed URL instead of retaining multiple large HTML bodies.
            self._tickets = {key: value for key, value in self._tickets.items() if value[1] != extension_id}
            if len(self._tickets) >= MAX_EXTENSIONS:
                raise ExtensionError('확장 화면을 닫은 뒤 다시 열어 주세요.', status=409)
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = (now + TICKET_TTL, extension_id, html)
        return {'id': extension_id, 'url': f'/extension-ui/{extension_id}?ticket={ticket}'}

    def consume_view(self, extension_id, ticket):
        if not isinstance(ticket, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', ticket):
            raise ExtensionError('확장 화면 연결이 만료되었습니다. 다시 열어 주세요.', status=403)
        with self._lock:
            record = self._tickets.pop(ticket, None)
        if not record or record[0] <= self.clock() or record[1] != extension_id:
            raise ExtensionError('확장 화면 연결이 만료되었습니다. 다시 열어 주세요.', status=403)
        return record[2]

    def pick_directory(self, extension_id, picker):
        # Only a native, explicit user selection creates a directory capability.
        # This never marks a chat/workspace trusted or changes its active folder.
        self._verified(extension_id)
        with self._lock:
            if self._closing:
                raise ExtensionError('앱 종료 중에는 폴더를 선택할 수 없습니다.', status=409)
        selected = picker('folder')
        if selected.get('status') == 'cancel':
            return {'cancelled': True}
        paths = selected.get('paths')
        if selected.get('status') != 'success' or not isinstance(paths, list) or len(paths) != 1:
            raise ExtensionError('선택한 폴더를 확인하지 못했습니다. 다시 선택해 주세요.')
        path = self._directory(paths[0])
        info = path.stat()
        now, token = self.clock(), secrets.token_urlsafe(32)
        with self._lock:
            if self._closing:
                raise ExtensionError('앱 종료 중에는 폴더를 선택할 수 없습니다.', status=409)
            self._directories = {key: value for key, value in self._directories.items()
                                 if value[0] > now and value[1] != extension_id}
            if len(self._directories) >= MAX_EXTENSIONS:
                raise ExtensionError('열린 확장 화면을 정리한 뒤 다시 선택해 주세요.', status=409)
            self._directories[token] = (now + DIRECTORY_TTL, extension_id, str(path), info.st_dev, info.st_ino)
        return {'cancelled': False, 'directoryToken': token, 'path': str(path)}

    @staticmethod
    def _directory(value):
        if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
            raise ExtensionError('폴더 선택 창에서 설치할 폴더를 다시 선택해 주세요.')
        path = no_links(value)
        if not path.is_dir() or path == Path(path.anchor):
            raise ExtensionError('드라이브 전체 대신 설치할 폴더를 선택해 주세요.')
        return path

    def directory_context(self, extension_id, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            raise ExtensionError('설치 폴더 선택이 만료되었습니다. 폴더를 다시 선택해 주세요.', status=403)
        with self._lock:
            record = self._directories.get(token)
        if not record or record[0] <= self.clock() or record[1] != extension_id:
            raise ExtensionError('설치 폴더 선택이 만료되었습니다. 폴더를 다시 선택해 주세요.', status=403)
        path = self._directory(record[2])
        info = path.stat()
        if (info.st_dev, info.st_ino) != record[3:]:
            raise ExtensionError('선택한 폴더가 변경되었습니다. 다시 선택해 주세요.', status=403)
        return {'selectedDirectory': str(path), 'selectedDirectoryIdentity': [info.st_dev, info.st_ino], 'directoryToken': token}

    def action(self, extension_id, action, payload, context=None):
        if not isinstance(payload, dict):
            raise ExtensionError('확장 요청 형식을 확인해 주세요.')
        try:
            wire = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, RecursionError):
            raise ExtensionError('확장 요청 형식을 확인해 주세요.') from None
        if len(wire) > MAX_PAYLOAD:
            raise ExtensionError('확장 요청이 너무 큽니다.')
        try:
            directory, manifest = self._verified(extension_id)
        except OSError:
            raise ExtensionError('확장 설치 파일을 읽지 못했습니다.') from None
        if action not in manifest['actions']:
            raise ExtensionError('확장이 지원하지 않는 동작입니다.')
        with self._condition:
            if self._closing:
                raise ExtensionError('앱 종료 중에는 확장을 실행할 수 없습니다.', status=409)
            if extension_id in self._active:
                raise ExtensionError('확장에서 이전 요청을 처리 중입니다. 완료 후 다시 시도하세요.', code='extension_busy', status=409)
            self._active.add(extension_id)
        try:
            data = no_links(self.data_root / extension_id)
            data.mkdir(parents=True, exist_ok=True)
            request = {'protocol': HOST_API, 'action': action, 'payload': payload,
                       'dataDirectory': str(data), 'context': context or {'workspace': None}}
            result = self.runner([sys.executable, '-I', '-B', '-X', 'utf8', str(directory / manifest['entrypoint'])],
                                 timeout=ACTION_TIMEOUT, cancel_event=self._cancel, output_limit=MAX_RESULT,
                                 input=json.dumps(request, ensure_ascii=False).encode('utf-8'),
                                 capture_output=True, env=worker_environment(), cwd=str(directory),
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0:
                raise ExtensionError('확장 작업이 정상적으로 끝나지 않았습니다. 설치 상태를 확인한 뒤 다시 시도하세요.',
                                     code='extension_worker_failed', verification_required=True)
            try:
                response = json.loads(result.stdout)
            except (ValueError, UnicodeError, RecursionError):
                raise ExtensionError('확장 응답을 확인하지 못했습니다. 설치 상태를 먼저 확인하세요.', verification_required=True) from None
            if not isinstance(response, dict) or type(response.get('ok')) is not bool:
                raise ExtensionError('확장 응답 형식이 올바르지 않습니다.', verification_required=True)
            if not response['ok']:
                message = response.get('error')
                if not _label(message, 500):
                    message = '확장 요청을 완료하지 못했습니다. 연결과 설치 상태를 확인해 주세요.'
                # Never return echoed secret input, stderr or Python tracebacks.
                for key, value in payload.items():
                    if re.search(r'token|key|secret|password', key, re.I) and isinstance(value, str) and len(value) >= 8:
                        message = message.replace(value, '[숨김]')
                raise ExtensionError(message)
            return {'ok': True, 'result': response.get('result')}
        except (CancelledError, subprocess.TimeoutExpired):
            raise ExtensionError('확장 작업이 중단되었습니다. 설치 상태를 확인해 주세요. 같은 요청을 자동으로 다시 실행하지 않습니다.',
                                 code='extension_interrupted', verification_required=True) from None
        except (OSError, subprocess.SubprocessError):
            raise ExtensionError('확장 실행 결과를 확인하지 못했습니다. 설치 상태를 먼저 확인하세요.',
                                 code='extension_worker_failed', verification_required=True) from None
        finally:
            with self._condition:
                self._active.discard(extension_id)
                self._condition.notify_all()

    def close(self, timeout=5):
        with self._condition:
            self._closing = True
            self._tickets.clear()
            self._directories.clear()
            self._cancel.set()
            return self._condition.wait_for(lambda: not self._active, timeout=timeout)
