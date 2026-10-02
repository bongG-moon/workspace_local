"""Bounded, opt-in installation of this application's public GitHub releases.

Only release discovery runs automatically. Network data is never a command or
path: the installer receives an already verified application ZIP as bytes. Existing
Claude configuration and the running installation are not changed here.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile
import zlib

from .history import HistoryStore, read, safe


REPOSITORY = 'bongG-moon/workspace_local'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASE_ROOT = f'https://github.com/{REPOSITORY}/releases'
MAX_METADATA = 1024 * 1024
MAX_ARCHIVE = 80 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_MEMBERS = 500
MAX_CHECKSUMS = 64 * 1024
MAX_NOTES = 32 * 1024
CHECK_INTERVAL = 12 * 60 * 60
MANUAL_INTERVAL = 60
CHECK_DEADLINE = 30
INSTALL_DEADLINE = 240
LAUNCH_TIMEOUT = 120
HANDOFF_TIMEOUT = 2 * 60 * 60 + 180
LAUNCH_POLL_INTERVAL = 1
_VERSION = re.compile(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\Z')
_DIGEST = re.compile(r'[a-fA-F0-9]{64}\Z')
_CDN_HOSTS = {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
_MESSAGES = {
    'invalid': '업데이트 파일의 출처 또는 무결성을 확인하지 못했습니다. 기존 앱은 그대로 사용할 수 있습니다.',
    'network': '업데이트를 확인하거나 내려받지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도해 주세요.',
    'rate': 'GitHub의 요청 한도에 도달했습니다. 잠시 후 다시 확인해 주세요.',
    'install': '새 버전을 실행하지 못했습니다. 기존 앱에서 다시 시도해 주세요.',
    'cancelled': '업데이트를 중지했습니다.',
    'launch_timeout': '새 버전의 시작을 확인하지 못했습니다. 기존 앱에서 업데이트를 다시 시도해 주세요.',
    'handoff_cancelled': '버전 전환을 취소했습니다. 준비되면 업데이트를 다시 시도할 수 있습니다.',
    'handoff_expired': '버전 전환 대기 시간이 지났습니다. 기존 앱에서 업데이트를 다시 시도해 주세요.',
    'handoff_failed': '버전 전환을 마치지 못했습니다. 기존 앱에서 업데이트를 다시 시도해 주세요.',
}


class UpdateError(ValueError):
    def __init__(self, code='invalid'):
        self.code = code if code in _MESSAGES else 'invalid'
        super().__init__(_MESSAGES[self.code])


def version_tuple(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise UpdateError()
    return tuple(int(part) for part in value.split('.'))


def _plain(value, limit):
    if value is None:
        return ''
    if not isinstance(value, str):
        raise UpdateError()
    value = ''.join(c for c in value if c in '\n\t' or ord(c) >= 32 and ord(c) != 127)
    return value.encode('utf-8', errors='replace')[:limit].decode('utf-8', errors='ignore')


def _url(value):
    if not isinstance(value, str) or not 0 < len(value) <= 8192 or any(ord(c) <= 32 for c in value):
        raise UpdateError()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.port not in (None, 443)
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or '\\' in value):
            raise UpdateError()
    except (ValueError, UnicodeError) as exc:
        raise UpdateError() from exc
    return parsed


def _download_url(value, initial):
    parsed = _url(value)
    if initial == API_URL:
        if value != API_URL:
            raise UpdateError()
    elif value != initial and parsed.hostname not in _CDN_HOSTS:
        raise UpdateError()
    return value


class _Redirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def __init__(self, initial, cancel, deadline):
        self.initial, self.cancel, self.deadline = initial, cancel, deadline

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _active(self.cancel, self.deadline)
        _download_url(newurl, self.initial)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _active(cancel, deadline):
    if cancel.is_set():
        raise UpdateError('cancelled')
    if time.monotonic() >= deadline:
        raise UpdateError('network')


def github_download(url, *, limit, deadline, cancel, progress=None):
    """Anonymous HTTPS only; bound redirects, bytes, socket waits and elapsed time."""
    _active(cancel, deadline)
    parsed = _url(url)
    if url != API_URL and (parsed.hostname != 'github.com' or parsed.query
                          or not parsed.path.startswith(f'/{REPOSITORY}/releases/download/')):
        raise UpdateError()
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json' if url == API_URL else 'application/octet-stream',
        'User-Agent': 'Company-Workspace-Updater',
        'X-GitHub-Api-Version': '2022-11-28',
        'Accept-Encoding': 'identity',
    })
    opener = build_opener(_Redirects(url, cancel, deadline))
    with opener.open(request, timeout=max(.1, min(8, deadline - time.monotonic()))) as response:
        _download_url(response.geturl(), url)
        if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
            raise UpdateError()
        size = response.headers.get('Content-Length')
        if size is not None:
            if not size.isdigit() or not 0 < int(size) <= limit:
                raise UpdateError()
            size = int(size)
        result = bytearray()
        read_chunk = getattr(response, 'read1', response.read)
        while True:
            _active(cancel, deadline)
            chunk = read_chunk(min(64 * 1024, limit + 1 - len(result)))
            _active(cancel, deadline)
            if not chunk:
                break
            result.extend(chunk)
            if len(result) > limit:
                raise UpdateError()
            if progress:
                progress(len(result), size)
        if size is not None and len(result) != size:
            raise UpdateError()
        return bytes(result)


def parse_release(payload):
    """Return a small canonical GitHub payload; reject nonstable or foreign data."""
    if not isinstance(payload, dict) or payload.get('draft') is not False or payload.get('prerelease') is not False:
        raise UpdateError()
    tag = payload.get('tag_name')
    if not isinstance(tag, str) or not tag.startswith('v'):
        raise UpdateError()
    version = tag[1:]
    version_tuple(version)
    page = f'{RELEASE_ROOT}/tag/{tag}'
    if payload.get('html_url') != page:
        raise UpdateError()
    published = payload.get('published_at')
    if not isinstance(published, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', published):
        raise UpdateError()
    assets = payload.get('assets')
    if not isinstance(assets, list) or len(assets) > 100:
        raise UpdateError()
    expected = {f'Company-Workspace-{version}-vbs.zip': MAX_ARCHIVE, 'SHA256SUMS.txt': MAX_CHECKSUMS}
    selected = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise UpdateError()
        name = asset.get('name')
        if not isinstance(name, str):
            raise UpdateError()
        if name not in expected:
            continue
        size, digest = asset.get('size'), asset.get('digest')
        if (name in selected or asset.get('state') != 'uploaded'
                or type(size) is not int or not 0 < size <= expected[name]):
            raise UpdateError()
        if digest is not None and (not isinstance(digest, str) or not digest.startswith('sha256:')
                                   or not _DIGEST.fullmatch(digest[7:])):
            raise UpdateError()
        url = f'{RELEASE_ROOT}/download/{tag}/{name}'
        if asset.get('browser_download_url') != url:
            raise UpdateError()
        selected[name] = {'name': name, 'browser_download_url': url, 'size': size,
                          'state': 'uploaded', 'digest': digest.lower() if digest else None}
    if set(selected) != set(expected):
        raise UpdateError()
    return {'tag_name': tag, 'draft': False, 'prerelease': False, 'html_url': page,
            'name': _plain(payload.get('name'), 300) or f'Company Workspace {version}',
            'body': _plain(payload.get('body'), MAX_NOTES), 'published_at': published,
            'assets': [selected[name] for name in expected]}


def _checksum(data, filename):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise UpdateError() from exc
    matches = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([a-fA-F0-9]{64}) [ *]([^\r\n]+)', line)
        if not match:
            raise UpdateError()
        if match[2] == filename:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise UpdateError()
    return matches[0]


def verify_archive(data, version):
    """Verify the common application payload used by both EXE and VBS editions.

    No member is extracted here. The installer validates paths again when it
    writes an immutable application directory owned only by this app.
    """
    version_tuple(version)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_ARCHIVE:
        raise UpdateError()
    required = {'Company-Workspace.vbs', 'deploy/Start-CompanyWorkspace.ps1', 'local_app/server.py',
                'local_app/web/index.html', 'desktop/Workspace.Desktop.exe',
                'desktop/Microsoft.Web.WebView2.Core.dll', 'desktop/Microsoft.Web.WebView2.WinForms.dll',
                'desktop/WebView2Loader.dll'}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if not 0 < len(infos) <= MAX_MEMBERS:
                raise UpdateError()
            paths, files, expanded = set(), set(), 0
            for info in infos:
                name = info.filename.replace('\\', '/')
                parts = name.rstrip('/').split('/')
                if (name != info.orig_filename.replace('\\', '/') or len(name) > 240
                        or parts[0] != 'Company-Workspace' or len(parts) < 2
                        or any(not part or part in ('.', '..') or part.endswith(('.', ' '))
                               or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                               or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
                               for part in parts) or any(part.casefold() == 'runtime' for part in parts)):
                    raise UpdateError()
                key = '/'.join(parts).casefold()
                if key in paths:
                    raise UpdateError()
                paths.add(key)
                mode = stat.S_IFMT(info.external_attr >> 16)
                directory = name.endswith('/')
                if (info.flag_bits & 1 or info.external_attr & 0x400
                        or mode not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or not 0 <= info.file_size <= MAX_ARCHIVE
                        or not 0 <= info.compress_size <= len(data)
                        or directory and info.file_size != 0):
                    raise UpdateError()
                expanded += info.file_size
                if expanded > MAX_EXPANDED:
                    raise UpdateError()
                if not directory:
                    files.add('/'.join(parts[1:]))
            if not required <= files:
                raise UpdateError()
            file_keys = {'company-workspace/' + name.casefold() for name in files}
            if any('/'.join(key.split('/')[:end]) in file_keys
                   for key in paths for end in range(1, len(key.split('/')))):
                raise UpdateError()
            # Read all members in bounded chunks to check CRC without retaining
            # their expanded contents. Only the small version declaration is kept.
            server = None
            for info in infos:
                total = 0
                is_server = info.filename.replace('\\', '/') == 'Company-Workspace/local_app/server.py'
                if is_server and info.file_size > 2 * 1024 * 1024:
                    raise UpdateError()
                source = bytearray()
                with archive.open(info) as stream:
                    while True:
                        chunk = stream.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > info.file_size:
                            raise UpdateError()
                        if is_server:
                            source.extend(chunk)
                if total != info.file_size:
                    raise UpdateError()
                if is_server:
                    server = source.decode('utf-8-sig')
            if server is None or re.findall(r'^WORKSPACE_VERSION = [\"\']([^\"\']+)[\"\']\s*$', server, re.M) != [version]:
                raise UpdateError()
    except (OSError, ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as exc:
        raise UpdateError() from exc
    return data, hashlib.sha256(data).hexdigest()


class UpdateManager:
    """Small, locked public state; all network and installer work runs off-thread.

    transport(url, *, limit, deadline, cancel, progress=None) returns bytes.
    installer(version, package_bytes, sha256, *, cancel) returns a status dictionary.
    close() signals cancellation without waiting on network or disk operations.
    """
    def __init__(self, state, current_version, *, demo=False, installer=None, transport=None,
                 clock=time.time, handoff_status=None):
        version_tuple(current_version)
        self.current_version = current_version
        self.path = Path(state) / 'app-updates.json'
        self.demo, self.installer = bool(demo), installer
        self.transport, self.clock = transport or github_download, clock
        self.handoff_status = handoff_status
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._busy = False
        self._thread = None
        self._scheduler = None
        self._started = False
        self._auto = True
        self._last_checked = None
        self._metadata = None
        self._trusted = False
        self._status = 'idle'
        self._error = None
        self._progress = None
        if not self.demo:
            self._load()
        else:
            self._auto, self._status = False, 'disabled'

    def _load(self):
        try:
            if not safe(self.path).exists():
                return
            saved = read(self.path, MAX_METADATA)
            if not isinstance(saved, dict) or saved.get('schemaVersion') != 1 or type(saved.get('autoCheck')) is not bool:
                raise UpdateError()
            checked = saved.get('lastChecked')
            if checked is not None and (type(checked) not in (float, int) or not math.isfinite(checked)
                                        or checked < 0 or checked > self.clock() + 300):
                raise UpdateError()
            metadata = parse_release(saved['release']) if saved.get('release') is not None else None
            self._auto, self._last_checked, self._metadata = saved['autoCheck'], checked, metadata
            self._trusted = saved.get('verified') is True and metadata is not None
            self._status = self._resting_status()
        except (OSError, ValueError, TypeError, RecursionError):
            self._error, self._status = '업데이트 설정을 읽지 못했습니다. 다시 확인해 주세요.', 'error'

    def _persist(self):
        payload = {'schemaVersion': 1, 'autoCheck': self._auto, 'lastChecked': self._last_checked,
                   'release': self._metadata, 'verified': self._trusted}
        HistoryStore(self.path.parent)._write(self.path, json.dumps(payload, ensure_ascii=True).encode('ascii'))

    def _newer(self):
        return self._metadata is not None and version_tuple(self._metadata['tag_name'][1:]) > version_tuple(self.current_version)

    def _resting_status(self):
        if self._trusted:
            return 'available' if self._newer() else 'current'
        return 'idle' if self._auto else 'disabled'

    def _snapshot(self):
        release = None
        if self._metadata:
            data = self._metadata
            release = {'version': data['tag_name'][1:], 'title': data['name'], 'notes': data['body'],
                       'publishedAt': data['published_at'], 'url': data['html_url']}
        return {'currentVersion': self.current_version, 'status': self._status, 'autoCheck': self._auto,
                'lastChecked': self._last_checked, 'release': release, 'progress': self._progress,
                'error': self._error, 'canInstall': bool(self._trusted and self._newer() and self.installer
                    and not self.demo and not self._busy and not self._cancel.is_set()
                    and self._status in ('available', 'error'))}

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def start(self):
        with self._lock:
            if self._started:
                return self._snapshot()
            self._started = True
            if not self.demo and not self._cancel.is_set():
                self._scheduler = threading.Thread(target=self._schedule, name='workspace-update-schedule', daemon=True)
                self._scheduler.start()
        return self.check()

    def _schedule(self):
        # A tray session can stay open for weeks. Wake cheaply; check() applies
        # the persisted 12-hour TTL and the user's automatic-check preference.
        while not self._cancel.wait(15 * 60):
            self.check()

    def configure(self, auto_check):
        if type(auto_check) is not bool:
            raise ValueError('자동 확인 설정을 확인해 주세요.')
        with self._lock:
            if self.demo or self._cancel.is_set():
                return self._snapshot()
            previous = self._auto
            self._auto = auto_check
            try:
                self._persist()
            except (OSError, ValueError) as exc:
                self._auto = previous
                raise ValueError('업데이트 설정을 저장하지 못했습니다. 다시 시도해 주세요.') from exc
            if self._status in ('idle', 'disabled'):
                self._status = self._resting_status()
            return self._snapshot()

    def check(self, manual=False):
        if type(manual) is not bool:
            raise ValueError('업데이트 확인 요청을 확인해 주세요.')
        with self._lock:
            if self.demo or self._cancel.is_set() or self._busy or self._status in ('ready', 'launching'):
                return self._snapshot()
            if not manual and not self._auto:
                return self._snapshot()
            now = self.clock()
            interval = MANUAL_INTERVAL if manual else CHECK_INTERVAL
            if self._last_checked is not None and now - self._last_checked < interval:
                return self._snapshot()
            self._last_checked = now
            self._busy, self._status, self._error, self._progress = True, 'checking', None, None
            self._thread = threading.Thread(target=self._check, name='workspace-update-check', daemon=True)
            self._thread.start()
            return self._snapshot()

    def _fetch(self, url, limit, deadline, progress=None):
        _active(self._cancel, deadline)
        data = self.transport(url, limit=limit, deadline=deadline, cancel=self._cancel, progress=progress)
        _active(self._cancel, deadline)
        if not isinstance(data, bytes) or not 0 < len(data) <= limit:
            raise UpdateError()
        return data

    def _failure(self, exc, *, install=False):
        if isinstance(exc, UpdateError):
            message = str(exc)
        elif isinstance(exc, HTTPError) and exc.code in (403, 429):
            message = _MESSAGES['rate']
        else:
            message = _MESSAGES['install' if install else 'network']
        self._error, self._status, self._progress = message, 'error', None

    def _check(self):
        try:
            raw = self._fetch(API_URL, MAX_METADATA, time.monotonic() + CHECK_DEADLINE)
            try:
                metadata = parse_release(json.loads(raw.decode('utf-8')))
            except (ValueError, TypeError, RecursionError) as exc:
                raise UpdateError() from exc
            with self._lock:
                if self._cancel.is_set():
                    return
                self._metadata, self._trusted = metadata, True
                self._status = self._resting_status()
                self._persist()
        except Exception as exc:
            with self._lock:
                if not self._cancel.is_set():
                    self._trusted = False
                    self._failure(exc)
                    try:
                        self._persist()
                    except (OSError, ValueError):
                        pass
        finally:
            with self._lock:
                self._busy = False

    def install(self, version):
        version_tuple(version)
        with self._lock:
            if self._busy or self._status in ('ready', 'launching'):
                return self._snapshot()
            if (not self._snapshot()['canInstall'] or self._metadata['tag_name'] != 'v' + version):
                raise ValueError('확인된 새 버전만 설치할 수 있습니다. 업데이트를 먼저 확인해 주세요.')
            metadata = self._metadata
            self._busy, self._status, self._error, self._progress = True, 'downloading', None, 0
            self._thread = threading.Thread(target=self._install, args=(metadata,), name='workspace-update-install', daemon=True)
            self._thread.start()
            return self._snapshot()

    def _progressed(self, count, total):
        with self._lock:
            if not self._cancel.is_set():
                self._progress = min(95, max(0, int(count / total * 95))) if total else None

    def _asset(self, asset, limit, deadline, progress=None):
        data = self._fetch(asset['browser_download_url'], limit, deadline, progress)
        if len(data) != asset['size'] or (asset['digest'] is not None
                and hashlib.sha256(data).hexdigest() != asset['digest'][7:]):
            raise UpdateError()
        return data

    def _install(self, metadata):
        installer_phase = False
        try:
            deadline = time.monotonic() + INSTALL_DEADLINE
            version = metadata['tag_name'][1:]
            archive, checksums = metadata['assets']
            sums = self._asset(checksums, MAX_CHECKSUMS, deadline)
            expected = _checksum(sums, archive['name'])
            data = self._asset(archive, MAX_ARCHIVE, deadline, self._progressed)
            if hashlib.sha256(data).hexdigest() != expected:
                raise UpdateError()
            package, digest = verify_archive(data, version)
            _active(self._cancel, deadline)
            # This callback acquires the application's session lock. It must
            # never run under our lock (bootstrap holds them in the other order).
            initial = self._handoff()
            baseline = initial.get('requestId') if initial else None
            with self._lock:
                if self._cancel.is_set():
                    return
                self._status, self._progress = 'ready', 100
            installer_phase = True
            # The installer must honor this same cancellation event immediately
            # before launch; holding the UI lock over disk writes would stall quit.
            result = self.installer(version, package, digest, cancel=self._cancel)
            # A busy task may postpone handoff for hours. Release the downloaded
            # archive before waiting; the installer already owns verified files.
            del data, package, sums
            with self._lock:
                if not self._cancel.is_set():
                    status = result.get('status', result.get('state')) if isinstance(result, dict) else None
                    self._status = status if status in ('ready', 'launching') else 'launching'
            process = result.get('_process') if isinstance(result, dict) else None
            if self.handoff_status is not None or process is not None:
                self._monitor_launch(version, process, baseline)
        except Exception as exc:
            with self._lock:
                if not self._cancel.is_set():
                    self._failure(exc, install=installer_phase)
        finally:
            with self._lock:
                self._busy = False

    def _handoff(self):
        if self.handoff_status is None:
            return None
        value = self.handoff_status()
        pending = value.get('upgrade') if isinstance(value, dict) else None
        if (not isinstance(pending, dict) or not isinstance(pending.get('requestId'), str)
                or not isinstance(pending.get('targetVersion'), str)):
            return None
        return pending

    def _monitor_launch(self, version, process, baseline):
        """Observe cooperative restart; a launcher exit 0 is only a handoff ACK.

        A cancelled/expired previous attempt must not poison a retry, so require
        a new request identifier as well as the exact requested target version.
        The process is a private local handle, never part of snapshot or cache.
        """
        started = time.monotonic()
        matched = None
        while not self._cancel.is_set():
            if process is not None:
                code = process.poll()
                if code is not None and code != 0:
                    raise UpdateError('install')
            pending = self._handoff()
            if (pending and pending['targetVersion'] == version and pending['requestId'] != baseline
                    and (matched is None or matched == pending['requestId'])):
                matched = pending['requestId']
                stage = pending.get('stage')
                if stage in ('cancelled', 'expired', 'failed'):
                    raise UpdateError('handoff_' + stage)
                if stage == 'closed':
                    return
            elapsed = time.monotonic() - started
            if matched is None and elapsed >= LAUNCH_TIMEOUT:
                raise UpdateError('launch_timeout')
            if elapsed >= HANDOFF_TIMEOUT:
                raise UpdateError('handoff_expired')
            if self._cancel.wait(LAUNCH_POLL_INTERVAL):
                return

    def close(self):
        self._cancel.set()
