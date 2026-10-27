"""Non-secret, application-bundled LLM usage connection settings."""
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

CONFIG_NAME = 'workspace-llm-usage.json'
MAX_CONFIG = 8192


def checked_url(value):
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value):
        raise ValueError('사용량 MCP의 HTTP 또는 HTTPS 주소를 입력해 주세요.')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or '%' in parsed.netloc
                or parsed.port is not None and not 1 <= parsed.port <= 65535
                or any(p in ('.', '..') for p in unquote(parsed.path).split('/'))):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('주소에는 서버와 MCP 경로만 입력하세요. KEY나 계정 정보는 넣지 않습니다.') from None
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or '/', '', ''))


def normalize(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'mcpUrl'}
            or type(value.get('schema')) is not int or value['schema'] != 1):
        raise ValueError('사용량 연결 설정 형식을 확인해 주세요. 주소만 저장할 수 있습니다.')
    return {'schema': 1, 'mcpUrl': checked_url(value['mcpUrl'])}


def from_bytes(raw, *, test=False):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_CONFIG:
        raise ValueError('사용량 연결 설정 파일을 확인해 주세요.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('사용량 연결 설정에 중복 항목이 있습니다.')
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    return normalize_test(value) if test else normalize(value)


def normalize_test(value):
    if not isinstance(value, dict):
        raise ValueError('사용량 연결 설정을 확인해 주세요.')
    mode = value.get('networkMode', 'system')
    if mode not in ('system', 'direct'):
        raise ValueError('연결 방식을 다시 선택해 주세요.')
    return {**normalize({k: v for k, v in value.items() if k != 'networkMode'}), 'networkMode': mode}


def load(path, *, test=False):
    path = Path(path)
    if not path.exists():
        return None
    with path.open('rb') as stream:
        return from_bytes(stream.read(MAX_CONFIG + 1), test=test)


def encoded(value, *, test=False):
    normalized = normalize_test(value) if test else normalize(value)
    return (json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
