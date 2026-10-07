"""Publish manifest-verified source through GitLab's atomic Commits API.

No Git checkout is required. The caller supplies a verified source ZIP directory
or an isolated export of a clean commit. Only create/update actions are used;
unrelated remote files and existing history are preserved. Tokens are transient.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile
import zlib

from local_app.update_source import https_parts, origin
from .config import PublisherError, active, checked_version, normalize, safe_path
from .publish import Response, resolve_token_kind
from . import source_archive

MAX_JSON = 8 * 1024 * 1024
MAX_SOURCE = 64 * 1024 * 1024
MAX_REQUEST = 96 * 1024 * 1024
MAX_ARCHIVE = 96 * 1024 * 1024
MAX_REMOTE_FILES = 40000
_HEX = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')


def _http_error(status):
    detail = {
        400: '파일이나 브랜치가 동시에 바뀌었거나 서버가 커밋 요청을 거절했습니다. 현재 프로젝트 상태를 확인한 뒤 다시 시도해 주세요.',
        401: 'Access Token의 종류·만료·취소 여부를 확인해 주세요.',
        403: 'Access Token의 api 범위와 대상 프로젝트·기본 브랜치의 커밋 권한을 확인해 주세요. 보호된 브랜치는 관리자 확인이 필요할 수 있습니다.',
        404: 'HTTPS 서버 주소·숫자 프로젝트 ID·프로젝트 접근 권한을 확인해 주세요.',
        409: '원격 파일이나 브랜치가 동시에 바뀌었습니다. 현재 프로젝트 상태를 확인한 뒤 다시 시도해 주세요.',
        413: '서버가 한 번에 받을 수 있는 소스 크기를 넘었습니다. 관리자에게 Commits API와 프록시의 요청 크기 제한을 확인해 주세요.',
        429: 'GitLab 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
    }.get(status, 'GitLab의 저장소 API와 사내망 연결 상태를 확인해 주세요.')
    label = f'HTTP {status}' if type(status) is int else 'HTTP 오류'
    return PublisherError(f'프로젝트 소스 작업을 완료하지 못했습니다 ({label}). {detail} 토큰과 서버 응답 본문은 저장하지 않았습니다.')


class RepositoryClient:
    """Bounded HTTPS requests restricted to one configured numeric project."""
    def __init__(self, config):
        value = normalize(config)
        self.root = value['baseUrl'] + '/api/v4/projects/' + value['projectId']

    def __call__(self, method, url, *, headers=None, data=None, max_bytes=MAX_JSON, cancel=None):
        active(cancel)
        parsed = https_parts(url)
        allowed = urlsplit(self.root)
        decoded_path = unquote(parsed.path, errors='strict')
        if (method not in {'GET', 'HEAD', 'POST'} or origin(url) != origin(self.root)
                or not (parsed.path == allowed.path or parsed.path.startswith(allowed.path + '/'))
                or any(part in ('.', '..') for part in decoded_path.split('/'))
                or '\\' in decoded_path or any(ord(c) < 32 or ord(c) == 127 for c in decoded_path)):
            raise PublisherError('소스 게시 주소가 선택한 GitLab 프로젝트와 다릅니다.')
        class NoRedirects(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise PublisherError('소스 게시 주소가 다른 위치로 이동했습니다. 토큰을 전달하지 않았습니다. HTTPS 서버 주소를 확인해 주세요.')
        request = Request(url, method=method, data=data, headers={
            'User-Agent': 'Company-Workspace-Publisher', 'Accept-Encoding': 'identity', **(headers or {})})
        deadline = time.monotonic() + 240
        try:
            try:
                response = build_opener(NoRedirects()).open(request, timeout=30)
            except HTTPError as exc:
                response = exc
            with response:
                if response.geturl() != url:
                    raise PublisherError('소스 API 응답의 최종 주소가 요청 주소와 다릅니다.')
                content = bytearray()
                while True:
                    active(cancel)
                    if time.monotonic() >= deadline:
                        raise PublisherError('소스 API 응답 대기 시간이 지났습니다. 원격 프로젝트 상태를 확인한 뒤 다시 시도해 주세요.')
                    chunk = response.read(min(65536, max_bytes + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise PublisherError('소스 API 응답이 확인 가능한 크기를 넘었습니다.')
                return Response(response.status, bytes(content), dict(response.headers))
        except PublisherError:
            raise
        except (OSError, URLError, ValueError) as exc:
            raise PublisherError('소스 API 연결을 확인하지 못했습니다. 요청 뒤 연결이 끊겼다면 원격 저장소에 반영됐을 수 있으므로 다시 확인해 주세요.') from exc


def _private_path(name):
    parts = name.casefold().split('/')
    basename = parts[-1]
    examples = {'.env.example', '.env.sample', '.env.template'}
    if (any(part in {'.aws', '.ssh', '.codex', '.claude', '.credentials'} for part in parts)
            or (basename == '.env' or basename.startswith('.env.')) and basename not in examples
            or Path(basename).suffix in {'.pem', '.pfx', '.p12', '.key', '.kdbx'}
            or basename in {'workspace-publisher.json', 'publisher-config.json', 'credentials.json', 'secrets.json',
                            '.git-credentials', '.netrc', '_netrc', '.pypirc', 'id_rsa', 'id_ed25519'}):
        raise PublisherError('소스 목록에 개인 설정·인증 정보용 파일이 있습니다. 게시용 소스에서 제외해 주세요.')


def _local(root, expected_source_id, cancel):
    root = safe_path(Path(root).absolute())
    raw, version, entries = source_archive.verified(root, cancel=cancel)
    source_id = hashlib.sha256(raw).hexdigest()
    if expected_source_id is not None and expected_source_id != source_id:
        raise PublisherError('빌드한 소스와 현재 소스 목록이 다릅니다. 같은 소스로 다시 빌드해 주세요.')
    if sum(row['size'] for row in entries.values()) + len(raw) > MAX_SOURCE:
        raise PublisherError('한 번에 게시할 수 있는 소스 크기 64 MiB를 넘었습니다.')
    content = {source_archive.MANIFEST: raw}
    for name, row in entries.items():
        active(cancel)
        _private_path(name)
        data = source_archive._read(root / name, source_archive.MAX_FILE)
        if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise PublisherError('게시 준비 중 소스 내용이 바뀌었습니다. 다시 빌드해 주세요.')
        content[name] = data
    return root, version, source_id, content


def _branch(value):
    if (not isinstance(value, str) or not 0 < len(value) <= 240 or value.startswith(('/', '-', '.'))
            or value.endswith(('/', '.', '.lock')) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '~^:?*[\\' for c in value)
            or '..' in value or '@{' in value or '//' in value):
        raise PublisherError('GitLab 기본 브랜치 이름을 안전하게 확인하지 못했습니다.')
    return value


def _sha(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise PublisherError('GitLab 커밋 식별자를 확인하지 못했습니다.')
    return value


def _blob(data, size=40):
    value = b'blob ' + str(len(data)).encode('ascii') + b'\0' + data
    return (hashlib.sha1(value) if size == 40 else hashlib.sha256(value)).hexdigest()


def _previous_manifest(raw):
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'files'}
                or type(value['schema']) is not int or value['schema'] != 1
                or not isinstance(value['files'], list) or not 0 < len(value['files']) <= source_archive.MAX_FILES):
            raise ValueError()
        version = checked_version(value['version'])
        result, folded, total = {}, set(), 0
        for row in value['files']:
            if not isinstance(row, dict) or set(row) != {'path', 'size', 'sha256'}:
                raise ValueError()
            name = source_archive.source_path(row['path'])
            _private_path(name)
            if (name.casefold() in folded or type(row['size']) is not int or not 0 <= row['size'] <= source_archive.MAX_FILE
                    or not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
                raise ValueError()
            total += row['size']
            if total > source_archive.MAX_TOTAL:
                raise ValueError()
            result[name] = row
            folded.add(name.casefold())
        if not {'.gitignore', 'local_app/server.py'} <= result.keys():
            raise ValueError()
        return version, result
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise PublisherError('원격 소스 확인 목록이 올바르지 않습니다. 기존 내용을 덮어쓰지 않았습니다. 프로젝트 담당자가 목록을 확인해 주세요.') from exc


class _API:
    def __init__(self, config, token, transport, cancel):
        self.root = config['baseUrl'] + '/api/v4/projects/' + config['projectId']
        self.token, self.transport, self.cancel = token, transport or RepositoryClient(config), cancel

    def request(self, method, path='', *, query=None, data=None, limit=MAX_JSON, missing=False):
        active(self.cancel)
        url = self.root + path + ('?' + urlencode(query) if query else '')
        response = self.transport(method, url, headers={'PRIVATE-TOKEN': self.token, 'Content-Type': 'application/json'},
                                  data=data, max_bytes=limit, cancel=self.cancel)
        if response.status == 404 and missing:
            return None
        if response.status not in ({201} if method == 'POST' else {200}):
            raise _http_error(response.status)
        if not isinstance(response.body, bytes) or len(response.body) > limit:
            raise PublisherError('소스 API 응답 크기를 확인하지 못했습니다.')
        return response

    def json(self, method, path='', **kwargs):
        response = self.request(method, path, **kwargs)
        if response is None:
            return None
        try:
            return json.loads(response.body)
        except (ValueError, UnicodeError) as exc:
            raise PublisherError('GitLab 소스 API 응답 형식을 확인하지 못했습니다.') from exc

    def head(self, branch, *, missing=False):
        value = self.json('GET', '/repository/branches/' + quote(branch, safe=''), missing=missing)
        if value is None:
            return None
        if not isinstance(value, dict) or value.get('name') != branch or not isinstance(value.get('commit'), dict):
            raise PublisherError('GitLab 기본 브랜치 상태를 확인하지 못했습니다.')
        return _sha(value['commit'].get('id'))

    def tree(self, commit):
        result, folded = {}, set()
        for page in range(1, MAX_REMOTE_FILES // 100 + 2):
            response = self.request('GET', '/repository/tree', query={'ref': commit, 'recursive': 'true', 'per_page': 100, 'page': page})
            try:
                values = json.loads(response.body)
                if not isinstance(values, list) or len(values) > 100:
                    raise ValueError()
                for row in values:
                    if not isinstance(row, dict) or row.get('type') not in {'blob', 'tree', 'commit'}:
                        raise ValueError()
                    name = row.get('path')
                    if (not isinstance(name, str) or not name or name.startswith('/') or '\\' in name
                            or any(part in ('', '.', '..') for part in name.split('/'))
                            or any(ord(c) < 32 or ord(c) == 127 for c in name) or name.casefold() in folded):
                        raise ValueError()
                    mode = row.get('mode')
                    if not isinstance(mode, str) or not re.fullmatch('[0-7]{6}', mode):
                        raise ValueError()
                    result[name] = {'id': _sha(row.get('id')), 'type': row['type'], 'mode': mode}
                    folded.add(name.casefold())
                    if len(result) > MAX_REMOTE_FILES:
                        raise ValueError()
            except (ValueError, TypeError, KeyError) as exc:
                raise PublisherError('원격 저장소 파일 목록을 안전하게 확인하지 못했습니다.') from exc
            headers = {str(key).lower(): value for key, value in (response.headers or {}).items()}
            next_page = headers.get('x-next-page')
            if next_page is not None:
                if next_page in ('', None):
                    return result
                if next_page != str(page + 1):
                    raise PublisherError('원격 저장소 파일 목록의 다음 페이지를 확인하지 못했습니다.')
            elif len(values) < 100:
                return result
        raise PublisherError('원격 저장소 파일 목록이 확인 가능한 개수를 넘었습니다.')

    def raw(self, name, commit, *, limit=source_archive.MAX_FILE):
        return self.request('GET', '/repository/files/' + quote(name, safe='') + '/raw', query={'ref': commit}, limit=limit).body

    def last_commit(self, name, commit):
        response = self.request('HEAD', '/repository/files/' + quote(name, safe=''), query={'ref': commit})
        headers = {str(key).lower(): value for key, value in (response.headers or {}).items()}
        return _sha(headers.get('x-gitlab-last-commit-id'))


def _verify_remote(api, commit, content, initial_tree):
    current = api.tree(commit)
    for name, data in content.items():
        row = current.get(name)
        if row is None or row['type'] != 'blob' or row['mode'] not in {'100644', '100755'} or row['id'] != _blob(data, len(row['id'])):
            raise PublisherError('커밋 후 소스 파일 목록이나 내용이 다릅니다. 패키지 게시를 진행하지 않았습니다.')
    for name, row in initial_tree.items():
        if row['type'] != 'tree' and name not in content and current.get(name) != row:
            raise PublisherError('커밋 후 기존 프로젝트 파일이 바뀌었습니다. 원격 저장소를 확인해 주세요.')
    data = api.request('GET', '/repository/archive.zip', query={'sha': commit}, limit=MAX_ARCHIVE).body
    try:
        found, seen, roots = set(), set(), set()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_REMOTE_FILES * 2:
                raise ValueError()
            for entry in entries:
                name = entry.filename
                parts = name.rstrip('/').split('/')
                if ('\\' in name or name.startswith('/') or any(part in ('', '.', '..') for part in parts)
                        or name in seen or any(ord(c) < 32 or ord(c) == 127 for c in name)):
                    raise ValueError()
                seen.add(name)
                roots.add(parts[0])
                relative = '/'.join(parts[1:])
                if relative not in content:
                    continue
                if (relative in found or entry.is_dir() or entry.file_size != len(content[relative])
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG) or entry.flag_bits & 1):
                    raise ValueError()
                digest, size = hashlib.sha256(), 0
                with archive.open(entry) as stream:
                    while True:
                        chunk = stream.read(256 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > len(content[relative]):
                            raise ValueError()
                        digest.update(chunk)
                if size != len(content[relative]) or digest.digest() != hashlib.sha256(content[relative]).digest():
                    raise ValueError()
                found.add(relative)
        if found != set(content) or len(roots) != 1:
            raise ValueError()
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, zlib.error) as exc:
        raise PublisherError('커밋은 생성됐을 수 있지만 원격 소스 ZIP의 파일별 SHA256 검증을 완료하지 못했습니다. 패키지 게시를 진행하지 않았습니다.') from exc


def publish_source(root, config, token, *, token_kind='auto', expected_source_id=None,
                   transport=None, cancel=None, emit=lambda event: None):
    """Create/update one atomic source commit, then verify a pinned ZIP readback.

    Returns only credential-free metadata. No delete, force, tag, branch-policy,
    identity, Git installation or personal-settings change is performed.
    """
    value = normalize(config)
    if resolve_token_kind(token, token_kind) != 'access':
        raise PublisherError('소스·구성 파일 게시에는 api 범위와 저장소 커밋 권한이 있는 Access Token이 필요합니다. Deploy Token·CI Job Token은 이 기능에서 사용하지 않습니다. 패키지만 게시하려면 소스 게시 선택을 해제해 주세요.')
    root, version, source_id, content = _local(root, expected_source_id, cancel)
    if any(token.encode('ascii') in data for data in content.values()):
        raise PublisherError('게시용 토큰이 소스 파일 내용에 포함되어 있습니다. 인증 정보를 소스에서 제거한 뒤 다시 준비해 주세요.')
    api = _API(value, token, transport, cancel)
    emit({'kind': 'progress', 'message': '프로젝트 기본 브랜치와 기존 소스 파일을 확인합니다.'})
    project = api.json('GET')
    if (not isinstance(project, dict) or str(project.get('id')) != value['projectId']
            or type(project.get('empty_repo')) is not bool or project.get('archived') is True):
        raise PublisherError('대상 프로젝트 상태를 확인하지 못했거나 보관된 프로젝트입니다.')
    empty = project['empty_repo']
    branch = _branch(project.get('default_branch') or ('main' if empty else None))
    head = api.head(branch, missing=empty)
    if empty and head is not None or not empty and head is None:
        raise PublisherError('확인 중 원격 저장소 상태가 바뀌었습니다. 다시 시도해 주세요.')
    tree = {} if empty else api.tree(head)
    previous, previous_raw = {}, None
    if source_archive.MANIFEST in tree:
        if tree[source_archive.MANIFEST]['mode'] not in {'100644', '100755'}:
            raise PublisherError('원격 소스 확인 목록이 일반 파일이 아닙니다.')
        previous_raw = api.raw(source_archive.MANIFEST, head, limit=source_archive.MAX_MANIFEST)
        if _blob(previous_raw, len(tree[source_archive.MANIFEST]['id'])) != tree[source_archive.MANIFEST]['id']:
            raise PublisherError('원격 소스 확인 목록의 커밋 내용을 검증하지 못했습니다.')
        previous_version, previous = _previous_manifest(previous_raw)
        if tuple(map(int, previous_version.split('.'))) > tuple(map(int, version.split('.'))):
            raise PublisherError('원격 소스가 더 새로운 버전입니다. 이전 소스로 덮어쓰지 않았습니다.')
    remote_files = [name for name, row in tree.items() if row['type'] != 'tree']
    initial_readme_only = (previous_raw is None and len(remote_files) == 1
                          and re.fullmatch(r'(?i:README(?:\.(?:md|txt|rst))?)', remote_files[0]) is not None)
    actions, created, updated = [], 0, 0
    folded_tree = {name.casefold(): name for name in tree}
    for name, data in sorted(content.items()):
        active(cancel)
        if folded_tree.get(name.casefold(), name) != name:
            raise PublisherError(f'Windows에서 구분할 수 없는 원격 파일 이름과 충돌합니다: {name}')
        row = tree.get(name)
        # A remote file cannot be replaced by a directory or vice versa.
        parents = name.split('/')[:-1]
        if any(folded_tree.get('/'.join(parents[:end]).casefold(), '/'.join(parents[:end])) != '/'.join(parents[:end])
               for end in range(1, len(parents) + 1)):
            raise PublisherError(f'Windows에서 구분할 수 없는 원격 폴더 이름과 충돌합니다: {name}')
        if any('/'.join(parents[:end]) in tree and tree['/'.join(parents[:end])]['type'] != 'tree'
               for end in range(1, len(parents) + 1)):
            raise PublisherError(f'원격 파일과 소스 폴더 경로가 충돌합니다: {name}')
        if row is not None:
            if row['type'] != 'blob' or row['mode'] not in {'100644', '100755'}:
                raise PublisherError(f'소스와 충돌하는 원격 항목이 일반 파일이 아닙니다: {name}')
            if row['id'] == _blob(data, len(row['id'])):
                continue
            if name == source_archive.MANIFEST:
                remote = previous_raw
            elif name in previous:
                remote = api.raw(name, head)
                old = previous[name]
                if (len(remote) != old['size'] or hashlib.sha256(remote).hexdigest() != old['sha256']
                        or _blob(remote, len(row['id'])) != row['id']):
                    raise PublisherError(f'원격 소스가 게시 후 수정되어 덮어쓰지 않았습니다: {name}')
            elif initial_readme_only and re.fullmatch(r'(?i:README(?:\.(?:md|txt|rst))?)', name):
                remote = None  # Initial project README replacement is intentional.
            else:
                raise PublisherError(f'기존 프로젝트 파일과 소스 내용이 달라 덮어쓰지 않았습니다: {name}')
            action = {'action': 'update', 'file_path': name, 'last_commit_id': api.last_commit(name, head)}
            updated += 1
        else:
            if name in previous:
                raise PublisherError(f'원격에서 삭제된 소스 파일을 자동 복구하지 않았습니다: {name}')
            action = {'action': 'create', 'file_path': name}
            created += 1
        action.update(content=base64.b64encode(data).decode('ascii'), encoding='base64')
        actions.append(action)
    # Revalidate the local source and the exact remote branch before any write.
    if source_archive.inspect(root, cancel=cancel)['sourceId'] != source_id:
        raise PublisherError('게시 준비 중 소스 확인 목록이 바뀌었습니다.')
    if api.head(branch, missing=empty) != head:
        raise PublisherError('게시 준비 중 기본 브랜치가 바뀌었습니다. 원격 내용을 덮어쓰지 않았습니다. 다시 시도해 주세요.')
    committed = head
    if actions:
        # start_sha is a new-branch base, not a compare-and-swap guard for
        # an existing one. GitLab rejects it here with force=False. Use
        # branch's current tip; keep the checked branch head above,
        # per-file last_commit_id guards, and pinned readback below instead.
        payload = {'branch': branch, 'commit_message': f'Publish Company Workspace source {version}', 'actions': actions, 'force': False}
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if len(body) > MAX_REQUEST:
            raise PublisherError('소스 커밋 요청이 한 번에 확인할 수 있는 크기를 넘었습니다.')
        emit({'kind': 'progress', 'message': f'소스·구성 파일 {len(actions)}개를 한 커밋으로 게시합니다.'})
        response = api.json('POST', '/repository/commits', data=body)
        if not isinstance(response, dict):
            raise PublisherError('커밋 응답을 확인하지 못했습니다. 원격 프로젝트 상태를 확인해 주세요.')
        committed = _sha(response.get('id'))
    emit({'kind': 'progress', 'message': '원격 소스 ZIP의 파일별 SHA256을 검증합니다.'})
    _verify_remote(api, committed, content, tree)
    if api.head(branch) != committed:
        raise PublisherError('소스 검증 중 기본 브랜치가 다시 변경되었습니다. 원격 상태를 확인한 뒤 다시 시도해 주세요.')
    preserved = sum(row['type'] != 'tree' and name not in content for name, row in tree.items())
    message = ('동일한 소스·구성 파일이 이미 있어 새 커밋 없이 검증했습니다.' if not actions
               else f'기본 브랜치에 소스·구성 파일 {len(content)}개를 게시하고 파일별 SHA256을 확인했습니다.')
    if preserved:
        message += f' 기존 프로젝트 파일 {preserved}개는 유지했습니다. 추가 파일이 있는 GitLab Download ZIP은 소스 목록 검사에서 별도 확인이 필요합니다.'
    return {'verified': True, 'unchanged': not actions, 'commit': committed, 'branch': branch,
            'created': created, 'updated': updated, 'files': len(content), 'sourceId': source_id,
            'version': version, 'preservedRemoteFiles': preserved, 'sourceDownloadReady': not preserved,
            'message': message}
