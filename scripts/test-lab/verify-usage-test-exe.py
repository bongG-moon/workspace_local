"""Exercise a built test EXE against a local MCP fixture without personal KEYs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener, ProxyHandler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--proxy-check',action='store_true')
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    config=root/'claude';config.mkdir()
    (config/'settings.json').write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"local-test-only"}}',encoding='utf-8')
    calls=[];fail=[False]
    class MCP(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(data.get('params',{}).get('name',data['method']))
            assert self.headers['Authorization']=='Bearer local-test-only'
            if data['method']=='initialize':
                result={'protocolVersion':'2024-11-05'}
            else:
                assert self.headers['Mcp-Session-Id']=='local-session'
                if data['method']=='notifications/initialized':
                    self.send_response(202);self.end_headers();return
                name=data['params']['name']
                if fail[0] and name=='get_team_budget_usage':
                    self.send_response(500);self.end_headers();self.wfile.write(b'private-server-response');return
                inner={'teams':[{'team_id':'fixture','team_alias':'test-team'}]} if name=='list_my_teams' else {'spend':2.5,'max_budget':20,'remaining':17.5}
                result={'content':[{'type':'text','text':json.dumps(inner)}]}
            raw=('data: '+json.dumps({'jsonrpc':'2.0','id':data['id'],'result':result})+'\n\n').encode()
            self.send_response(200)
            if data['method']=='initialize': self.send_header('mcp-session-id','local-session')
            self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    mcp_host='127.0.0.2' if args.proxy_check else '127.0.0.1'
    mcp=ThreadingHTTPServer((mcp_host,0),MCP)
    threading.Thread(target=mcp.serve_forever,daemon=True).start()
    proxy_hits=[]
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            proxy_hits.append(True)
            self.send_response(503);self.end_headers();self.wfile.write(b'fixture proxy unavailable')
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy) if args.proxy_check else None
    if proxy:threading.Thread(target=proxy.serve_forever,daemon=True).start()
    env={k:v for k,v in os.environ.items() if k not in {'SK_BUDGET_HUD_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_API_KEY','OPENAI_API_KEY','LLMOPS_MCP_URL','LLMOPS_DEMO'}}
    env.update(CLAUDE_CONFIG_DIR=str(config),PSModulePath=str(Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/Modules'))
    if proxy:
        env={k:v for k,v in env.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy')}
        env.update(HTTP_PROXY=f'http://127.0.0.1:{proxy.server_port}',HTTPS_PROXY=f'http://127.0.0.1:{proxy.server_port}',NO_PROXY='localhost,127.0.0.1')
    state=root/'state';runtime=state/'runtime.json';http=build_opener(ProxyHandler({}))
    address=None
    def request(route,data=None):
        info=json.loads(runtime.read_text(encoding='utf-8'))
        address=urlsplit(info['url']);headers={'Authorization':'Bearer '+parse_qs(address.fragment)['token'][0]}
        if data is not None:headers['Content-Type']='application/json'
        req=Request(address.scheme+'://'+address.netloc+route,data=None if data is None else json.dumps(data).encode(),headers=headers)
        with http.open(req,timeout=30) as response:return json.load(response)
    def launch():
        # The EXE's normal default state is separate too. Explicit paths keep
        # this automated verification entirely inside the requested output dir.
        command=[str(args.exe.resolve()),'--no-browser','--state',str(state),'--cache-root',str(root/'cache'),'--python',sys.executable]
        run=subprocess.run(command,env=env,capture_output=True,timeout=100,creationflags=subprocess.CREATE_NO_WINDOW)
        assert run.returncode==0, 'test launcher exit '+str(run.returncode)
        deadline=time.monotonic()+30
        while not runtime.exists() and time.monotonic()<deadline:time.sleep(.1)
        assert runtime.exists(),'isolated server did not start'
    def settled():
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            value=request('/api/llm-usage')
            if not value['loading']:return value
            time.sleep(.05)
        raise AssertionError('usage request did not settle')
    def stop():
        if runtime.exists():
            result=request('/api/quit',{})
            assert result.get('ok'), 'test server shutdown failed'
            deadline=time.monotonic()+15
            while runtime.exists() and time.monotonic()<deadline:time.sleep(.1)
            assert not runtime.exists()
    try:
        launch()
        first=settled()
        assert first['connection']['editable'] and first['error']=='not_configured'
        endpoint=f'http://{mcp_host}:{mcp.server_port}/mcp'
        if proxy:
            request('/api/llm-usage',{'configure':{'mcpUrl':endpoint,'networkMode':'system'}})
            routed=settled()
            assert routed['diagnostic']=={'stage':'initialize','httpStatus':503,'networkPath':'proxy'}
            assert len(proxy_hits)==3 and not calls  # Initial attempt + two retries, no direct fallback.
        mode='direct' if proxy else 'system'
        request('/api/llm-usage',{'configure':{'mcpUrl':endpoint,'networkMode':mode}})
        value=settled()
        assert value['usage']['percent']==12.5 and not value['usage']['demo']
        assert calls==['initialize','notifications/initialized','list_my_teams','get_team_budget_usage']
        assert 'local-test-only' not in json.dumps(value)
        stop();launch()
        restored=settled()
        assert restored['connection']['mcpUrl']==endpoint and restored['usage']['percent']==12.5
        assert restored['connection']['networkMode']==mode
        fail[0]=True
        request('/api/llm-usage',{'configure':{'mcpUrl':endpoint,'networkMode':mode}})
        failure=settled()
        assert failure['error']=='http_error' and failure['diagnostic']=={'stage':'get_team_budget_usage','httpStatus':500,'networkPath':'direct'}
        assert 'private-server-response' not in json.dumps(failure)
        assert failure['usage'] is None
        report={'exeLaunch':True,'isolatedState':True,'savedAddress':True,'settingsTokenRead':True,
                'sessionCalls':calls,'usagePercent':value['usage']['percent'],'restartRestored':True,
                'failureDiagnostic':failure['diagnostic'],'privateValuesExposed':False}
        if proxy:
            assert len(proxy_hits)==3
            report.update(proxyInitialization503Verified=True,explicitDirectSucceeded=True,noAutomaticRouteFallback=True,
                          boundedProxyAttempts=3)
        (root/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2))
    finally:
        stop();mcp.shutdown();mcp.server_close()
        if proxy:proxy.shutdown();proxy.server_close()


if __name__=='__main__':main()
