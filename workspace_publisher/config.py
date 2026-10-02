"""Local publisher preferences and the credential-free runtime source contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit, urlunsplit
import uuid


class PublisherError(ValueError):
    """A safe, user-facing error. Never include server bodies or credentials."""


DEFAULTS = {'schema': 1, 'baseUrl': '', 'projectId': '', 'remoteName': 'intranet',
            'remoteUrl': '', 'releaseTag': '', 'title': '', 'notes': '',
            'allowedDownloadOrigins': [], 'tokenKind': 'deploy'}
VERSION = re.compile(r'(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\Z')


def checked_version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise PublisherError('소스 버전은 0.23.0처럼 숫자 세 부분이어야 합니다.')
    return value


def safe_path(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise PublisherError('절대 경로인 작업 위치를 선택해 주세요.')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise PublisherError('연결된 폴더나 파일은 게시 작업 위치로 사용할 수 없습니다.')
    return path


def https_base(value, *, origin_only=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise PublisherError('GitLab HTTPS 주소를 입력해 주세요.')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or '\\' in value
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
        path = parsed.path.rstrip('/')
        decoded = unquote(path)
        if any(part in {'.', '..'} for part in decoded.split('/')) or '\\' in decoded or any(ord(c) < 32 for c in decoded):
            raise ValueError()
        if origin_only and path:
            raise ValueError()
        return urlunsplit(('https', parsed.netloc.lower(), path, '', ''))
    except (ValueError, UnicodeError) as exc:
        raise PublisherError('주소에는 HTTPS 호스트와 선택적인 포트·GitLab 하위 경로만 넣어 주세요. 계정이나 토큰은 넣지 않습니다.') from exc


def normalize(config, *, require_target=True):
    if not isinstance(config, dict) or set(config) - DEFAULTS.keys():
        raise PublisherError('게시 설정 형식이 올바르지 않습니다. 토큰은 설정 파일에 저장하지 않습니다.')
    origins = config.get('allowedDownloadOrigins', [])
    if not isinstance(origins, list) or len(origins) > 16:
        raise PublisherError('다운로드 허용 출처는 HTTPS 주소 목록으로 입력해 주세요.')
    value = {**DEFAULTS, **config, 'allowedDownloadOrigins': list(origins)}
    if type(value['schema']) is not int or value['schema'] != 1:
        raise PublisherError('지원하지 않는 게시 설정 버전입니다.')
    for key, limit in [('baseUrl', 2048), ('projectId', 20), ('remoteName', 80), ('remoteUrl', 2048),
                       ('releaseTag', 120), ('title', 300), ('notes', 32768), ('tokenKind', 20)]:
        if not isinstance(value[key], str) or len(value[key].encode('utf-8')) > limit or '\x00' in value[key]:
            raise PublisherError('게시 설정에 너무 길거나 올바르지 않은 값이 있습니다.')
    for key in ('baseUrl', 'projectId', 'remoteName', 'remoteUrl', 'releaseTag', 'title'):
        value[key] = value[key].strip()
    if require_target or value['baseUrl']:
        value['baseUrl'] = https_base(value['baseUrl'])
    if (require_target or value['projectId']) and not re.fullmatch(r'[1-9][0-9]{0,19}', value['projectId']):
        raise PublisherError('GitLab 프로젝트 ID에 숫자를 입력해 주세요.')
    if value['tokenKind'] not in {'deploy', 'job'}:
        raise PublisherError('게시 토큰 종류는 Deploy Token 또는 CI Job Token이어야 합니다.')
    value['allowedDownloadOrigins'] = list(dict.fromkeys(https_base(item, origin_only=True) for item in origins))
    return value


def runtime_config(config):
    value = normalize(config)
    parsed = urlsplit(value['baseUrl'])
    origin = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
    result = {'schema': 1, 'provider': 'gitlab', 'baseUrl': value['baseUrl'], 'projectId': value['projectId'],
            'packageName': 'company-workspace', 'channelPackage': 'company-workspace-channel',
            'channelVersion': '0.0.0',
            'allowedDownloadOrigins': list(dict.fromkeys([origin, *value['allowedDownloadOrigins']]))}
    from local_app.update_source import source_from_config
    try:
        return source_from_config(result).config
    except ValueError as exc:
        raise PublisherError('앱 업데이트 주소와 다운로드 허용 출처를 확인해 주세요. 허용 출처는 기본 서버를 포함해 최대 12개입니다.') from exc


def atomic_json(path, value):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = safe_path(path.parent / ('.publisher-' + uuid.uuid4().hex + '.tmp'))
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            safe_path(temporary).unlink()


def active(cancel):
    if cancel is not None and cancel.is_set():
        raise PublisherError('작업을 취소했습니다. 이미 게시한 버전 파일은 유지됩니다.')
