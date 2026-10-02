"""Explicit preparation of the pinned WebView2 build SDK.

Source archives contain only the lock file. This module never downloads during
inspection or an application build, and never persists a download URL or token.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from .config import PublisherError, safe_path


MAX_SDK_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
DOWNLOAD_TIMEOUT = 300
_READY = 'WebView2 빌드 SDK가 준비되었습니다. 지정된 버전과 체크섬을 확인했습니다.'
_MISSING = ('WebView2 빌드 SDK가 없습니다. 사내에서 승인된 SDK 파일을 선택하거나 '
            '다운로드 버튼으로 준비해 주세요. 소스 ZIP에는 실행 파일을 넣지 않습니다.')


def _active(cancel):
    if cancel is not None and cancel.is_set():
        raise PublisherError('SDK 준비를 취소했습니다. 기존 SDK와 소스 파일은 유지됩니다.')


def _url(value):
    """Credential-free HTTPS only, including all redirects."""
    try:
        if (not isinstance(value, str) or not value or len(value) > 4096
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or '\\' in value):
            raise ValueError()
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or parts.port is not None and not 1 <= parts.port <= 65535):
            raise ValueError()
        return urlunsplit(('https', parts.netloc.lower(), parts.path, '', ''))
    except (ValueError, UnicodeError) as exc:
        raise PublisherError('SDK 주소는 계정·토큰·쿼리 문자열이 없는 HTTPS 파일 주소로 입력해 주세요.') from exc


def _origin(url):
    parts = urlsplit(_url(url))
    return (parts.hostname.lower(), parts.port or 443)


def _lock(repo_root):
    root = safe_path(Path(repo_root).absolute())
    try:
        path = safe_path(root / 'deploy/WebView2.lock.json')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 32768:
            raise ValueError()
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        version, digest = value['version'], value['sha256']
        if (value['package'] != 'Microsoft.Web.WebView2'
                or not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', version)
                or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError()
        url = _url(value['url'])
        expected = ('https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/'
                    + version + '/microsoft.web.webview2.' + version + '.nupkg')
        if url != expected:
            raise ValueError()
        return {'version': version, 'sha256': digest, 'url': url,
                'path': str(safe_path(root / 'build/desktop-sdk' / (version + '.nupkg')))}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise PublisherError('소스의 WebView2 버전·체크섬 정보를 확인하지 못했습니다. 정상적인 소스 ZIP을 다시 준비해 주세요.') from exc


def _digest(path):
    path = safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size < MAX_SDK_BYTES:
        raise ValueError()
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK_BYTES):
            size += len(chunk)
            if size >= MAX_SDK_BYTES:
                raise ValueError()
            digest.update(chunk)
    if size != info.st_size:
        raise ValueError()
    return digest.hexdigest()


def _inspect(lock):
    path = Path(lock['path'])
    try:
        safe_path(path)
        if not path.exists():
            return {**lock, 'status': 'missing', 'message': _MISSING}
        if _digest(path) != lock['sha256']:
            raise ValueError()
        return {**lock, 'status': 'ready', 'message': _READY}
    except (OSError, ValueError):
        return {**lock, 'status': 'invalid', 'message':
                '준비된 SDK의 체크섬을 확인하지 못했습니다. 승인된 파일을 다시 선택하거나 다운로드해 주세요.'}


def inspect_sdk(repo_root):
    """Read-only; returns ready, missing or invalid. No directory is created."""
    return _inspect(_lock(repo_root))


def _notify(emit, message):
    if emit is not None:
        emit({'kind': 'progress', 'message': message})


def _temporary(lock):
    target = safe_path(Path(lock['path']))
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_path(target.parent)
    return safe_path(target.parent / ('.sdk-' + uuid.uuid4().hex + '.tmp'))


def _copy_verified(incoming, outgoing, lock, *, cancel=None, deadline=None):
    digest, size = hashlib.sha256(), 0
    while True:
        _active(cancel)
        if deadline is not None and time.monotonic() >= deadline:
            raise PublisherError('SDK 다운로드 시간이 초과되었습니다. 사내 연결을 확인하거나 승인된 SDK 파일을 선택해 주세요.')
        chunk = incoming.read(CHUNK_BYTES)
        if not chunk:
            break
        size += len(chunk)
        if size >= MAX_SDK_BYTES:
            raise PublisherError('SDK 파일이 허용 크기를 넘었습니다. 원본 파일과 배포 주소를 확인해 주세요.')
        digest.update(chunk)
        outgoing.write(chunk)
    _active(cancel)
    if not size or digest.hexdigest() != lock['sha256']:
        raise PublisherError('SDK 체크섬이 소스에 지정된 값과 다릅니다. 기존 파일을 교체하지 않았습니다. 정확한 버전의 원본 SDK를 준비해 주세요.')
    outgoing.flush()
    os.fsync(outgoing.fileno())


def _commit(temporary, lock, cancel):
    _active(cancel)
    target = safe_path(Path(lock['path']))
    # Another explicit operation may have prepared the same SDK in the meantime.
    if _inspect(lock)['status'] != 'ready':
        os.replace(safe_path(temporary), target)
    return {**lock, 'status': 'ready', 'message': _READY}


def _cleanup(temporary):
    if temporary is not None and temporary.exists():
        safe_path(temporary).unlink()


def import_sdk(repo_root, path, *, cancel=None, emit=None):
    """Verify a locally approved nupkg and atomically cache it. Input is untouched."""
    _active(cancel)
    lock = _lock(repo_root)
    ready = _inspect(lock)
    if ready['status'] == 'ready':
        _notify(emit, '이미 검증된 SDK를 사용합니다. 기존 파일을 교체하지 않았습니다.')
        return ready
    temporary = None
    try:
        original = safe_path(Path(path).absolute())
        info = original.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size < MAX_SDK_BYTES:
            raise PublisherError('승인된 WebView2 .nupkg 파일을 선택해 주세요. 파일이 없거나 크기가 올바르지 않습니다.')
        temporary = _temporary(lock)
        _notify(emit, '선택한 SDK의 버전과 체크섬을 확인합니다. 원본 파일은 변경하지 않습니다.')
        with original.open('rb') as incoming, temporary.open('xb') as outgoing:
            _copy_verified(incoming, outgoing, lock, cancel=cancel)
        result = _commit(temporary, lock, cancel)
        _notify(emit, _READY)
        return result
    except PublisherError:
        raise
    except (OSError, ValueError) as exc:
        raise PublisherError('SDK 파일을 준비하지 못했습니다. 파일 경로와 읽기·쓰기 권한을 확인해 주세요.') from exc
    finally:
        _cleanup(temporary)


class _SDKRedirects(HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, origins, cancel):
        self.origins = set(origins)
        self.cancel = cancel

    def redirect_request(self, request, response, code, message, headers, newurl):
        _active(self.cancel)
        if _origin(newurl) not in self.origins:
            raise PublisherError('SDK 다운로드가 다른 서버로 이동했습니다. 승인된 최종 HTTPS 파일 주소를 직접 입력해 주세요.')
        return super().redirect_request(request, response, code, message, headers, _url(newurl))


def download_sdk(repo_root, url=None, *, cancel=None, emit=None):
    """Download only after an explicit user action; no URL or secret is saved."""
    _active(cancel)
    lock = _lock(repo_root)
    address = _url(lock['url'] if url is None or url == '' else url)
    ready = _inspect(lock)
    if ready['status'] == 'ready':
        _notify(emit, '이미 검증된 SDK를 사용합니다. 네트워크에 연결하지 않았습니다.')
        return ready
    origins = {_origin(address)}
    if address == lock['url']:
        origins.add(('globalcdn.nuget.org', 443))
    temporary = None
    try:
        _notify(emit, '지정된 HTTPS 서버에서 빌드 SDK를 받습니다. 받은 뒤 체크섬을 확인합니다.')
        request = Request(address, headers={'User-Agent': 'Company-Workspace-Publisher',
                                           'Accept-Encoding': 'identity'})
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT
        with build_opener(_SDKRedirects(origins, cancel)).open(request, timeout=8) as response:
            if response.status != 200 or _origin(response.geturl()) not in origins:
                raise PublisherError('SDK 서버가 정상 파일을 반환하지 않았습니다. 승인된 최종 다운로드 주소를 확인해 주세요.')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or not 0 < int(length) < MAX_SDK_BYTES):
                raise PublisherError('SDK 서버가 반환한 파일 크기가 올바르지 않습니다.')
            temporary = _temporary(lock)
            with temporary.open('xb') as outgoing:
                _copy_verified(response, outgoing, lock, cancel=cancel, deadline=deadline)
        result = _commit(temporary, lock, cancel)
        _notify(emit, _READY)
        return result
    except PublisherError:
        raise
    except (OSError, HTTPError, URLError, ValueError) as exc:
        raise PublisherError('SDK를 받지 못했습니다. 사내 HTTPS 인증서·프록시·접속 허용을 확인하거나 승인된 SDK 파일을 선택해 주세요. 보안 설정은 변경하지 않았습니다.') from exc
    finally:
        _cleanup(temporary)
