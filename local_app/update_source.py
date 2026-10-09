"""Application-bundled update source settings, independent of user credentials.

An absent file selects the public GitHub distribution. An invalid file disables
updates; it never falls back to a different server. Remote manifests cannot
modify these settings.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .history import safe

CONFIG_NAME = 'workspace-update-source.json'
MAX_CONFIG = 16 * 1024
REPOSITORY = 'bongG-moon/workspace_local'
GITHUB_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
SOURCE_ERROR = '앱에 포함된 업데이트 서버 설정을 확인하지 못했습니다. 배포 담당자에게 문의해 주세요. 기존 앱은 계속 사용할 수 있습니다.'
_VERSION = r'(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})\.(?:0|[1-9][0-9]{0,5})'


def https_parts(value):
    """Validate HTTPS while allowing company subpaths and nondefault TLS ports."""
    if (not isinstance(value, str) or not 0 < len(value) <= 8192 or '\\' in value
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError(SOURCE_ERROR)
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or '%' in parsed.netloc
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError(SOURCE_ERROR)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(SOURCE_ERROR) from exc
    return parsed


def origin(value):
    parsed = https_parts(value)
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    return 'https://' + host + (':' + str(parsed.port) if parsed.port not in (None, 443) else '')


def _base(value):
    parsed = https_parts(value)
    path = parsed.path.rstrip('/')
    if (parsed.query or not re.fullmatch(r'(?:/[A-Za-z0-9._~-]+)*', path)
            or any(part in ('.', '..') for part in path.split('/'))):
        raise ValueError(SOURCE_ERROR)
    return origin(value) + path


@dataclass(frozen=True)
class UpdateSource:
    provider: str = 'github'
    base_url: str = ''
    project_id: str = ''
    allowed_download_origins: tuple[str, ...] = ()
    error: str | None = None

    @property
    def label(self):
        return {'github': 'GitHub', 'gitlab': '사내 GitLab'}.get(self.provider, '업데이트 설정 오류')

    def public(self):
        return {'provider': self.provider, 'label': self.label}

    def as_config(self):
        if self.provider != 'gitlab' or self.error:
            raise ValueError(SOURCE_ERROR)
        return {'schema': 1, 'provider': 'gitlab', 'baseUrl': self.base_url,
                'projectId': self.project_id, 'packageName': 'company-workspace',
                'channelPackage': 'company-workspace-channel', 'channelVersion': '0.0.0',
                'allowedDownloadOrigins': list(self.allowed_download_origins)}

    @property
    def config(self):
        return self.as_config()

    @property
    def identity(self):
        if self.error:
            return 'invalid'
        config = self.as_config() if self.provider == 'gitlab' else {'provider': 'github', 'repository': REPOSITORY}
        return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @property
    def registry_root(self):
        if self.provider != 'gitlab' or self.error:
            raise ValueError(SOURCE_ERROR)
        return f'{self.base_url}/api/v4/projects/{self.project_id}/packages/generic'

    @property
    def latest_url(self):
        if self.error:
            raise ValueError(SOURCE_ERROR)
        return (self.registry_root + '/company-workspace-channel/0.0.0/latest.json'
                if self.provider == 'gitlab' else GITHUB_API)

    def asset_url(self, version, name):
        if not isinstance(version, str) or not re.fullmatch(_VERSION, version):
            raise ValueError(SOURCE_ERROR)
        names = {f'{brand}-{version}-{edition}.zip'
                 for brand in ('AX-Workspace', 'Company-Workspace') for edition in ('vbs', 'exe')}
        if name not in names | {'SHA256SUMS.txt'}:
            raise ValueError(SOURCE_ERROR)
        return f'{self.registry_root}/company-workspace/{version}/{name}'

    def validate_initial(self, value):
        https_parts(value)
        if value == self.latest_url:
            return value
        prefix = self.registry_root + '/company-workspace/'
        if not value.startswith(prefix):
            raise ValueError(SOURCE_ERROR)
        pieces = value[len(prefix):].split('/')
        if len(pieces) != 2 or self.asset_url(*pieces) != value:
            raise ValueError(SOURCE_ERROR)
        return value

    def validate_download(self, value, initial):
        """Only the exact request or an explicitly configured storage origin."""
        self.validate_initial(initial)
        https_parts(value)
        if value != initial and origin(value) not in self.allowed_download_origins:
            raise ValueError(SOURCE_ERROR)
        return value


GITHUB_SOURCE = UpdateSource()


def from_config(value):
    required = {'schema', 'provider', 'baseUrl', 'projectId', 'packageName', 'channelPackage', 'channelVersion'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {'allowedDownloadOrigins'} or type(value.get('schema')) is not int
            or value['schema'] != 1 or value['provider'] != 'gitlab'
            or value['packageName'] != 'company-workspace' or value['channelPackage'] != 'company-workspace-channel'
            or value['channelVersion'] != '0.0.0' or not isinstance(value['projectId'], str)
            or not re.fullmatch(r'[1-9][0-9]{0,19}', value['projectId'])):
        raise ValueError(SOURCE_ERROR)
    base = _base(value['baseUrl'])
    origins = value.get('allowedDownloadOrigins', [])
    if not isinstance(origins, list) or len(origins) > 12:
        raise ValueError(SOURCE_ERROR)
    clean = set()
    for url in origins:
        parsed = https_parts(url)
        if parsed.path not in ('', '/') or parsed.query:
            raise ValueError(SOURCE_ERROR)
        clean.add(origin(url))
    return UpdateSource(provider='gitlab', base_url=base, project_id=value['projectId'],
                        allowed_download_origins=tuple(sorted(clean)))


def source_from_config(value):
    """Validate publisher/build input, raising ValueError for invalid settings."""
    return from_config(value)


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(SOURCE_ERROR)
        value[key] = item
    return value


def from_bytes(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_CONFIG:
        raise ValueError(SOURCE_ERROR)
    return from_config(json.loads(data.decode('utf-8-sig'), object_pairs_hook=_unique_pairs))


def load_source(root):
    """Return valid settings or a visible fail-closed error, without any writes."""
    path = Path(root) / CONFIG_NAME
    try:
        if not safe(path).exists():
            return GITHUB_SOURCE
        with safe(path).open('rb') as stream:
            return from_bytes(stream.read(MAX_CONFIG + 1))
    except (OSError, ValueError, TypeError, RecursionError):
        return UpdateSource(provider='invalid', error=SOURCE_ERROR)
