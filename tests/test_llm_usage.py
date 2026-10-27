import io
import json
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from local_app.llm_usage import UsageError, UsageService, parse_response, query_usage, resolve_token


def test_mcp_session_sse_and_team_selection(tmp_path):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((body, dict(self.headers)))
            assert self.headers['Authorization'] == 'Bearer test-secret'
            if body['method'] == 'initialize':
                result = {'protocolVersion': '2024-11-05'}
            else:
                assert self.headers['Mcp-Session-Id'] == 'fixture-session'
                if body['method'] == 'notifications/initialized':
                    self.send_response(202); self.end_headers(); return
                name = body['params']['name']
                value = {'teams': [{'team_id':'pro','team_alias':'hcp-pro-tier'}, {'team_id':'free','team_alias':'hcp-free-tier'}]} if name == 'list_my_teams' else {
                    'spend':19.2, 'max_budget':20, 'remaining':.8, 'reset_at':'2026-10-28T00:00:00'}
                if name == 'get_team_budget_usage':
                    assert body['params']['arguments'] == {'team_id':'free'}
                result = {'content':[{'type':'text','text':json.dumps(value)}]}
            raw = (': keepalive\n\ndata: {"jsonrpc":"2.0","method":"notice"}\n\ndata: ' + json.dumps({'jsonrpc':'2.0','id':body['id'],'result':result}) + '\n\n').encode()
            self.send_response(200)
            if body['method'] == 'initialize': self.send_header('mcp-session-id','fixture-session')
            self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        result=query_usage({'LLMOPS_MCP_URL':f'http://127.0.0.1:{server.server_port}/mcp','ANTHROPIC_AUTH_TOKEN':'test-secret'},tmp_path,team='hcp-free-tier')
        assert result['percent']==96 and result['team']=='free'
        assert result['resetAt'] == '2026-10-28T00:00:00'  # Preserve the API value for display/diagnosis.
        assert len(seen)==4 and 'test-secret' not in json.dumps(result)
    finally: server.shutdown();server.server_close()


def test_auth_precedence_and_parser(tmp_path):
    (tmp_path/'.claude').mkdir();(tmp_path/'.claude/settings.json').write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"settings-token"}}')
    assert resolve_token({'OPENAI_API_KEY':'fallback'},tmp_path)=='settings-token'
    assert resolve_token({'SK_BUDGET_HUD_API_KEY':'first','ANTHROPIC_AUTH_TOKEN':'second'},tmp_path)=='first'
    assert parse_response(b'{"id":2,"result":{"ok":true}}',2)=={'ok':True}
    with pytest.raises(UsageError,match='bad_payload'): parse_response(b'data: {"id":3,"result":{}}\n\n',2)
    with pytest.raises(UsageError,match='not_configured'): query_usage({},tmp_path)
    with pytest.raises(UsageError,match='key_required'): query_usage({'LLMOPS_MCP_URL':'http://127.0.0.1/mcp'},tmp_path/'empty')


def test_background_coalescing_and_failure_keeps_last_result():
    release=threading.Event();started=threading.Event();calls=[]
    def query(**kwargs):
        calls.append(kwargs);started.set();release.wait(2)
        return {'spend':1,'max':20}
    now=[1000]
    service=UsageService(query=query,clock=lambda:now[0])
    begin=time.monotonic()
    for _ in range(30): assert service.snapshot()['loading']
    assert time.monotonic()-begin < .2
    assert started.wait(1) and len(calls)==1
    release.set()
    deadline=time.monotonic()+2
    while service.loading and time.monotonic()<deadline: time.sleep(.005)
    assert service.snapshot()['usage']['spend']==1
    now[0]+=301
    def fail(**_): raise UsageError('key_required')
    service.query=fail;service.snapshot()
    deadline=time.monotonic()+2
    while service.loading and time.monotonic()<deadline: time.sleep(.005)
    result=service.snapshot()
    assert result['error']=='key_required' and result['usage']['spend']==1
