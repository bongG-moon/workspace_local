"""Run from repo root: real HTTP/browser queue QA; isolated CLI test double only."""
import sys,json,threading,time,subprocess,os
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from tests.test_workspace_dispatch_routes import DispatchRoutesTests,Bridge
case=DispatchRoutesTests();case.setUp();stop=threading.Event()
try:
 item=case.app.get(case.sid);item.update(state='running',sessionId='fixture-session')
 case.bridge.finish=False
 for text in ['first queued','cancel this','apply this']:case.enqueue(text)
 resumed=Bridge(case.app,case.sid,finish=False)
 def pump():
  while not stop.wait(.03):case.app.dispatch.pump()
 config=case.root / 'browser-config.json';config.write_text(json.dumps({'url':case.server.origin+'/#token='+case.app.token,'id':case.sid}),encoding='utf-8')
 with patch('local_app.server.ClaudeSession',return_value=resumed):
  worker=threading.Thread(target=pump,daemon=True);worker.start()
  result=subprocess.run([r'C:\nvm4w\nodejs\node.exe','scripts/test-lab/verify-workspace-queue-http.cjs',str(config)],env={**os.environ,'NODE_PATH':r'C:\Users\qkekt\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'},timeout=90)
  assert result.returncode==0,result.returncode
  assert resumed.sent==['apply this'],resumed.sent
  rows=case.app.dispatch.snapshot(case.sid)['queue']
  assert [r['text'] for r in rows if r['state']=='queued']==['first queued'],rows
  assert [r['text'] for r in rows if r['state']=='submitted']==['apply this'],rows
  assert case.bridge.closed
  print('Server verified: selected request sent exactly once after interrupt; remaining request preserved.')
finally:
 stop.set();case.doCleanups()
