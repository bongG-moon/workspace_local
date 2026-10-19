"""Read older internal GitLab evidence with the publisher's in-memory token.

No source-bundle notes, public releases, tag writes, or ordinary latest-file
downloads are used to reconstruct historical deployment descriptions.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re
import time
from urllib.parse import quote, quote_plus, urlencode, urlsplit

from .config import PublisherError, active, checked_version, https_base, runtime_config
from .release_history import MAX_HISTORY_BYTES, SOURCE_RANK, _entry, _json, _version
from local_app.app_updates import parse_manifest
from local_app.update_source import origin, source_from_config

MAX_SECONDS = 20
MAX_PAGES = 3
PAGE_SIZE = 30
MAX_METADATA_PAGES = 12
MAX_DOWNLOADS = 30
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
LIMIT_WARNING = '이전 이력 조회 한도(20초·목록별 3쪽·채널 파일 30개·전체 8MiB)에 도달해 확인한 기록만 반영했습니다.'
INVALID_WARNING = '이전 이력 일부의 형식·크기·프로젝트 일치를 확인하지 못해 해당 기록을 제외했습니다.'


class ImportLimit(Exception):
    pass


def _id(value):
    if type(value) is not int or not 0 < value < 2 ** 63:
        raise ValueError()
    return value


class Importer:
    def __init__(self, config, transport, headers, cancel):
        self.source = runtime_config(config)
        self.api = self.source['baseUrl'] + '/api/v4/projects/' + self.source['projectId']
        self.transport, self.headers, self.parent_cancel = transport, headers, cancel
        self.deadline = time.monotonic() + MAX_SECONDS
        self.bytes = self.pages_used = self.downloads = 0
        self.warnings, self.candidates, self.ambiguous = [], {}, set()
        self.web = ''
        self.stopped = False

    def is_set(self):
        return bool(self.parent_cancel is not None and self.parent_cancel.is_set() or time.monotonic() >= self.deadline)

    def warn(self, message):
        if message not in self.warnings and len(self.warnings) < 10:
            self.warnings.append(message)

    def excluded(self, value, reason):
        version = '확인 불가'
        if isinstance(value, dict):
            raw = value.get('version', value.get('tag_name', ''))
            try:
                version = checked_version(raw.removeprefix('v'))
            except (ValueError, TypeError, AttributeError):
                pass
        ident = value.get('id') if isinstance(value, dict) else None
        suffix = f' · 기록 ID {ident}' if type(ident) is int and 0 < ident < 2 ** 63 else ''
        self.warn(f'버전 {version}{suffix}: {reason} 해당 기록을 제외했습니다.')

    def request(self, url, *, max_bytes=MAX_HISTORY_BYTES):
        remaining = MAX_TOTAL_BYTES - self.bytes
        if self.is_set() or remaining <= 1:
            raise ImportLimit()
        active(self.parent_cancel)
        # HTTPSClient reads at most max_bytes+1 before rejecting an oversized
        # body. Reserve that whole allowance before I/O; failed reads cannot
        # silently reset the aggregate budget and trigger another large read.
        limit = min(max_bytes, remaining - 1)
        reserved = limit + 1
        self.bytes += reserved
        try:
            response = self.transport('GET', url, headers=self.headers, max_bytes=limit, cancel=self)
        except (PublisherError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            if self.bytes >= MAX_TOTAL_BYTES or self.is_set():
                raise ImportLimit() from exc
            raise
        if not isinstance(response.body, bytes) or len(response.body) > limit:
            if self.bytes >= MAX_TOTAL_BYTES:
                raise ImportLimit()
            raise ValueError()
        self.bytes -= reserved - len(response.body)
        if self.is_set():
            raise ImportLimit()
        return response

    def status(self, response, label):
        if response.status == 200:
            return True
        code = f'HTTP {response.status}' if type(response.status) is int else 'HTTP 오류'
        self.warn(label + f' 조회를 지원하지 않거나 읽기 권한이 없습니다 ({code}). 확인 가능한 이전 기록만 반영했습니다.')
        return False

    def pages(self, path, params, label):
        for page in range(1, MAX_PAGES + 1):
            if self.pages_used >= MAX_METADATA_PAGES:
                raise ImportLimit()
            self.pages_used += 1
            response = self.request(self.api + path + '?' + urlencode({**params, 'per_page': PAGE_SIZE, 'page': page}))
            if not self.status(response, label):
                return
            rows = _json(response.body)
            if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
                raise ValueError()
            yield from rows
            headers = {str(key).lower(): value for key, value in (response.headers or {}).items()}
            next_page = headers.get('x-next-page')
            if next_page == '' or next_page is None and len(rows) < PAGE_SIZE:
                return
            if next_page is not None and str(next_page) != str(page + 1):
                self.warn(INVALID_WARNING)
                return
        self.warn(LIMIT_WARNING)

    def project_matches(self, value):
        if not isinstance(value, dict):
            raise ValueError()
        if 'project_id' in value and _id(value['project_id']) != int(self.source['projectId']):
            raise ValueError('프로젝트 ID 불일치')

    def project(self):
        response = self.request(self.api)
        if not self.status(response, '프로젝트 정보'):
            return False
        value = _json(response.body)
        if not isinstance(value, dict) or _id(value.get('id')) != int(self.source['projectId']):
            raise ValueError()
        self.web = https_base(value.get('web_url'))
        if (origin(self.web) != origin(self.source['baseUrl'])
                or not urlsplit(self.web).path.startswith(urlsplit(self.source['baseUrl']).path + '/')):
            raise ValueError()
        return True

    def add(self, row):
        row = _entry(row)
        for key, token in self.headers.items():
            if str(key).upper().endswith('TOKEN') or str(key).upper() == 'AUTHORIZATION':
                if isinstance(token, str) and token and any(secret in row['title'] or secret in row['notes']
                        for secret in {token, quote(token, safe=''), quote_plus(token)}):
                    raise ValueError()
        key = row['version'], row['noteSource']
        if key in self.ambiguous:
            return
        old = self.candidates.get(key)
        if old is not None and all(old[k] == row[k] for k in ('title', 'notes')):
            # Re-publishing the same description only changes its timestamp.
            if row['publishedAt'] < old['publishedAt']:
                self.candidates[key] = row
            return
        if old is not None and old != row:
            if row['noteSource'] == 'package':
                # Multiple package IDs still prove only package presence. Use
                # the earliest observed creation date; never infer any notes.
                if datetime.fromisoformat(row['publishedAt'].replace('Z', '+00:00')) < datetime.fromisoformat(old['publishedAt'].replace('Z', '+00:00')):
                    self.candidates[key] = row
                return
            if row['noteSource'] == 'gitlab-release':
                self.ambiguous.add(key)
                self.warn('같은 버전의 GitLab 릴리스 태그가 여러 개여서 처음 확인한 설명을 유지했습니다. 중복 태그의 설명을 확인해 주세요.')
                return
            del self.candidates[key]
            self.ambiguous.add(key)
            self.warn(f"버전 {row['version']} · 배포 기록: 제목 또는 설명이 달라 최신 설명을 단정하지 않았습니다. 저장된 기록 또는 GitLab 설명을 유지합니다.")
            return
        self.candidates[key] = row

    def packages(self, name):
        seen = set()
        for value in self.pages('/packages', {'package_type': 'generic', 'package_name': name,
                                              'order_by': 'created_at', 'sort': 'desc'}, '패키지 목록'):
            reason = '프로젝트 ID 또는 응답 형식 불일치.'
            try:
                self.project_matches(value)
                if value.get('name') != name or value.get('package_type') != 'generic':
                    continue  # GitLab's package_name query is a fuzzy filter.
                if value.get('status', 'default') not in {'default', 'deprecated'}:
                    continue
                reason = '패키지 ID 형식 오류.'
                package_id = _id(value.get('id'))
                if package_id in seen:
                    continue
                seen.add(package_id)
                reason = '프로젝트 링크 불일치.'
                links = value.get('_links', {})
                expected_path = urlsplit(self.web).path + '/-/packages/' + str(package_id)
                if (not isinstance(links, dict) or 'web_path' in links and links['web_path'] != expected_path):
                    raise ValueError()
                yield value
            except (ValueError, TypeError, KeyError, UnicodeError):
                self.excluded(value, reason)

    def package_rows(self):
        for value in self.packages(self.source['packageName']):
            reason = '버전·등록일 형식 오류.'
            try:
                version = checked_version(value.get('version'))
                self.add({'version': version, 'title': 'AX Workspace ' + version, 'notes': '',
                          'publishedAt': value.get('created_at'), 'noteSource': 'package'})
            except (ValueError, TypeError, KeyError, UnicodeError):
                self.excluded(value, reason)

    def release_rows(self):
        for value in self.pages('/releases', {'order_by': 'released_at', 'sort': 'desc'}, 'GitLab 릴리스 목록'):
            reason = '프로젝트 ID 또는 응답 형식 불일치.'
            try:
                self.project_matches(value)
                tag = value.get('tag_name')
                if not isinstance(tag, str) or value.get('upcoming_release') is True:
                    continue
                try:
                    version = checked_version(tag[1:] if tag.startswith('v') else tag)
                except ValueError:
                    continue
                reason = '프로젝트 링크 불일치.'
                links = value.get('_links', {})
                if not isinstance(links, dict) or 'self' in links and links['self'] != self.web + '/-/releases/' + tag:
                    raise ValueError()
                title, notes = value.get('name'), value.get('description')
                if 'description' not in value or 'name' not in value:
                    raise ValueError()
                reason = '릴리스 제목·설명 크기·문자 또는 날짜 형식 오류.'
                self.add({'version': version, 'title': 'AX Workspace ' + version if title is None else title,
                          'notes': '' if notes is None else notes, 'publishedAt': value.get('released_at'),
                          'noteSource': 'gitlab-release'})
            except (ValueError, TypeError, KeyError, UnicodeError):
                self.excluded(value, reason)

    def retained_rows(self):
        seen = set()
        for package in self.packages(self.source['channelPackage']):
            if package.get('version') != self.source['channelVersion']:
                continue
            package_id = package['id']
            path = '/packages/' + str(package_id) + '/package_files'
            for value in self.pages(path, {'order_by': 'id', 'sort': 'desc'}, '보존된 채널 파일 목록'):
                evidence = value
                reason = '프로젝트·파일 ID 또는 상위 패키지 불일치.'
                try:
                    self.project_matches(value)
                    if value.get('file_name') != 'latest.json':
                        continue
                    file_id = _id(value.get('id'))
                    if _id(value.get('package_id')) != package_id:
                        raise ValueError()
                    key = package_id, file_id
                    if key in seen:
                        continue
                    seen.add(key)
                    reason = '파일 크기 또는 SHA256 메타데이터 형식 오류.'
                    size, digest = value.get('size'), value.get('file_sha256')
                    if (type(size) is not int or not 0 < size <= MAX_MANIFEST_BYTES
                            or not isinstance(digest, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', digest)):
                        raise ValueError()
                    if self.downloads >= MAX_DOWNLOADS:
                        raise ImportLimit()
                    self.downloads += 1
                    # File ID is essential: the ordinary Generic Package URL
                    # returns the newest same-name file, not this historical one.
                    response = self.request(self.api + path + '/' + str(file_id) + '/download', max_bytes=size)
                    if not self.status(response, '보존된 채널 파일'):
                        continue
                    reason = '다운로드 크기 또는 SHA256 검증 실패.'
                    if len(response.body) != size or hashlib.sha256(response.body).hexdigest() != digest.lower():
                        raise ValueError()
                    reason = '보존된 latest.json 형식 또는 프로젝트·파일 주소 불일치.'
                    manifest = _json(response.body)
                    if isinstance(manifest, dict):
                        evidence = {'id': file_id, 'version': manifest.get('version')}
                    parse_manifest(manifest, source_from_config(self.source))
                    self.add({key: manifest[key] for key in ('version', 'title', 'notes', 'publishedAt')}
                             | {'noteSource': 'deployment'})
                except (ValueError, TypeError, KeyError, UnicodeError):
                    self.excluded(evidence, reason)

    def phase(self, action):
        if self.stopped:
            return
        try:
            action()
        except ImportLimit:
            self.stopped = True
            self.warn(LIMIT_WARNING)
        except (PublisherError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            if self.is_set():
                self.stopped = True
                self.warn(LIMIT_WARNING)
            else:
                self.warn('이전 이력의 연결·응답을 확인하지 못했습니다. 확인한 기록을 유지하며 현재 배포 이력 저장을 계속합니다.')

    def result(self):
        chosen = {}
        for row in self.candidates.values():
            old = chosen.get(row['version'])
            if old is None or SOURCE_RANK[row['noteSource']] > SOURCE_RANK[old['noteSource']]:
                chosen[row['version']] = row
        if any(row['noteSource'] == 'package' for row in chosen.values()):
            self.warn('일부 이전 버전은 패키지 게시 정보만 확인했습니다. 당시 배포 설명을 확인할 수 없습니다.')
        return {'releases': sorted(chosen.values(), key=_version, reverse=True), 'warnings': self.warnings}


def import_history(config, *, transport, headers, token_kind, cancel=None):
    if token_kind == 'deploy':
        return {'releases': [], 'warnings': ['Deploy Token으로는 이전 GitLab 릴리스·패키지 메타데이터를 조회할 수 없습니다. 확인된 최신 채널의 이력만 저장합니다.']}
    reader = Importer(config, transport, headers, cancel)
    ready = []
    reader.phase(lambda: ready.append(reader.project()))
    if ready == [True]:
        reader.phase(reader.package_rows)
        reader.phase(reader.release_rows)
        reader.phase(reader.retained_rows)
    return reader.result()
