"""Immutable Generic Package assets, verified anonymously, then channel last.

No package is deleted. A server that refuses or hides a duplicate channel file
requires an administrator to remove that exact channel file before retrying.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from .config import PublisherError, active, checked_version, https_base, runtime_config, safe_path
from local_app.update_source import https_parts, origin, source_from_config
from local_app.app_updates import parse_manifest

MAX_FILE = 80 * 1024 * 1024
MAX_CHANNEL = 256 * 1024
MAX_RESPONSE = 64 * 1024


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict | None = None


class HTTPSClient:
    def __init__(self, allowed_origins=()):
        self.origins = {origin(https_base(value, origin_only=True)) for value in allowed_origins}

    def __call__(self, method, url, *, headers=None, data=None, max_bytes=MAX_RESPONSE, cancel=None):
        active(cancel)
        https_base(url)
        origins, deadline = self.origins, time.monotonic() + 180
        class Redirects(HTTPRedirectHandler):
            max_redirections = 5
            def redirect_request(self, request, response, code, message, response_headers, newurl):
                active(cancel)
                try:
                    # Storage services use signed query strings. They are valid
                    # only for an anonymous GET on an explicitly allowed origin.
                    https_parts(newurl)
                    redirected_origin = origin(newurl)
                except ValueError as exc:
                    raise PublisherError('다운로드 이동 주소의 HTTPS 출처를 확인하지 못했습니다.') from exc
                # Credentials never accompany redirects, even to the same host.
                if (method != 'GET' or headers and any(key.upper().endswith('TOKEN') or key.upper() == 'AUTHORIZATION' for key in headers)
                        or redirected_origin not in origins):
                    raise PublisherError('게시 주소가 다른 위치로 이동했습니다. 토큰을 전달하지 않았습니다. GitLab 주소와 다운로드 허용 출처를 확인해 주세요.')
                return super().redirect_request(request, response, code, message, response_headers, newurl)
        request = Request(url, method=method, data=data,
                          headers={'User-Agent': 'Company-Workspace-Publisher', 'Accept-Encoding': 'identity', **(headers or {})})
        try:
            try:
                response = build_opener(Redirects()).open(request, timeout=8)
            except HTTPError as exc:
                response = exc
            with response:
                body = bytearray()
                while True:
                    active(cancel)
                    if time.monotonic() >= deadline:
                        raise PublisherError('사내 서버 응답 대기 시간이 지났습니다. 연결을 확인한 뒤 다시 시도해 주세요.')
                    chunk = response.read(min(65536, max_bytes + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise PublisherError('서버 응답이 허용 크기를 넘었습니다.')
                return Response(response.status, bytes(body), dict(response.headers))
        except PublisherError:
            raise
        except (OSError, URLError, ValueError) as exc:
            raise PublisherError('사내 GitLab 연결을 확인하지 못했습니다. HTTPS 인증서·프록시·사내망 연결을 확인해 주세요.') from exc


def endpoint(config, version, name, *, channel=False):
    source = runtime_config(config)
    package = source['channelPackage'] if channel else source['packageName']
    version = source['channelVersion'] if channel else checked_version(version)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise PublisherError('게시 파일 이름을 확인하지 못했습니다.')
    return source['baseUrl'] + '/api/v4/projects/' + source['projectId'] + '/packages/generic/' + package + '/' + version + '/' + quote(name, safe='')


def _file(path):
    path = safe_path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE:
        raise PublisherError('게시 파일이 없거나 허용 크기를 넘었습니다.')
    raw = path.read_bytes()
    if len(raw) > MAX_FILE:
        raise PublisherError('게시 파일의 크기가 바뀌었습니다. 다시 빌드해 주세요.')
    return raw


def verify_files(directory, version, expected_source):
    """Recheck exact filenames, checksums, VBS contents and EXE ZIP before PUT."""
    version = checked_version(version)
    directory = safe_path(Path(directory))
    names = [f'Company-Workspace-{version}-vbs.zip', 'SHA256SUMS.txt', f'Company-Workspace-{version}-exe.zip']
    content = {name: _file(directory / name) for name in names}
    if len(content['SHA256SUMS.txt']) > 65536:
        raise PublisherError('체크섬 파일이 너무 큽니다.')
    try:
        lines = content['SHA256SUMS.txt'].decode('utf-8-sig').splitlines()
        checksums = {}
        for line in lines:
            if not line:
                continue
            match = re.fullmatch(r'([a-fA-F0-9]{64}) [ *]([^\r\n]+)', line)
            if not match or match[2] in checksums:
                raise ValueError()
            checksums[match[2]] = match[1].lower()
        if set(checksums) != {names[0], names[2]}:
            raise ValueError()
        for name, digest in checksums.items():
            if hashlib.sha256(content[name]).hexdigest() != digest:
                raise ValueError()
        # The application validator checks Windows paths, all CRCs, required
        # native files, and the source's declared version without extraction.
        from local_app.app_updates import verify_archive
        verify_archive(content[names[0]], version, source=source_from_config(expected_source))
        with zipfile.ZipFile(io.BytesIO(content[names[0]])) as archive:
            by_name = {item.filename.replace('\\', '/'): item for item in archive.infolist()}
            source = json.loads(archive.read(by_name['Company-Workspace/workspace-update-source.json']).decode('utf-8-sig'))
            if source != expected_source:
                raise ValueError()
        with zipfile.ZipFile(io.BytesIO(content[names[2]])) as archive:
            exe = f'Company-Workspace-{version}.exe'
            if set(archive.namelist()) != {exe, exe + '.sha256', 'README.txt'} or len(archive.infolist()) != 3:
                raise ValueError()
            if any(item.file_size > MAX_FILE or item.flag_bits & 1 for item in archive.infolist()):
                raise ValueError()
            executable = archive.read(exe)
            if not executable.startswith(b'MZ'):
                raise ValueError()
            checksum = archive.read(exe + '.sha256').decode('utf-8-sig').strip()
            if checksum != hashlib.sha256(executable).hexdigest() + '  ' + exe:
                raise ValueError()
    except (ValueError, KeyError, OSError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PublisherError('배포 파일의 버전·체크섬·구성 검사가 실패했습니다. 같은 설정으로 다시 빌드해 주세요.') from exc
    return [{'name': name, 'path': str(directory / name), 'size': len(content[name]),
             'sha256': hashlib.sha256(content[name]).hexdigest()} for name in names]


def connection(config, *, transport=None, cancel=None):
    source = runtime_config(config)
    transport = transport or HTTPSClient(source['allowedDownloadOrigins'])
    response = transport('GET', endpoint(config, '0.0.0', 'latest.json', channel=True), max_bytes=MAX_CHANNEL, cancel=cancel)
    if response.status == 404:
        return {'ok': True, 'published': False, 'message': 'GitLab에 연결했습니다. 아직 채널 파일이 없거나 익명 읽기가 허용되지 않았습니다. 게시 단계에서 익명 다운로드를 반드시 확인합니다.'}
    if response.status != 200:
        raise PublisherError('앱에서 인증 없이 업데이트를 읽을 수 없습니다. GitLab 패키지의 익명 읽기 권한을 확인해 주세요.')
    try:
        value = json.loads(response.body)
        parse_manifest(value, source_from_config(source))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise PublisherError('채널 주소에서 정상적인 업데이트 안내를 읽지 못했습니다.') from exc
    return {'ok': True, 'published': True, 'message': '앱과 같은 익명 읽기 방식으로 기존 업데이트 채널을 확인했습니다.'}


def publish(config, result, token, *, token_kind='deploy', transport=None, emit=lambda event: None, cancel=None):
    active(cancel)
    source = runtime_config(config)
    if token_kind not in {'deploy', 'job'} or not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise PublisherError('게시용 Deploy Token 또는 CI Job Token을 입력해 주세요. 토큰은 저장하지 않습니다.')
    version = checked_version(result.get('version'))
    files = verify_files(Path(result['directory']), version, source)
    expected = [{key: row[key] for key in ('name', 'size', 'sha256')} for row in result.get('files', [])]
    if expected != [{key: row[key] for key in ('name', 'size', 'sha256')} for row in files]:
        raise PublisherError('빌드 이후 배포 파일이 바뀌었습니다. 다시 빌드한 결과를 게시해 주세요.')
    transport = transport or HTTPSClient(source['allowedDownloadOrigins'])
    header = {'DEPLOY-TOKEN' if token_kind == 'deploy' else 'JOB-TOKEN': token,
              'Content-Type': 'application/octet-stream'}
    for file in files:
        active(cancel)
        url = endpoint(config, version, file['name'])
        existing = transport('GET', url, max_bytes=MAX_FILE, cancel=cancel)
        if existing.status == 200:
            if len(existing.body) != file['size'] or hashlib.sha256(existing.body).hexdigest() != file['sha256']:
                raise PublisherError('같은 버전에 다른 파일이 이미 있습니다. 기존 버전을 덮어쓰지 않았습니다. 새 버전으로 빌드해 주세요.')
            emit({'kind': 'info', 'message': file['name'] + ': 같은 파일이 이미 있어 재사용합니다.'})
            continue
        if existing.status != 404:
            raise PublisherError('패키지의 익명 읽기 권한을 확인하지 못했습니다. 공개 읽기와 게시 토큰 권한을 확인해 주세요.')
        emit({'kind': 'progress', 'message': file['name'] + ' 게시 중'})
        raw = _file(Path(file['path']))
        if hashlib.sha256(raw).hexdigest() != file['sha256']:
            raise PublisherError('게시 직전에 파일이 바뀌었습니다. 다시 빌드해 주세요.')
        uploaded = transport('PUT', url, headers=header, data=raw, max_bytes=MAX_RESPONSE, cancel=cancel)
        if uploaded.status not in {200, 201}:
            raise PublisherError('버전 파일을 게시하지 못했습니다. 토큰의 write_package_registry 권한과 패키지 정책을 확인해 주세요. 채널은 변경하지 않았습니다.')
        verified = transport('GET', url, max_bytes=MAX_FILE, cancel=cancel)
        if verified.status != 200 or len(verified.body) != file['size'] or hashlib.sha256(verified.body).hexdigest() != file['sha256']:
            raise PublisherError('게시한 버전 파일을 앱과 같은 익명 다운로드로 검증하지 못했습니다. 채널은 변경하지 않았습니다.')
    active(cancel)
    manifest = {'schema': 1, 'channel': 'stable', 'version': version,
                'publishedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'title': config.get('title') or f'Company Workspace {version}', 'notes': config.get('notes', ''),
                'files': [{key: file[key] for key in ('name', 'size', 'sha256')} for file in files]}
    try:
        parse_manifest(manifest, source_from_config(source))
    except ValueError as exc:
        raise PublisherError('앱이 읽을 수 있는 업데이트 안내를 만들지 못했습니다. 배포 파일과 게시 설정을 확인해 주세요.') from exc
    channel_url = endpoint(config, version, 'latest.json', channel=True)
    previous = transport('GET', channel_url, max_bytes=MAX_CHANNEL, cancel=cancel)
    if previous.status not in {200, 404}:
        raise PublisherError('기존 채널을 확인하지 못했습니다. 게시한 버전 파일은 유지했고 채널은 변경하지 않았습니다.')
    if previous.status == 200:
        try:
            old = json.loads(previous.body)
            parse_manifest(old, source_from_config(source))
            if tuple(map(int, checked_version(old['version']).split('.'))) > tuple(map(int, version.split('.'))):
                raise PublisherError('채널이 더 새로운 버전을 가리킵니다. 이전 버전으로 되돌리지 않았습니다.')
            if {key: old.get(key) for key in ('schema', 'channel', 'version', 'title', 'notes', 'files')} == {key: manifest[key] for key in ('schema', 'channel', 'version', 'title', 'notes', 'files')}:
                return {'version': version, 'url': channel_url, 'verified': True, 'message': '같은 버전 파일과 업데이트 채널이 이미 게시되어 있습니다.'}
        except PublisherError:
            raise
        except (ValueError, KeyError, TypeError):
            raise PublisherError('기존 채널 형식이 올바르지 않습니다. 관리자가 해당 채널 파일을 확인한 뒤 다시 게시해 주세요.')
        # Credential-free recovery evidence stays beside the owned build.
        backup = safe_path(Path(result['directory']) / 'previous-latest.json')
        if not backup.exists():
            backup.write_bytes(previous.body)
    body = json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_CHANNEL:
        raise PublisherError('업데이트 설명이 너무 깁니다. 내용을 줄여 주세요.')
    emit({'kind': 'progress', 'message': '모든 버전 파일을 검증했습니다. 마지막으로 업데이트 채널을 게시합니다.'})
    uploaded = transport('PUT', channel_url, headers=header, data=body, max_bytes=MAX_RESPONSE, cancel=cancel)
    if uploaded.status not in {200, 201}:
        raise PublisherError('채널 파일 갱신이 거절되었습니다. 관리자에게 company-workspace-channel / 0.0.0 / latest.json만 정리해 달라고 요청한 뒤 재시도하세요. 버전 파일은 삭제하지 않습니다.')
    actual = transport('GET', channel_url, max_bytes=MAX_CHANNEL, cancel=cancel)
    if actual.status != 200 or actual.body != body:
        raise PublisherError('채널을 게시했지만 앱이 읽는 내용이 새 내용과 다릅니다. 관리자가 company-workspace-channel / 0.0.0 / latest.json의 중복 파일만 정리한 뒤 재시도하세요. 성공으로 처리하지 않았습니다.')
    return {'version': version, 'url': channel_url, 'verified': True,
            'message': '버전 파일과 최신 채널의 익명 다운로드·체크섬 확인을 마쳤습니다. 사내 사용자 PC에서도 최초 실행을 확인해 주세요.'}
