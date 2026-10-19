"""Best-effort records for verified deployments, separate from latest.json.

Reviewed source notes are not evidence that a version reached this GitLab.
Historical imports identify retained deployment notes, native release notes,
and package-only evidence separately. Source bundles never fill missing notes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import re
import time
from urllib.parse import quote

from .config import PublisherError, active, checked_version, https_base, runtime_config
from local_app.update_source import origin

HISTORY_NAME = 'release-history.json'
MAX_HISTORY_BYTES = 1024 * 1024
MAX_RELEASES = 30
MAX_TITLE = 300
MAX_NOTES = 32768
BASE_FIELDS = {'version', 'title', 'notes', 'publishedAt'}
FIELDS = BASE_FIELDS | {'noteSource'}
SOURCE_RANK = {'package': 0, 'deployment': 1, 'gitlab-release': 2}


class HistoryError(PublisherError):
    """Only fixed, credential-free messages may be passed to this error."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(_value):
    raise ValueError()


def _json(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_HISTORY_BYTES:
        raise ValueError()
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=_constant)


def _entry(value, *, legacy=False):
    if not isinstance(value, dict) or set(value) != (BASE_FIELDS if legacy else FIELDS):
        raise ValueError()
    source = 'deployment' if legacy else value['noteSource']
    if not isinstance(source, str) or source not in SOURCE_RANK:
        raise ValueError()
    checked_version(value['version'])
    for key, limit in (('title', MAX_TITLE), ('notes', MAX_NOTES)):
        if (not isinstance(value[key], str) or len(value[key]) > limit
                or any(ord(char) == 127 or ord(char) < 32 and char not in '\n\r\t' for char in value[key])):
            raise ValueError()
        value[key].encode('utf-8')
    if source == 'package' and (value['notes'] != '' or value['title'] != 'AX Workspace ' + value['version']):
        raise ValueError()
    date = value['publishedAt']
    if (not isinstance(date, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)', date)
            or datetime.fromisoformat(date.replace('Z', '+00:00')).utcoffset() != timedelta(0)):
        raise ValueError()
    return {**{key: value[key] for key in ('version', 'title', 'notes', 'publishedAt')}, 'noteSource': source}


def _version(entry):
    return tuple(map(int, entry['version'].split('.')))


def parse_history(raw):
    """Validate a bounded document, copying and ordering its unique entries."""
    try:
        value = _json(raw)
        if (not isinstance(value, dict) or set(value) != {'schema', 'releases'}
                or type(value['schema']) is not int or value['schema'] not in {1, 2}
                or not isinstance(value['releases'], list) or len(value['releases']) > MAX_RELEASES):
            raise ValueError()
        rows = [_entry(row, legacy=value['schema'] == 1) for row in value['releases']]
        if len({row['version'] for row in rows}) != len(rows):
            raise ValueError()
        return {'schema': 2, 'releases': sorted(rows, key=_version, reverse=True)}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise HistoryError('기존 배포 이력 형식이나 크기를 확인하지 못했습니다. 기존 이력을 덮어쓰지 않았습니다.') from exc


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _bounded(rows):
    result = {'schema': 2, 'releases': sorted(rows.values(), key=_version, reverse=True)[:MAX_RELEASES]}
    # Unicode notes can reach the document byte bound before the entry bound.
    while len(_encoded(result)) > MAX_HISTORY_BYTES and len(result['releases']) > 1:
        result['releases'].pop()
    if len(_encoded(result)) > MAX_HISTORY_BYTES:
        raise HistoryError('배포 이력이 허용 크기를 넘었습니다. 기존 이력을 유지했습니다.')
    return result


def _rows(history):
    entries = [_entry(row, legacy=history['schema'] == 1) for row in history['releases']]
    return {row['version']: row for row in entries}


def merge_history(history, manifest):
    """Current/previous verified latest wins, retaining a known deployment date."""
    row = _entry({**{key: manifest[key] for key in BASE_FIELDS}, 'noteSource': 'deployment'})
    rows = _rows(history)
    old = rows.get(row['version'])
    if old and old['noteSource'] == 'gitlab-release':
        return _bounded(rows)
    if old and old['noteSource'] == 'deployment':
        row['publishedAt'] = old['publishedAt']
    rows[row['version']] = row
    return _bounded(rows)


