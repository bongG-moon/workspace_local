"""Optional, bounded team-budget observation. Never participates in task execution."""
import json
import math
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY = 512 * 1024
REFRESH_SECONDS = 300


class UsageError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise UsageError('unavailable')


def resolve_token(env, home):
    for key in ('SK_BUDGET_HUD_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY'):
        if isinstance(env.get(key), str) and env[key].strip():
            return env[key].strip()
    try:
        path = Path(home) / '.claude' / 'settings.json'
        if path.stat().st_size <= MAX_BODY:
            token = json.loads(path.read_text(encoding='utf-8-sig')).get('env', {}).get('ANTHROPIC_AUTH_TOKEN')
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


def query_usage(env=None, home=None, team='', *, timeout=9, opener=None):
    env = os.environ if env is None else env
    url = env.get('LLMOPS_MCP_URL', '').strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise UsageError('not_configured')
    token = resolve_token(env, home or Path.home())
    if not token or '\r' in token or '\n' in token:
        raise UsageError('key_required')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'Authorization': 'Bearer ' + token}
    opener = opener or urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic() + timeout

    def request(method, params, request_id=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UsageError('unavailable')
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
                        raise UsageError('unavailable')
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
            raise UsageError('key_required' if exc.code in (401, 403) else 'unavailable') from None
        except (OSError, ValueError):
            raise UsageError('unavailable') from None

    initialized = request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                          'clientInfo': {'name': 'ax-workspace-usage', 'version': '1.0'}}, 0)
    if isinstance(initialized, dict) and initialized.get('protocolVersion'):
        headers['MCP-Protocol-Version'] = str(initialized['protocolVersion'])
    request('notifications/initialized', {})

    def call(name, args, number):
        result = request('tools/call', {'name': name, 'arguments': args}, number)
        try:
            if result.get('isError'):
                raise UsageError('server_error')
            return json.loads(next(row['text'] for row in result['content'] if row.get('type', 'text') == 'text'))
        except (ValueError, KeyError, TypeError, StopIteration, AttributeError):
            raise UsageError('bad_payload') from None

    result = call('list_my_teams', {}, 1)
    teams = result.get('teams', []) if isinstance(result, dict) else []
    teams = [row for row in teams if isinstance(row, dict) and isinstance(row.get('team_id'), str)
             and isinstance(row.get('team_alias'), str)] if isinstance(teams, list) else []
    if not teams or len(teams) > 500:
        raise UsageError('no_teams')
    wanted = team or env.get('LLMOPS_TEAM', 'hcp-pro-tier')
    selected = next((row for row in teams if wanted in (row['team_id'], row['team_alias'])), None)
    if team and selected is None:
        raise UsageError('no_teams')
    selected = selected or teams[0]
    budget = call('get_team_budget_usage', {'team_id': selected['team_id']}, 2)
    def number(key):
        value = budget.get(key) if isinstance(budget, dict) else None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise UsageError('bad_payload')
        return value
    spend, maximum = number('spend'), number('max_budget')
    if spend < 0 or maximum < 0:
        raise UsageError('bad_payload')
    remaining = budget.get('remaining', maximum - spend)
    if type(remaining) not in (int, float) or not math.isfinite(remaining):
        raise UsageError('bad_payload')
    return {'team': selected['team_id'], 'teamAlias': selected['team_alias'],
            'teams': [{'id': row['team_id'], 'name': row['team_alias']} for row in teams],
            'spend': spend, 'max': maximum, 'remaining': remaining,
            'percent': round(spend / maximum * 100, 1) if maximum > 0 else None,
            'resetAt': str(budget.get('reset_at') or '')[:80], 'demo': False}


class UsageService:
    def __init__(self, *, demo=False, query=query_usage, clock=time.time):
        self.demo, self.query, self.clock = demo, query, clock
        self.lock = threading.Lock()
        self.result, self.error, self.loading, self.next_query, self.team = None, None, False, 0, ''
        self.last_attempt = -float('inf')

    def snapshot(self, *, refresh=False, team=None):
        if team is not None and (not isinstance(team, str) or len(team) > 200):
            raise ValueError('사용량을 확인할 팀을 다시 선택해 주세요.')
        with self.lock:
            if team is not None and team != self.team:
                if self.loading:
                    raise ValueError('사용량 조회가 끝나면 팀을 다시 선택해 주세요.')
                self.team, self.result, self.error, self.next_query = team, None, None, 0
            if not self.loading and (self.clock() >= self.next_query or refresh and self.clock() - self.last_attempt >= 15):
                self.loading = True
                self.last_attempt = self.clock()
                threading.Thread(target=self._refresh, daemon=True, name='llm-usage').start()
            return {'usage': self.result, 'error': self.error, 'loading': self.loading}

    def _refresh(self):
        try:
            if self.demo:
                result = dict(team='demo', teamAlias='예시 팀', teams=[{'id': 'demo', 'name': '예시 팀'}],
                              spend=7.2, max=20, remaining=12.8, percent=36, resetAt='', demo=True)
            else:
                result = self.query(team=self.team)
            with self.lock:
                self.result, self.error = dict(result, updatedAt=self.clock()), None
        except Exception as exc:
            # Never expose network responses, credentials or settings paths.
            with self.lock:
                self.error = str(exc) if isinstance(exc, UsageError) else 'unavailable'
        finally:
            with self.lock:
                self.loading = False
                self.next_query = self.clock() + (60 if self.error else REFRESH_SECONDS)
