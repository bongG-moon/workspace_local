"""Isolated approval-card browser fixture; no real permissions are changed."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.bridge import probe_cli
from local_app.server import LocalApp, Server

parser = argparse.ArgumentParser()
parser.add_argument('--layout', action='store_true', help='Use long approval, edit and question specimens')
parser.add_argument('--output', type=Path, help='Write runtime metadata to this file')
args = parser.parse_args()
out = Path(tempfile.mkdtemp(prefix='workspace-permission-ui-', dir=ROOT / 'build'))
os.environ['CLAUDE_CONFIG_DIR'] = str(out / 'isolated-config')
os.environ['LOCALAPPDATA'] = str(out / 'isolated-local')
os.environ['WORKSPACE_PERMISSION_DESTINATION'] = 'localSettings'
fixture = 'workspace_approval_layout_cli.py' if args.layout else 'workspace_permission_cli.py'
command = [sys.executable, '-B', str(ROOT / 'tests/fixtures' / fixture)]
app = LocalApp(out / 'state', command=command, info=probe_cli(command), managed_workspace_root=out / 'workspaces')
server = Server(app)
task = app.create('', True, managed=True, title='승인 선택 검증')
app.send(task['id'], 'permission', [], True)
metadata = args.output or ROOT / 'build/qa-workspace-0.12.1-permission-ui.json'
metadata.parent.mkdir(parents=True, exist_ok=True)
metadata.write_text(json.dumps({
    'url': server.origin + '/#token=' + app.token, 'output': str(out), 'id': task['id']
}), encoding='utf-8')
print('Synthetic approval UI fixture ready.', flush=True)
try:
    server.serve_forever(poll_interval=.3)
finally:
    app.close()
    server.server_close()