def merge_imported(history, imported):
    """Upgrade evidence and refresh native edits, preserving deployment notes."""
    rows, warnings = _rows(history), []
    for value in imported:
        row = _entry(value)
        old = rows.get(row['version'])
        if old is None or SOURCE_RANK[row['noteSource']] > SOURCE_RANK[old['noteSource']]:
            rows[row['version']] = row
        elif old['noteSource'] == row['noteSource'] == 'gitlab-release':
            rows[row['version']] = row
        elif old['noteSource'] == row['noteSource'] == 'deployment' and any(old[k] != row[k] for k in ('title', 'notes')):
            message = f"버전 {row['version']} · 배포 기록: 제목 또는 설명이 달라 저장된 배포 설명을 유지했습니다."
            if message not in warnings:
                warnings.append(message)
    return _bounded(rows), warnings


def _http(response, expected, context):
    if response.status not in expected:
        status = f'HTTP {response.status}' if type(response.status) is int else 'HTTP 오류'
        raise HistoryError(context + f' ({status}). 대상 프로젝트 권한과 서버 정책을 확인한 뒤 다시 게시해 주세요.')


def publish_history(config, manifest, previous, *, transport, headers, cancel=None, token_kind='access'):
    from .publish import endpoint
    from .history_import import import_history
    url = endpoint(config, manifest['version'], HISTORY_NAME, channel=True)
    response = transport('GET', url, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(response, {200, 404}, '배포 이력을 읽지 못했습니다')
    history = parse_history(response.body) if response.status == 200 else {'schema': 2, 'releases': []}
    legacy = response.status == 200 and _json(response.body)['schema'] == 1
    imported = import_history(config, transport=transport, headers=headers, token_kind=token_kind, cancel=cancel)
    candidate, conflicts = merge_imported(history, imported['releases'])
    warnings = imported['warnings'] + conflicts
    # A prior deployment may have advanced latest.json while its auxiliary
    # write failed. Recover that known deployment even when older history exists.
    candidate = merge_history(candidate, previous) if previous is not None else candidate
    merged = merge_history(candidate, manifest)
    if response.status == 200 and merged == history and not legacy:
        return {'verified': True, 'url': url, 'unchanged': True, 'warnings': warnings}
    # Generic Packages has no conditional PUT. Detect an intervening writer
    # before upload; a server-side publish lock is still needed for atomic CAS.
    latest = transport('GET', url, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    if latest.status != response.status or response.status == 200 and latest.body != response.body:
        raise HistoryError('다른 게시 작업이 배포 이력을 변경했습니다. 기존 이력을 덮어쓰지 않았습니다. 다시 게시해 주세요.')
    body = _encoded(merged)
    uploaded = transport('PUT', url, headers=headers, data=body, max_bytes=65536, cancel=cancel)
    _http(uploaded, {200, 201}, '배포 이력을 저장하지 못했습니다')
    actual = transport('GET', url, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(actual, {200}, '배포 이력의 익명 다운로드를 확인하지 못했습니다')
    if actual.body != body:
        raise HistoryError('배포 이력의 읽기 검증이 실패했습니다. release-history.json 중복 파일 정책을 확인한 뒤 다시 게시해 주세요.')
    return {'verified': True, 'url': url, 'unchanged': False, 'warnings': warnings}


def _api_json(response, context):
    try:
        result = _json(response.body)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise HistoryError(context + ' 응답 형식을 확인하지 못했습니다.') from exc


def _matches(release, expected):
    assets = release.get('assets')
    links = assets.get('links') if isinstance(assets, dict) else None
    if not isinstance(links, list) or not all(isinstance(link, dict) for link in links):
        return False
    try:
        if datetime.fromisoformat(release.get('released_at', '').replace('Z', '+00:00')) != datetime.fromisoformat(expected['released_at'].replace('Z', '+00:00')):
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    return (release.get('name') == expected['name'] and release.get('description') == expected['description']
            and sorted((str(link.get('name')), str(link.get('url'))) for link in links)
            == sorted((link['name'], link['url']) for link in expected['assets']['links']))


def _description(manifest, notice, history_url, bundled_history):
    description = manifest['notes'] + '\n\n---\n' + notice + '\n\n[게시된 버전 이력](' + history_url + ')'
    # This optional section is reviewed source history, never deployment dates
    # or development commit subjects. Invalid optional input simply omits it.
    if not isinstance(bundled_history, list) or len(bundled_history) > MAX_RELEASES:
        return description
    try:
        rows = []
        seen = set()
        for value in bundled_history:
            if not isinstance(value, dict) or set(value) != {'version', 'title', 'notes'}:
                raise ValueError()
            checked_version(value['version'])
            if value['version'] in seen:
                raise ValueError()
            seen.add(value['version'])
            for key, limit in (('title', MAX_TITLE), ('notes', MAX_NOTES)):
                if (not isinstance(value[key], str) or len(value[key]) > limit
                        or any(ord(char) == 127 or ord(char) < 32 and char not in '\n\r\t' for char in value[key])):
                    raise ValueError()
                value[key].encode('utf-8')
            if _version(value) < _version(manifest):
                rows.append(value)
        if not rows:
            return description
        section = '\n\n<details>\n<summary>이전 앱 변경 이력 (사내 배포 날짜 아님)</summary>\n'
        ending = '\n</details>'
        for row in sorted(rows, key=_version, reverse=True):
            addition = '\n### ' + row['version'] + ' · ' + row['title'] + '\n\n' + row['notes'] + '\n'
            if len((description + section + addition + ending).encode('utf-8')) > 256 * 1024:
                break
            section += addition
        return description + section + ending
    except (ValueError, TypeError, KeyError, UnicodeError):
        return description


def publish_gitlab_release(config, manifest, files, *, transport, headers, cancel=None, bundled_history=None):
    """Create only the current deployment release; never edit tags/releases."""
    from .publish import endpoint
    source = runtime_config(config)
    api = source['baseUrl'] + '/api/v4/projects/' + source['projectId']
    authenticated = {**headers, 'Content-Type': 'application/json'}
    project_response = transport('GET', api, headers=authenticated, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(project_response, {200}, 'GitLab 프로젝트 정보를 읽지 못했습니다')
    project = _api_json(project_response, 'GitLab 프로젝트')
    try:
        web = https_base(project.get('web_url'))
        if type(project.get('id')) is not int or project['id'] != int(source['projectId']) or origin(web) != origin(source['baseUrl']):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise HistoryError('GitLab 프로젝트의 HTTPS 주소와 ID를 확인하지 못했습니다.') from exc
    tag = 'v' + manifest['version']
    url = web + '/-/releases/' + tag
    history_url = endpoint(config, manifest['version'], HISTORY_NAME, channel=True)
    notice = '이 릴리스는 검증된 배포 패키지를 기록합니다. 태그가 배포 패키지 전체 소스를 나타내는 것은 아닙니다.'
    expected = {'tag_name': tag, 'name': manifest['title'],
                'description': _description(manifest, notice, history_url, None),
                'released_at': manifest['publishedAt'],
                'assets': {'links': [{'name': file['name'], 'url': endpoint(config, manifest['version'], file['name'])}
                                    for file in files] + [{'name': HISTORY_NAME, 'url': history_url}]}}
    release_url = api + '/releases/' + tag
    response = transport('GET', release_url, headers=authenticated, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(response, {200, 404}, 'GitLab 릴리스를 읽지 못했습니다')
    if response.status == 200:
        release = _api_json(response, 'GitLab 릴리스')
        if release.get('tag_name') != tag:
            raise HistoryError('GitLab 릴리스의 버전을 확인하지 못했습니다. 기존 릴리스를 유지했습니다.')
        result = {'verified': True, 'url': url, 'existing': True}
        if not _matches(release, expected):
            result['warning'] = '같은 버전의 GitLab 릴리스가 이미 있지만 설명·날짜·다운로드 링크가 다릅니다. 기존 릴리스와 태그를 유지했습니다.'
        return result
    branch = project.get('default_branch')
    if (not isinstance(branch, str) or not 0 < len(branch) <= 1024
            or any(ord(char) < 32 for char in branch)):
        raise HistoryError('GitLab 기본 브랜치가 없어 릴리스를 만들지 못했습니다. 프로젝트 소스를 먼저 게시한 뒤 다시 시도해 주세요.')
    branch_response = transport('GET', api + '/repository/branches/' + quote(branch, safe=''),
                                headers=authenticated, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(branch_response, {200}, 'GitLab 기본 브랜치 커밋을 읽지 못했습니다')
    branch_data = _api_json(branch_response, 'GitLab 기본 브랜치')
    commit = branch_data.get('commit')
    sha = commit.get('id') if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', sha):
        raise HistoryError('GitLab 기본 브랜치의 커밋 ID를 확인하지 못했습니다.')
    body = _encoded({**expected, 'ref': sha, 'tag_message': notice})
    if len(body) > MAX_HISTORY_BYTES:
        raise HistoryError('GitLab 릴리스 설명이 허용 크기를 넘었습니다.')
    created = transport('POST', api + '/releases', headers=authenticated, data=body,
                        max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(created, {200, 201}, 'GitLab 릴리스를 만들지 못했습니다')
    actual = transport('GET', release_url, headers=authenticated, max_bytes=MAX_HISTORY_BYTES, cancel=cancel)
    _http(actual, {200}, 'GitLab 릴리스 게시 결과를 확인하지 못했습니다')
    release = _api_json(actual, 'GitLab 릴리스')
    if release.get('tag_name') != tag or not _matches(release, expected):
        raise HistoryError('GitLab 릴리스 게시 내용을 확인하지 못했습니다. 기존 릴리스를 덮어쓰지 않고 다음 재시도에서 다시 확인합니다.')
    return {'verified': True, 'url': url, 'existing': False}


def publish_auxiliary(config, manifest, previous, files, *, token_kind, transport, headers, emit, cancel=None,
                      bundled_history=None):
    """An auxiliary failure cannot turn a verified package deployment into failure."""
    result = {'warnings': []}
    deadline = time.monotonic() + 60
    original_cancel, original_transport = cancel, transport

    class AuxiliaryCancel:
        def is_set(self):
            return bool(original_cancel is not None and original_cancel.is_set() or time.monotonic() >= deadline)

    cancel = AuxiliaryCancel()

    def transport(*args, **kwargs):
        active(cancel)
        response = original_transport(*args, **kwargs)
        active(cancel)
        return response

    def run(name, label, operation):
        try:
            active(cancel)
            value = operation()
            result[name] = value
            result['warnings'].extend(value.get('warnings', []))
            if value.get('warning'):
                result['warnings'].append(value['warning'])
            else:
                emit({'kind': 'info', 'message': label + ' 확인을 마쳤습니다.'})
        except HistoryError as exc:
            result[name] = {'verified': False}
            result['warnings'].append(label + ': ' + str(exc))
        except (PublisherError, OSError, ValueError, TypeError, KeyError, RecursionError):
            result[name] = {'verified': False}
            result['warnings'].append(label + ': 연결·응답 확인 또는 작업이 중단되었습니다. 배포 파일은 게시되어 있으며 다시 게시하면 추가 기록을 재시도합니다.')

    run('releaseHistory', '배포 이력', lambda: publish_history(config, manifest, previous,
        transport=transport, headers=headers, cancel=cancel, token_kind=token_kind))
    if token_kind == 'deploy':
        result['gitlabRelease'] = {'verified': False}
        result['warnings'].append('Deploy Token은 GitLab 릴리스 API를 사용할 수 없습니다. 배포 파일 게시를 유지했습니다. GitLab 릴리스도 기록하려면 api 권한의 Access Token 또는 허용된 CI Job Token으로 다시 게시해 주세요.')
    else:
        run('gitlabRelease', 'GitLab 릴리스', lambda: publish_gitlab_release(config, manifest, files,
            transport=transport, headers=headers, cancel=cancel, bundled_history=bundled_history))
    for warning in result['warnings']:
        emit({'kind': 'warning', 'message': '추가 기록 경고: ' + warning})
    return result
