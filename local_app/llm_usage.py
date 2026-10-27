"""Optional, bounded team-budget observation. Never participates in task execution."""
import json
import math
from email.utils import parsedate_to_datetime
import os
from pathlib import Path
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import llm_usage_config

MAX_BODY = 512 * 1024
REFRESH_SECONDS = 300
FAILURE_REFRESH_SECONDS = 60
RETRY_DELAYS = (2, 5)
MAX_RETRY_WAIT = 15


class UsageError(Exception):
    def __init__(self, code, *, stage='', http_status=None, network_path=None, retry_after=None):
        super().__init__(code)
        self.stage, self.http_status = stage, http_status
        self.network_path = network_path
        self.retry_after = retry_after

    def public(self):
        result = {'stage': self.stage}
        if type(self.http_status) is int:
            result['httpStatus'] = self.http_status
        if self.network_path in ('proxy', 'direct'):
            result['networkPath'] = self.network_path
        return result


def retry_after_seconds(value):
    """Keep only a bounded delay; never retain raw response headers."""
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        delay = int(value) if value.isascii() and value.isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
        return min(86400, max(0, math.ceil(delay)))
    except (ValueError, TypeError, OverflowError):
        return None


def is_transient(error):
    return isinstance(error, UsageError) and (
        str(error) in ('timeout', 'connection_error') or
        str(error) == 'http_error' and error.http_status in (502, 503, 504))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise UsageError('redirect_blocked')


class ObservedProxyHandler(urllib.request.ProxyHandler):
    """Report only whether routing used a proxy, never its URL/credentials."""
    def proxy_open(self, req, proxy, scheme):
        try:
            return super().proxy_open(req, proxy, scheme)
        finally:
            req._llm_proxy_applied = req.has_proxy() or bool(getattr(req, '_tunnel_host', None))


def resolve_token(env, home):
    for key in ('SK_BUDGET_HUD_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'):
        if isinstance(env.get(key), str) and env[key].strip():
            return env[key].strip()
    try:
        config = Path(env.get('CLAUDE_CONFIG_DIR') or Path(home) / '.claude').expanduser()
        path = config / 'settings.json'
        if path.stat().st_size <= MAX_BODY:
            settings = json.loads(path.read_text(encoding='utf-8-sig')).get('env', {})
            for key in ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'):
                token = settings.get(key)
                if isinstance(token, str) and token.strip():
                    return token.strip()
    except (OSError, ValueError, AttributeError):
        pass
    return env.get('OPENAI_API_KEY', '').strip()


def parse_response(raw, request_id):
    """Match the response id, ignoring SSE keepalives and server notifications."""
    try:
        text = raw.decode('utf-8-sig')
        if text.lstrip().startswith('{'):
            candidates = [json.loads(text)]
        else:
            candidates, data = [], []
            for line in (text.replace('\r\n', '\n') + '\n\n').split('\n'):
                if line.startswith('data:'):
                    data.append(line[5:].lstrip(' '))
                elif not line and data:
                    value = '\n'.join(data)
                    data = []
                    if value != '[DONE]':
                        candidates.append(json.loads(value))
        response = next(row for row in candidates if isinstance(row, dict) and row.get('id') == request_id)
        if 'error' in response:
            raise UsageError('server_error')
        return response['result']
    except (ValueError, KeyError, StopIteration, UnicodeError):
        raise UsageError('bad_payload') from None


