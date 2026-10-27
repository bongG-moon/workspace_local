"""A healthy MCP behind a failing proxy reproduces initialize HTTP 503."""
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request

import pytest

from local_app import llm_usage_config as config
from local_app.llm_usage import ObservedProxyHandler, UsageError, UsageService, query_usage


@pytest.fixture
def endpoints():
    seen={'proxy':0,'mcp':[]}
    class MCP(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen['mcp'].append(body['method'])
            if body['method']=='initialize':result={'protocolVersion':'2024-11-05'}
            else:
                assert self.headers['Mcp-Session-Id']=='fixture'
                if body['method']=='notifications/initialized':
                    self.send_response(202);self.end_headers();return
                payload={'teams':[{'team_id':'one','team_alias':'fixture'}]} if body['params']['name']=='list_my_teams' else {'spend':2,'max_budget':20}
                result={'content':[{'type':'text','text':json.dumps(payload)}]}
            raw=json.dumps({'id':body['id'],'result':result}).encode()
            self.send_response(200);self.send_header('mcp-session-id','fixture')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            seen['proxy']+=1
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(503);self.send_header('Via','private-proxy-address');self.end_headers();self.wfile.write(b'private-proxy-body')
    servers=[ThreadingHTTPServer(('127.0.0.1',0),kind) for kind in (MCP,Proxy)]
    for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
    try:yield f'http://127.0.0.1:{servers[0].server_port}/mcp',f'http://127.0.0.1:{servers[1].server_port}',seen
    finally:
        for server in servers:server.shutdown();server.server_close()


def test_default_proxy_503_and_explicit_direct_success(endpoints,monkeypatch,tmp_path):
    url,proxy,seen=endpoints
    monkeypatch.setattr('urllib.request.getproxies',lambda:{'http':proxy})
    monkeypatch.setattr('urllib.request.proxy_bypass',lambda _host:False)
    env={'LLMOPS_MCP_URL':url,'ANTHROPIC_AUTH_TOKEN':'fixture-only'}
    with pytest.raises(UsageError) as error:query_usage(env,tmp_path)
    assert error.value.public()=={'stage':'initialize','httpStatus':503,'networkPath':'proxy'}
    assert seen=={'proxy':1,'mcp':[]}  # No automatic direct retry.
    result=query_usage(env,tmp_path,network_mode='direct')
    assert result['percent']==10 and result['networkPath']=='direct'
    assert seen['proxy']==1 and len(seen['mcp'])==4
    assert 'private' not in json.dumps(error.value.public())
    assert 'fixture-only' not in json.dumps(result)


def test_proxy_exception_remains_direct(endpoints,monkeypatch,tmp_path):
    url,proxy,seen=endpoints
    monkeypatch.setattr('urllib.request.getproxies',lambda:{'http':proxy})
    monkeypatch.setattr('urllib.request.proxy_bypass',lambda _host:True)
    result=query_usage({'LLMOPS_MCP_URL':url,'ANTHROPIC_AUTH_TOKEN':'fixture-only'},tmp_path)
    assert result['networkPath']=='direct' and seen['proxy']==0


def test_https_tunnel_is_reported_as_proxy(monkeypatch):
    monkeypatch.setattr('urllib.request.proxy_bypass',lambda _host:False)
    request=Request('https://example.test/mcp')
    handler=ObservedProxyHandler({'https':'http://proxy.example.test:8080'})
    handler.proxy_open(request,'http://proxy.example.test:8080','https')
    assert request._llm_proxy_applied is True


def test_route_is_test_only_and_persists(tmp_path):
    value={'schema':1,'mcpUrl':'http://example.test/mcp','networkMode':'direct'}
    with pytest.raises(ValueError):config.encoded(value)
    assert config.from_bytes(config.encoded(value,test=True),test=True)==value
    path=tmp_path/'test.json';path.write_bytes(config.encoded(value,test=True))
    service=UsageService(test_settings=path,env={},config_path=tmp_path/'none')
    assert service.network_mode()=='direct'
    assert UsageService(env={}).network_mode()=='system'
    assert config.from_bytes(config.encoded({'schema':1,'mcpUrl':value['mcpUrl']}),test=True)['networkMode']=='system'
    with pytest.raises(ValueError):config.encoded({**value,'networkMode':'automatic-fallback'},test=True)


def test_existing_node_fetch_can_succeed_while_python_default_uses_proxy(endpoints,monkeypatch,tmp_path):
    node=shutil.which('node')
    if not node:pytest.skip('Node comparison runtime is not installed; application does not require it.')
    url,proxy,seen=endpoints
    monkeypatch.setattr('urllib.request.getproxies',lambda:{'http':proxy})
    monkeypatch.setattr('urllib.request.proxy_bypass',lambda _host:False)
    with pytest.raises(UsageError):query_usage({'LLMOPS_MCP_URL':url,'ANTHROPIC_AUTH_TOKEN':'fixture-only'},tmp_path)
    env={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy','node_options','node_use_env_proxy')}
    env.update(HTTP_PROXY=proxy,HTTPS_PROXY=proxy,NO_PROXY='')
    source="const r=await fetch(process.argv[1],{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'fixture',version:'1'}}})});console.log(r.status);"
    result=subprocess.run([node,'--input-type=module','-e',source,url],env=env,capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0 and result.stdout.strip()=='200'
    assert seen['proxy']==1 and seen['mcp']==['initialize']
