"""One-shot installation/update registration, separate from ordinary app use.

Private deployment settings never enter browser snapshots. A durable exclusive
claim is written before the sole network attempt, including when it later fails.
There is no scheduler, retry, telemetry observer, or normal-use network hook.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from .history import safe

CONFIG_NAME = 'workspace-registration.json'
DEFAULT_BASE_URL = 'http://pntaisquare.skhynix.com'
MAX_CONFIG = 16 * 1024
MAX_RESPONSE = 16 * 1024
REQUEST_TIMEOUT = 3.0
PATH = '/api/ax-workspace/registrations'
PROTOCOL = 'ax-registration-v1'
VERSION = re.compile(r'(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\Z')
CONFIG_ERROR = '설치 현황 집계의 사내 배포 설정을 확인해 주세요.'
EMPLOYEE_ERROR = '사번은 앞뒤 공백을 제외하고 1~8자로 입력해 주세요.'


def _version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError('앱 버전을 확인해 주세요.')
    return tuple(int(part) for part in value.split('.'))


def checked_employee(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 8:
        raise ValueError(EMPLOYEE_ERROR)
    return value.strip()


def _base_url(value):
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value) or '\\' in value):
        raise ValueError(CONFIG_ERROR)
    try:
        parsed = urlsplit(value)
        decoded = unquote(parsed.path)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or '%' in parsed.netloc
                or parsed.port is not None and not 1 <= parsed.port <= 65535
                or any(part in ('.', '..') for part in decoded.split('/'))
                or '\\' in decoded or any(ord(char) <= 32 for char in decoded)):
            raise ValueError()
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/'), '', ''))
    except (ValueError, UnicodeError):
        raise ValueError(CONFIG_ERROR) from None


def normalize(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'baseUrl', 'key'}
            or type(value.get('schema')) is not int or value['schema'] != 1):
        raise ValueError(CONFIG_ERROR)
    key = value['key']
    try:
        if (not isinstance(key, str) or not 32 <= len(key) <= 256
                or any(ord(char) < 32 or ord(char) == 127 for char in key)):
            raise ValueError()
        key.encode('utf-8')
    except (ValueError, UnicodeError):
        raise ValueError(CONFIG_ERROR) from None
    return {'schema': 1, 'baseUrl': _base_url(value['baseUrl']), 'key': key}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(CONFIG_ERROR)
        result[key] = value
    return result


def from_bytes(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CONFIG:
        raise ValueError(CONFIG_ERROR)
    try:
        return normalize(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique))
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ValueError(CONFIG_ERROR) from None


def encoded(value):
    return (json.dumps(normalize(value), ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('ascii')


def _load(path, maximum=MAX_CONFIG):
    path = safe(Path(path))
    if not path.exists():
        return None
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(CONFIG_ERROR)
    return raw


def _atomic(path, value, *, raw=False):
    path = safe(Path(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe(path.with_name('.registration-' + uuid.uuid4().hex + '.tmp'))
    data = value if raw else (json.dumps(value, ensure_ascii=True, sort_keys=True) + '\n').encode('ascii')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _send(config, payload):
    """Use the system proxy and certificate defaults, with no redirects/retry."""
    raw = json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
    canonical = '\n'.join(('AX-REGISTRATION-V1', 'POST', PATH, timestamp, nonce,
                           hashlib.sha256(raw).hexdigest()))
    signature = hmac.new(config['key'].encode('utf-8'), canonical.encode('ascii'), hashlib.sha256).hexdigest()
    request = Request(config['baseUrl'] + PATH, data=raw, method='POST', headers={
        'Content-Type': 'application/json', 'Accept': 'application/json',
        'X-AX-Timestamp': timestamp, 'X-AX-Nonce': nonce, 'X-AX-Signature': signature,
        'User-Agent': 'AX-Workspace-Registration/1',
    })
    started = time.monotonic()
    try:
        with build_opener(_NoRedirect()).open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status not in (200, 201):
                return False
            # read1 returns one available buffer instead of waiting to fill the
            # whole response limit. The elapsed bound also stops trickle bodies.
            chunks, length = [], 0
            reader = getattr(response, 'read1', response.read)
            while length <= MAX_RESPONSE and time.monotonic() - started < REQUEST_TIMEOUT:
                part = reader(min(4096, MAX_RESPONSE + 1 - length))
                if not part:
                    break
                chunks.append(part)
                length += len(part)
            else:
                return False
            value = json.loads(b''.join(chunks).decode('utf-8'))
            return (isinstance(value, dict) and value.get('service') == 'P&T AI SQUARE'
                    and value.get('protocol') == PROTOCOL and value.get('status') == 'ok'
                    and value.get('authenticated') is True and value.get('persisted') is True
                    and value.get('dryRun') is False and value.get('version') == payload['version'])
    except HTTPError as error:
        error.close()
        return False


class RegistrationManager:
    def __init__(self, state, current_version, *, application_root=None, demo=False):
        _version(current_version)
        self.state, self.version = Path(state), current_version
        self.root = self.state / 'registration'
        self._lock = threading.RLock()
        self._config = None
        self._employee = None
        self._lifecycle = {'schema': 1, 'introduced': False, 'pending': None, 'prepared': None}
        self._ready = self._closed = False
        self._worker = None
        if demo:
            return
        try:
            bundled = Path(application_root or Path(__file__).resolve().parents[1]) / CONFIG_NAME
            saved = self.state / CONFIG_NAME
            # A new explicit corporate payload can rotate a key. No config in a
            # later public payload retains the previous private deployment.
            raw = _load(bundled)
            if raw is not None:
                config = from_bytes(raw)
                if _load(saved) != encoded(config):
                    _atomic(saved, encoded(config), raw=True)
            else:
                raw = _load(saved)
                config = from_bytes(raw) if raw is not None else None
            if config is None:
                return
            profile = _load(self.root / 'profile.json')
            if profile is not None:
                value = json.loads(profile)
                if (not isinstance(value, dict) or set(value) != {'schema', 'employeeId'}
                        or type(value['schema']) is not int or value['schema'] != 1):
                    raise ValueError()
                self._employee = checked_employee(value['employeeId'])
            lifecycle = _load(self.root / 'lifecycle.json')
            if lifecycle is not None:
                self._lifecycle = self._checked_lifecycle(json.loads(lifecycle))
            self._config = config
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            # Broken private state is preserved and disables collection. It
            # cannot hold up app startup or silently reuse another destination.
            self._config = None

    @staticmethod
    def _checked_event(value):
        if not isinstance(value, dict) or set(value) - {'event', 'version', 'previousVersion'}:
            raise ValueError()
        if value.get('event') not in ('initial_install', 'app_update'):
            raise ValueError()
        _version(value.get('version'))
        if 'previousVersion' in value:
            if value['event'] != 'app_update':
                raise ValueError()
            _version(value['previousVersion'])
        return value

    @classmethod
    def _checked_lifecycle(cls, value):
        if (not isinstance(value, dict) or set(value) != {'schema', 'introduced', 'pending', 'prepared'}
                or type(value['schema']) is not int or value['schema'] != 1
                or type(value['introduced']) is not bool):
            raise ValueError()
        if value['pending'] is not None:
            cls._checked_event(value['pending'])
        prepared = value['prepared']
        if prepared is not None:
            if not isinstance(prepared, dict) or set(prepared) != {'version', 'previousVersion'}:
                raise ValueError()
            _version(prepared['version'])
            _version(prepared['previousVersion'])
        return value

    def snapshot(self):
        with self._lock:
            pending = self._lifecycle['pending']
            enabled = self._config is not None and not self._closed
            return {'enabled': enabled, 'employeeRegistered': bool(enabled and self._employee),
                    'needsEmployee': bool(enabled and self._ready and pending and not self._employee),
                    'pendingEvent': pending['event'] if enabled and pending else None}

    def save_employee(self, value):
        employee = checked_employee(value)
        with self._lock:
            if self._config is None or self._closed:
                raise ValueError(CONFIG_ERROR)
            try:
                if employee != self._employee:
                    _atomic(self.root / 'profile.json', {'schema': 1, 'employeeId': employee})
                    self._employee = employee
            except (OSError, ValueError):
                raise ValueError('사번을 저장하지 못했습니다. 이 PC의 저장 권한을 확인해 주세요.') from None
            self._dispatch()
        return self.snapshot()

    def prepare_update(self, target_version):
        if _version(target_version) <= _version(self.version):
            raise ValueError('업데이트할 새 버전을 확인해 주세요.')
        with self._lock:
            if self._config is None or self._closed:
                return
            if not self._employee:
                raise ValueError(EMPLOYEE_ERROR)
            prepared = {'version': target_version, 'previousVersion': self.version}
            if prepared != self._lifecycle['prepared']:
                self._lifecycle['prepared'] = prepared
                self._persist_lifecycle()

    def _persist_lifecycle(self):
        try:
            _atomic(self.root / 'lifecycle.json', self._lifecycle)
            return True
        except (OSError, ValueError):
            self._config = None
            return False

    def startup_ready(self, *, updated=False, previous_version=None, installer=False):
        with self._lock:
            if self._config is None or self._closed:
                return
            self._ready = True
            before = json.dumps(self._lifecycle, sort_keys=True)
            pending = self._lifecycle['pending']
            if pending and pending['version'] != self.version:
                self._lifecycle['pending'] = None
            if updated is True:
                event = {'event': 'app_update', 'version': self.version}
                prepared = self._lifecycle['prepared']
                if prepared and prepared['version'] == self.version:
                    previous_version = prepared['previousVersion']
                if previous_version is not None:
                    try:
                        if _version(previous_version) < _version(self.version):
                            event['previousVersion'] = previous_version
                    except ValueError:
                        pass
                self._lifecycle.update(introduced=True, pending=event, prepared=None)
            elif installer is True and not self._lifecycle['introduced']:
                self._lifecycle.update(introduced=True,
                    pending={'event': 'initial_install', 'version': self.version})
            if before != json.dumps(self._lifecycle, sort_keys=True) and not self._persist_lifecycle():
                return
            self._dispatch()

    def _dispatch(self):
        # Caller owns _lock. Browser status reads never enter this method.
        pending = self._lifecycle['pending']
        if (not self._ready or self._closed or self._config is None or not self._employee
                or not pending or pending['version'] != self.version):
            return
        attempt = self.root / 'attempts' / (self.version + '.json')
        payload = {'schema': 1, 'dryRun': False, 'employeeId': self._employee, **pending}
        config = dict(self._config)
        claimed = False
        try:
            safe(attempt)
            attempt.parent.mkdir(parents=True, exist_ok=True)
            # open(x) is cross-process admission, not merely an in-memory lock.
            descriptor = os.open(safe(attempt), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            claimed = True
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(b'{"schema":1,"attempted":true}\n')
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        except (OSError, ValueError):
            return
        self._lifecycle['pending'] = None
        if not self._persist_lifecycle() or not claimed:
            return
        try:
            self._worker = threading.Thread(target=self._run, args=(config, payload, attempt),
                                            name='workspace-registration', daemon=True)
            self._worker.start()
        except (RuntimeError, OSError):
            # The durable claim is deliberately retained: no later retry.
            self._worker = None

    def _run(self, config, payload, attempt):
        with self._lock:
            if self._closed:
                return
        try:
            persisted = _send(config, payload)
        except Exception:
            # No raw exceptions, server bodies, request headers or employee IDs
            # reach logs. Network/JSON/proxy failures cannot affect normal use.
            persisted = False
        try:
            _atomic(attempt, {'schema': 1, 'attempted': True, 'confirmed': persisted})
        except (OSError, ValueError):
            pass

    def close(self):
        with self._lock:
            self._closed = True
        # Never wait for a network request while closing the application.
