"""Actual general-chat HTTP/UI service with isolated state and synthetic AI."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
root = args.output.absolute()
root.mkdir(parents=True, exist_ok=False)
os.environ['LOCALAPPDATA'] = str(root / 'local')
os.environ['CLAUDE_CONFIG_DIR'] = str(root / 'claude')
(root / 'claude').mkdir()
(root / 'workspace').mkdir()
(root / 'original.txt').write_text('시험 자료입니다. 목표 100건, 실적 120건입니다.', encoding='utf-8')

from local_app.server import LocalApp, Server

app = LocalApp(root / 'state', demo=True)
work = app.create(str(root / 'workspace'), True, title='기존 업무 보존 확인')
server = Server(app)
(root / 'runtime.json').write_text(json.dumps({'url': server.origin + '/#token=' + app.token,
    'workId': work['id'], 'original': str(root / 'original.txt')}, ensure_ascii=False), encoding='utf-8')
print('Isolated general-chat fixture ready. Actual files/API, synthetic AI.', flush=True)
try:
    server.serve_forever(poll_interval=.2)
finally:
    app.close()
    server.server_close()