def query_usage(env=None, home=None, team='', *, timeout=9, opener=None, network_mode='system'):
    env = os.environ if env is None else env
    url = env.get('LLMOPS_MCP_URL', '').strip()
    try:
        url = llm_usage_config.checked_url(url)
    except ValueError:
        raise UsageError('not_configured', stage='configuration') from None
    token = resolve_token(env, home or Path.home())
    if not token or '\r' in token or '\n' in token:
        raise UsageError('key_required', stage='credentials')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'Authorization': 'Bearer ' + token}
    if network_mode not in ('system', 'direct'):
        raise UsageError('not_configured', stage='configuration')
    observe_route = opener is None
    # Explicit choice is available only in the separate connection test app.
    # Do not silently retry 503s over another route or change OS proxy settings.
    opener = opener or urllib.request.build_opener(ObservedProxyHandler({} if network_mode == 'direct' else None), NoRedirect())
    deadline = time.monotonic() + timeout
    last_path = None

    def request(method, params, request_id=None):
        nonlocal last_path
        stage = params.get('name', method)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UsageError('timeout', stage=stage)
        body = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if request_id is not None:
            body['id'] = request_id
        req = urllib.request.Request(url, json.dumps(body).encode(), headers, method='POST')
        try:
            with opener.open(req, timeout=remaining) as response:
                if method == 'initialize':
                    session = response.headers.get('mcp-session-id')
                    if not session or len(session) > 1024 or '\r' in session or '\n' in session:
                        raise UsageError('no_session')
                    headers['Mcp-Session-Id'] = session
                if request_id is None:
                    return None
                # Read finite chunks and stop at the matching SSE response; some
                # MCP servers keep the event stream open after delivering it.
                raw = bytearray()
                while len(raw) <= MAX_BODY:
                    if time.monotonic() >= deadline:
                        raise UsageError('timeout')
                    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                    if sock is not None:
                        sock.settimeout(max(.01, deadline - time.monotonic()))
                    chunk = response.read1(min(8192, MAX_BODY + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > MAX_BODY:
                        raise UsageError('bad_payload')
                    try:
                        return parse_response(bytes(raw), request_id)
                    except UsageError as exc:
                        if str(exc) != 'bad_payload':
                            raise
                return parse_response(bytes(raw), request_id)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = retry_after_seconds(exc.headers.get('Retry-After')) if exc.headers else None
            exc.close()
            reason = ('key_required' if status in (401, 403) else 'proxy_auth_required' if status == 407
                      else 'rate_limited' if status == 429 else 'http_error')
            raise UsageError(reason, stage=stage, http_status=status, retry_after=retry_after,
                             network_path=('proxy' if getattr(req, '_llm_proxy_applied', False) else 'direct') if observe_route else None) from None
        except UsageError as exc:
            exc.stage = stage
            if observe_route:
                exc.network_path = 'proxy' if getattr(req, '_llm_proxy_applied', False) else 'direct'
            raise
        except (OSError, ValueError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            code = ('dns_error' if isinstance(reason, socket.gaierror) else
                    'timeout' if isinstance(reason, TimeoutError) else
                    'tls_error' if isinstance(reason, ssl.SSLError) else 'connection_error')
            raise UsageError(code, stage=stage,
                             network_path=('proxy' if getattr(req, '_llm_proxy_applied', False) else 'direct') if observe_route else None) from None
        finally:
            if observe_route:
                last_path = 'proxy' if getattr(req, '_llm_proxy_applied', False) else 'direct'

    initialized = request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                          'clientInfo': {'name': 'ax-workspace-usage', 'version': '1.0'}}, 0)
    if isinstance(initialized, dict) and initialized.get('protocolVersion'):
        headers['MCP-Protocol-Version'] = str(initialized['protocolVersion'])
    request('notifications/initialized', {})

    def call(name, args, number):
        result = request('tools/call', {'name': name, 'arguments': args}, number)
        try:
            if result.get('isError'):
                raise UsageError('server_error', stage=name, network_path=last_path)
            return json.loads(next(row['text'] for row in result['content'] if row.get('type', 'text') == 'text'))
        except (ValueError, KeyError, TypeError, StopIteration, AttributeError):
            raise UsageError('bad_payload', stage=name, network_path=last_path) from None

    result = call('list_my_teams', {}, 1)
    teams = result.get('teams', []) if isinstance(result, dict) else []
    teams = [row for row in teams if isinstance(row, dict) and isinstance(row.get('team_id'), str)
             and isinstance(row.get('team_alias'), str)] if isinstance(teams, list) else []
    if not teams or len(teams) > 500:
        raise UsageError('no_teams', stage='list_my_teams')
    wanted = team or env.get('LLMOPS_TEAM', 'hcp-pro-tier')
    selected = next((row for row in teams if wanted in (row['team_id'], row['team_alias'])), None)
    if team and selected is None:
        raise UsageError('no_teams', stage='list_my_teams')
    selected = selected or teams[0]
    budget = call('get_team_budget_usage', {'team_id': selected['team_id']}, 2)
    def number(key):
        value = budget.get(key) if isinstance(budget, dict) else None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise UsageError('bad_payload', stage='get_team_budget_usage')
        return value
    spend, maximum = number('spend'), number('max_budget')
    if spend < 0 or maximum < 0:
        raise UsageError('bad_payload', stage='get_team_budget_usage')
    remaining = budget.get('remaining', maximum - spend)
    if type(remaining) not in (int, float) or not math.isfinite(remaining):
        raise UsageError('bad_payload', stage='get_team_budget_usage')
    return {'team': selected['team_id'], 'teamAlias': selected['team_alias'],
            'teams': [{'id': row['team_id'], 'name': row['team_alias']} for row in teams],
            'spend': spend, 'max': maximum, 'remaining': remaining,
            'percent': round(spend / maximum * 100, 1) if maximum > 0 else None,
            'resetAt': str(budget.get('reset_at') or '')[:80], 'demo': False, 'networkPath': last_path}


class UsageService:
    def __init__(self, *, demo=False, query=query_usage, clock=time.time, config_path=None, test_settings=None, env=None, sleep=time.sleep):
        self.demo, self.query, self.clock = demo, query, clock
        self.sleep = sleep
        self.env = os.environ if env is None else env
        self.config_path = config_path or Path(__file__).resolve().parents[1] / llm_usage_config.CONFIG_NAME
        # Only the separately built test edition supplies this path. Production
        # cannot enable address editing through an API payload or environment flag.
        self.test_settings = Path(test_settings) if test_settings else None
        self.lock = threading.Lock()
        self.result, self.error, self.loading, self.next_query, self.team = None, None, False, 0, ''
        self.diagnostic = None
        self.retry = None
        self.retry_not_before = 0
        self.last_attempt = -float('inf')

    def connection(self):
        if self.test_settings and self.test_settings.exists():
            value = llm_usage_config.load(self.test_settings, test=True)
            return value['mcpUrl'], 'test'
        value = llm_usage_config.load(self.config_path)
        # A packaged site URL pins the destination for that deployment.
        if value:
            return value['mcpUrl'], 'deployment'
        url = self.env.get('LLMOPS_MCP_URL', '').strip()
        return (llm_usage_config.checked_url(url), 'environment') if url else ('', 'missing')

    def network_mode(self):
        if self.test_settings:
            value = llm_usage_config.load(self.test_settings, test=True)
            if value:
                return value['networkMode']
        return 'system'

    def configure_test(self, data):
        if self.test_settings is None:
            raise ValueError('서버 주소는 배포 프로그램에서 설정합니다.')
        if (not isinstance(data, dict) or set(data) not in ({'mcpUrl'}, {'mcpUrl', 'networkMode'})
                or not isinstance(data['mcpUrl'], str)):
            raise ValueError('사용량 MCP 서버 주소를 입력해 주세요.')
        raw = llm_usage_config.encoded({'schema': 1, 'mcpUrl': data['mcpUrl'].strip(),
                                        'networkMode': data.get('networkMode', 'system')}, test=True)
        with self.lock:
            if self.loading:
                raise ValueError('진행 중인 조회가 끝난 뒤 주소를 변경해 주세요.')
            from .history import HistoryStore
            HistoryStore(self.test_settings.parent)._write(self.test_settings, raw)
            self.result, self.error, self.diagnostic, self.team = None, None, None, ''
            self.retry_not_before = 0
            self.next_query, self.last_attempt = 0, -float('inf')
        return self.snapshot(refresh=True)

    def snapshot(self, *, refresh=False, team=None):
        if team is not None and (not isinstance(team, str) or len(team) > 200):
            raise ValueError('사용량을 확인할 팀을 다시 선택해 주세요.')
        with self.lock:
            if team is not None and team != self.team:
                if self.loading:
                    raise ValueError('사용량 조회가 끝나면 팀을 다시 선택해 주세요.')
                self.team, self.result, self.error, self.next_query = team, None, None, 0
                self.diagnostic = None
            now = self.clock()
            if (not self.loading and now >= self.retry_not_before
                    and (now >= self.next_query or refresh and now - self.last_attempt >= 15)):
                self.loading = True
                self.retry = None
                self.last_attempt = now
                threading.Thread(target=self._refresh, daemon=True, name='llm-usage').start()
            connection = {'editable': self.test_settings is not None}
            if self.test_settings:
                try:
                    connection['mcpUrl'], connection['source'] = self.connection()
                    connection['networkMode'] = self.network_mode()
                except (OSError, ValueError):
                    connection.update(mcpUrl='', source='invalid')
            return {'usage': self.result, 'error': self.error, 'loading': self.loading,
                    'diagnostic': self.diagnostic, 'connection': connection, 'retry': self.retry,
                    'pollAfterMs': 1000 if self.loading else max(1000, math.ceil((self.next_query - now) * 1000))}

    def _refresh(self):
        try:
            if self.demo:
                result = dict(team='demo', teamAlias='예시 팀', teams=[{'id': 'demo', 'name': '예시 팀'}],
                              spend=7.2, max=20, remaining=12.8, percent=36, resetAt='', demo=True)
            else:
                try:
                    url, _ = self.connection()
                    network_mode = self.network_mode()
                except (OSError, ValueError):
                    raise UsageError('not_configured', stage='configuration') from None
                # Retry this read-only observation on the same route. No task,
                # tool installation or user request is ever replayed here.
                query_env = {**self.env, 'LLMOPS_MCP_URL': url}
                for attempt in range(len(RETRY_DELAYS) + 1):
                    try:
                        result = self.query(team=self.team, env=query_env, network_mode=network_mode)
                        break
                    except UsageError as exc:
                        if not is_transient(exc) or attempt == len(RETRY_DELAYS):
                            raise
                        delay = max(RETRY_DELAYS[attempt], exc.retry_after or 0)
                        if delay > MAX_RETRY_WAIT:
                            raise  # Respect a longer server cooldown without holding the worker.
                        with self.lock:
                            self.error, self.diagnostic = str(exc), exc.public()
                            self.retry = {'attempt': attempt + 1, 'limit': len(RETRY_DELAYS),
                                          'nextAttemptAt': self.clock() + delay}
                        self.sleep(delay)  # Outside the lock; snapshots and tasks stay responsive.
            with self.lock:
                self.result, self.error = dict(result, updatedAt=self.clock()), None
                self.diagnostic = None
                self.retry_not_before = 0
        except Exception as exc:
            # Never expose network responses, credentials or settings paths.
            with self.lock:
                self.error = str(exc) if isinstance(exc, UsageError) else 'unavailable'
                self.diagnostic = exc.public() if isinstance(exc, UsageError) else {'stage': 'client'}
                self.retry_not_before = self.clock() + (exc.retry_after or 0) if isinstance(exc, UsageError) else 0
        finally:
            with self.lock:
                self.loading = False
                self.retry = None
                self.next_query = max(self.retry_not_before,
                    self.clock() + (FAILURE_REFRESH_SECONDS if self.error else REFRESH_SECONDS))
