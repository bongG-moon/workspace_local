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


@pytest.mark.parametrize('failures', [1, 2])
def test_503_recovers_same_route_and_then_caches(tmp_path, failures):
    calls, sleeps = [], []
    def query(**kwargs):
        calls.append(kwargs)
        if len(calls) <= failures:
            raise UsageError('http_error', stage='initialize', http_status=503)
        return {'spend': 2, 'max': 20}
    service = service_for(tmp_path, query, sleep=sleeps.append, clock=lambda:1000)
    service.snapshot()
    state = settled(service)
    assert len(calls) == failures + 1 and sleeps == [2, 5][:failures]
    assert all(c == calls[0] for c in calls) and calls[0]['network_mode'] == 'system'
    assert state['error'] is None and state['retry'] is None
    assert state['usage']['spend'] == 2 and state['pollAfterMs'] == 300000
    for _ in range(10): service.snapshot(refresh=True)
    assert len(calls) == failures + 1


def test_exhausted_retries_keep_result_then_refresh_after_one_minute(tmp_path):
    now, calls, sleeps = [1000], [], []
    def query(**_):
        calls.append(1)
        raise UsageError('http_error', stage='initialize', http_status=503)
    service = service_for(tmp_path, query, sleep=sleeps.append, clock=lambda:now[0])
    service.result = {'spend': 7, 'max': 20, 'updatedAt': 900}
    service.snapshot()
    state = settled(service)
    assert len(calls) == 3 and sleeps == [2, 5]
    assert state['usage']['spend'] == 7 and state['usage']['updatedAt'] == 900
    assert state['pollAfterMs'] == 60000 and state['retry'] is None
    now[0] += 59
    assert service.snapshot()['pollAfterMs'] == 1000 and len(calls) == 3
    service.query = lambda **_: {'spend': 8, 'max': 20}
    now[0] += 1
    service.snapshot()
    recovered = settled(service)
    assert recovered['usage']['spend'] == 8 and recovered['error'] is None
    assert recovered['diagnostic'] is None and recovered['pollAfterMs'] == 300000


@pytest.mark.parametrize('code,status', [('key_required',401), ('key_required',403),
    ('not_configured',None), ('bad_payload',None), ('server_error',None),
    ('tls_error',None), ('proxy_auth_required',407), ('rate_limited',429)])
def test_permanent_or_auth_failures_do_not_immediately_retry(tmp_path, code, status):
    calls, sleeps = [], []
    def query(**_):
        calls.append(1)
        raise UsageError(code, http_status=status)
    service = service_for(tmp_path, query, sleep=sleeps.append)
    service.snapshot()
    state = settled(service)
    assert calls == [1] and sleeps == [] and state['error'] == code


@pytest.mark.parametrize('code,status', [('timeout',None), ('connection_error',None),
    ('http_error',502), ('http_error',504)])
def test_other_transient_errors_are_bounded(tmp_path, code, status):
    calls, sleeps = [], []
    def query(**_):
        calls.append(1)
        raise UsageError(code, http_status=status)
    service = service_for(tmp_path, query, sleep=sleeps.append)
    service.snapshot()
    assert settled(service)['error'] == code
    assert len(calls) == 3 and sleeps == [2, 5]


def test_backoff_does_not_hold_lock_and_coalesces_manual_refresh(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def query(**_):
        calls.append(1)
        if len(calls) == 1:
            raise UsageError('http_error', http_status=503)
        return {'spend': 2}
    def sleep(_):
        entered.set()
        assert release.wait(3)
    service = service_for(tmp_path, query, sleep=sleep)
    service.snapshot()
    try:
        assert entered.wait(1)
        begin = time.monotonic()
        for _ in range(100):
            state = service.snapshot(refresh=True)
            assert state['loading'] and state['retry']['attempt'] == 1
            assert state['pollAfterMs'] == 1000
        assert time.monotonic() - begin < .2 and len(calls) == 1
    finally:
        release.set()
    assert settled(service)['error'] is None and len(calls) == 2


def test_retry_after_header_is_safe_and_respected(tmp_path):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError('http://example.test', 503, 'secret-body',
                {'Retry-After':'120', 'X-Secret':'secret'}, io.BytesIO(b'secret'))
    def query(**_):
        return query_usage({'LLMOPS_MCP_URL':'http://example.test/mcp',
                            'ANTHROPIC_AUTH_TOKEN':'secret'}, opener=Opener())
    now, sleeps = [1000], []
    service = service_for(tmp_path, query, sleep=sleeps.append, clock=lambda:now[0])
    service.snapshot()
    state = settled(service)
    assert sleeps == [] and state['pollAfterMs'] == 120000
    assert 'secret' not in json.dumps(state)
    now[0] += 30
    assert service.snapshot(refresh=True)['pollAfterMs'] == 90000
    assert service.last_attempt == 1000  # Manual refresh also respects server cooldown.
    assert retry_after_seconds('8') == 8
    assert 6 <= retry_after_seconds(formatdate(time.time()+8, usegmt=True)) <= 8
    assert retry_after_seconds('secret') is None


def test_short_retry_after_extends_backoff(tmp_path):
    calls, sleeps = [], []
    def query(**_):
        calls.append(1)
        if len(calls) == 1:
            raise UsageError('http_error', http_status=503, retry_after=10)
        return {'spend': 1}
    service = service_for(tmp_path, query, sleep=sleeps.append)
    service.snapshot()
    assert settled(service)['error'] is None and sleeps == [10]


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
    sleeps = []
    def query(**_):
        return query_usage({'LLMOPS_MCP_URL':f'http://127.0.0.1:{server.server_port}/mcp',
                            'ANTHROPIC_AUTH_TOKEN':'fixture-only'}, network_mode='direct')
    try:
        service = service_for(tmp_path, query, sleep=sleeps.append)
        service.snapshot()
        state = settled(service)
        assert state['error'] is None and state['usage']['spend'] == 3
        assert sleeps == [2, 5] and len(sessions) == 3
        assert seen == ['initialize', 'notifications/initialized', 'list_my_teams', 'get_team_budget_usage'] * 3
        assert state['usage']['resetAt'] == '2026-10-28T00:00:00Z'
    finally:
        server.shutdown(); server.server_close()
