"""Read-only usage recovery must not delay task/UI threads or replay tasks."""
import io
import json
import threading
import time
import urllib.error
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from local_app.llm_usage import UsageError, UsageService, query_usage, retry_after_seconds


def settled(service):
    deadline = time.monotonic() + 3
    while service.loading and time.monotonic() < deadline:
        time.sleep(.005)
    assert not service.loading
    return service.snapshot()


def service_for(tmp_path, query, **kwargs):
    return UsageService(query=query, config_path=tmp_path/'missing', env={}, **kwargs)


def test_first_failure_waits_two_minutes_then_five_until_success(tmp_path):
    now, attempts = [1000], []
    def query(**kwargs):
        attempts.append(kwargs)
        if len(attempts) != 3:
            raise UsageError('http_error', stage='initialize', http_status=503)
        return {'spend': 8, 'max': 20}
    service = service_for(tmp_path, query, clock=lambda: now[0])
    service.result = {'spend': 7, 'updatedAt': 900}
    service.snapshot()
    state = settled(service)
    assert state['error'] == 'http_error' and state['usage']['updatedAt'] == 900
    assert state['pollAfterMs'] == 120000 and len(attempts) == 1

    for delay, next_delay in [(120, 300), (300, 300), (300, 120)]:
        count = len(attempts)
        now[0] += delay - 1
        assert service.snapshot()['pollAfterMs'] == 1000 and len(attempts) == count
        now[0] += 1
        service.snapshot()
        state = settled(service)
        assert state['pollAfterMs'] == next_delay * 1000
        assert len(attempts) == count + 1
        if len(attempts) == 3:
            assert state['usage']['spend'] == 8 and state['diagnostic'] is None
    assert all(args == attempts[0] for args in attempts)


@pytest.mark.parametrize('code,status', [
    ('key_required', 401), ('key_required', 403), ('not_configured', None), ('bad_payload', None),
    ('tls_error', None), ('proxy_auth_required', 407), ('rate_limited', 429), ('timeout', None),
    ('connection_error', None), ('http_error', 502), ('http_error', 504)])
def test_failure_does_not_repeat_request_without_user_action(tmp_path, code, status):
    now, calls = [1000], []
    def query(**_):
        calls.append(now[0])
        raise UsageError(code, http_status=status)
    service = service_for(tmp_path, query, clock=lambda: now[0])
    service.snapshot()
    assert settled(service)['error'] == code
    for elapsed in (2, 5, 14):
        now[0] = 1000 + elapsed
        service.snapshot(refresh=True)
    assert calls == [1000]
    now[0] = 1015
    service.snapshot(refresh=True)
    assert settled(service)['pollAfterMs'] == 300000 and calls == [1000, 1015]


def test_retry_after_header_is_safe_and_respected(tmp_path):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError('http://example.test', 503, 'secret-body',
                {'Retry-After':'120', 'X-Secret':'secret'}, io.BytesIO(b'secret'))
    def query(**_):
        return query_usage({'LLMOPS_MCP_URL':'http://example.test/mcp',
                            'ANTHROPIC_AUTH_TOKEN':'secret'}, opener=Opener())
    now = [1000]
    service = service_for(tmp_path, query, clock=lambda:now[0])
    service.snapshot()
    state = settled(service)
    assert state['pollAfterMs'] == 120000
    assert 'secret' not in json.dumps(state)
    now[0] += 30
    assert service.snapshot(refresh=True)['pollAfterMs'] == 90000
    assert service.last_attempt == 1000  # Manual refresh also respects server cooldown.
    assert retry_after_seconds('8') == 8
    assert 6 <= retry_after_seconds(formatdate(time.time()+8, usegmt=True)) <= 8
    assert retry_after_seconds('secret') is None


def test_live_http_budget_failure_reinitializes_only_read_only_usage_calls(tmp_path):
    seen, sessions, budget_calls = [], [], []
    class MCP(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            name = body.get('params', {}).get('name', body['method'])
            seen.append(name)
            if name == 'initialize':
                sessions.append(f'session-{len(sessions)}')
                result = {'protocolVersion':'2024-11-05'}
            else:
                assert self.headers['Mcp-Session-Id'] == sessions[-1]
                if name == 'notifications/initialized':
                    self.send_response(202); self.end_headers(); return
                if name == 'get_team_budget_usage':
                    budget_calls.append(1)
                    if len(budget_calls) <= 2:
                        self.send_response(503); self.end_headers(); return
                    payload = {'spend':3, 'max_budget':20, 'reset_at':'2026-10-28T00:00:00Z'}
                else:
                    assert name == 'list_my_teams'
                    payload = {'teams':[{'team_id':'test', 'team_alias':'fixture'}]}
                result = {'content':[{'type':'text','text':json.dumps(payload)}]}
            raw = json.dumps({'jsonrpc':'2.0', 'id':body['id'], 'result':result}).encode()
            self.send_response(200)
            self.send_header('mcp-session-id', sessions[-1])
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), MCP)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    now = [1000]
    def query(**_):
        return query_usage({'LLMOPS_MCP_URL':f'http://127.0.0.1:{server.server_port}/mcp',
                            'ANTHROPIC_AUTH_TOKEN':'fixture-only'}, network_mode='direct')
    try:
        service = service_for(tmp_path, query, clock=lambda: now[0])
        service.snapshot()
        state = settled(service)
        assert state['error'] == 'http_error' and len(sessions) == 1
        now[0] += 120
        service.snapshot()
        assert settled(service)['error'] == 'http_error' and len(sessions) == 2
        now[0] += 300
        service.snapshot()
        state = settled(service)
        assert state['error'] is None and state['usage']['spend'] == 3 and len(sessions) == 3
        assert seen == ['initialize', 'notifications/initialized', 'list_my_teams', 'get_team_budget_usage'] * 3
        assert state['usage']['resetAt'] == '2026-10-28T00:00:00Z'
    finally:
        server.shutdown(); server.server_close()
