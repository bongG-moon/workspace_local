"""Synthetic native/browser UI rehearsal with optional one-time asset failure.

Uses the real application and host, isolated state and synthetic files. Never
starts Claude. Fault injection exists only in this test server, not production.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.server import LocalApp, Server, Handler
from local_app.native_window import DesktopHost
from local_app.attention import AttentionNotifier

parser = argparse.ArgumentParser()
parser.add_argument('--state', type=Path, required=True)
parser.add_argument('--native', action='store_true')
parser.add_argument('--fail-workflow-once', action='store_true')
args = parser.parse_args()
state = args.state.resolve(); state.mkdir(parents=True, exist_ok=False)
app = LocalApp(state, demo=True)
task = app.create('', True, managed=True, title='패치 검증 · 합성 업무')
item = app.get(task['id'])
workspace = Path(item['workspace'])
(workspace/'metrics.csv').write_text('label,value\n**합계**,**468**\n', encoding='utf-8-sig')
item['messages'] = [{'role':'user', 'text':'표와 복사 기능 확인'},
    {'role':'assistant','text':'| 항목 | 값 |\n| --- | ---: |\n| 7월 | 120 |\n| 8월 | 156 |\n| 9월 | 192 |\n| **합계** | **468** |\n\n```python\nprint(sum([120, 156, 192]))\n```'}]
item['state'] = 'done'; app.save(item['id'])
server = Server(app)
fault_lock = threading.Lock()
remaining = args.fail_workflow_once

class RehearsalHandler(Handler):
    def do_GET(self):
        global remaining
        with fault_lock:
            fail = remaining and urlsplit(self.path).path == '/workflow.js'
            if fail:remaining = False
        if fail:
            return self.reply(b'// intentional one-time asset failure',503,'text/javascript; charset=utf-8')
        return super().do_GET()

server.RequestHandlerClass = RehearsalHandler
url = server.origin + '/#token=' + app.token
host = None
if args.native:
    app.notifier = AttentionNotifier(enabled=True)
    host = DesktopHost(app.notifier, url, state, background=True, on_event=app.ui_health.native)
    app._desktop_window = host
    app._open_window_callback = host.open
    host.open()
meta = {'url':url,'sessionId':item['id'],'workspace':str(workspace),'native':args.native}
(state/'fixture.json').write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'origin':server.origin,'metadata':str(state/'fixture.json'),'native':args.native}),flush=True)
try:
    server.serve_forever(poll_interval=.2)
finally:
    app.close()
    if host:host.close()
    server.server_close()
