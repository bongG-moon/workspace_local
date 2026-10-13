"""Isolated UI fixture: synthetic CLI, disposable skills/settings, no credentials."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_app.bridge import probe_cli
from local_app.server import LocalApp, Server

root = Path(tempfile.mkdtemp(prefix='customizations-ui-', dir=ROOT / 'build'))
config = root / 'profile'
os.environ['CLAUDE_CONFIG_DIR'] = str(config)
os.environ['LOCALAPPDATA'] = str(root / 'local')
skill = config / 'skills/quarterly-report/SKILL.md'
skill.parent.mkdir(parents=True)
skill.write_text('---\nname: quarterly-report\ndescription: 분기 실적 보고서를 정리하는 검증용 스킬입니다.\n---\nFixture only.',encoding='utf-8')
command = [sys.executable, '-B', str(ROOT / 'tests/fixtures/workspace_effort_cli.py')]
info = probe_cli(command); info['version']='2.1.296 (synthetic fixture)'
app = LocalApp(root / 'state', command=command, info=info, managed_workspace_root=root / 'workspaces')
server = Server(app)
app.create('', True, managed=True, title='스킬·명령 관리 검증')
metadata = {'url':server.origin + '/#token=' + app.token, 'root':str(root)}
(ROOT / 'build/customizations-ui.json').write_text(json.dumps(metadata),encoding='utf-8')
print('Isolated customization UI ready.',flush=True)
try:
    server.serve_forever(poll_interval=.3)
finally:
    app.close();server.server_close()
