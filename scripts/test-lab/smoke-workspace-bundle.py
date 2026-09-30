"""Extract and run the real bundled entry point in isolated demo state.

Run as the ordinary signed-in Windows user; startup identity checks stay active.
No model, personal Claude configuration, or external document app is invoked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('bundle', type=Path)
args = parser.parse_args()
bundle = args.bundle.resolve(strict=True)
trial = Path(tempfile.mkdtemp(prefix='workspace-0.17.0-extracted-', dir=ROOT / 'build'))
with zipfile.ZipFile(bundle) as archive:
    for member in archive.infolist():
        name = member.filename.replace('\\', '/')
        target = (trial / name).resolve()
        if not target.is_relative_to(trial) or ':' in name or name.startswith('/'):
            raise ValueError('Unsafe archive entry')
    archive.extractall(trial)
payload = trial / 'Company-Workspace'
state = trial / 'isolated-state'
runtime = state / 'demo/runtime.json'
env = os.environ.copy()
env.pop('PYTHONPATH', None)
env['CLAUDE_CONFIG_DIR'] = str(trial / 'isolated-config')
env['PYTHONDONTWRITEBYTECODE'] = '1'
checks = []
origin = token = None
log_path = trial / 'server.log'


def request(route, data=None, *, auth=True, raw=False):
    headers = {'Authorization': 'Bearer ' + token} if auth else {}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = Request(origin + route,
                  data=json.dumps(data).encode() if data is not None else None,
                  headers=headers)
    with urlopen(req, timeout=15) as response:
        body = response.read()
        return body if raw else json.loads(body)


with log_path.open('wb') as log:
    process = subprocess.Popen(
        [sys.executable, '-X', 'utf8', '-m', 'local_app.server', '--demo',
         '--no-browser', '--state', str(state)],
        cwd=payload, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 45
        while not runtime.is_file():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Bundled server failed to start; inspect ' + str(log_path))
            time.sleep(.1)
        context = json.loads(runtime.read_text(encoding='utf-8'))
        address = urlsplit(context['url'])
        origin = address.scheme + '://' + address.netloc
        token = parse_qs(address.fragment)['token'][0]
        boot = request('/api/bootstrap')
        assert boot['workspaceVersion'] == '0.17.0' and boot['demo'] is True
        assert Path(boot['appRoot']).resolve() == payload.resolve()
        assert boot['sessions'] == []
        checks.append('fresh extracted entry point, version and empty isolated state')
        try:
            request('/api/bootstrap', auth=False)
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError('Unauthenticated API was accepted')
        checks.append('authenticated API boundary')
        for route in ['/', '/app.js', '/composer.js', '/inline-controls.js', '/attention.js', '/app.css',
                      '/capabilities.js', '/rendering.js', '/attachments.js', '/workflow.js', '/desktop.js', '/session-import.js',
                      '/fonts/NotoSansKR-Variable.woff', '/manual/guide']:
            content = request(route, raw=True)
            assert len(content) > 100, route
        checks.append('HTML, JavaScript, full Korean font and bundled manual assets')
        task = request('/api/create', {'managed': True, 'trusted': True, 'title': '압축 해제 검증'})
        workspace = Path(task['workspace']).resolve(strict=True)
        assert workspace.is_relative_to(state.resolve())
        (workspace / '확인.txt').write_text('별도 ZIP 실행 확인', encoding='utf-8')
        files = request('/api/files?id=' + task['id'])
        assert any(row.get('name') == '확인.txt' for row in files['files'])
        assert request('/api/attention')['total'] == 0
        request('/api/capabilities?scope=common')
        checks.append('isolated task creation, file listing, attention and catalog APIs')
        assert request('/api/quit', {})['ok'] is True
        process.wait(timeout=15)
        assert process.returncode == 0 and not runtime.exists()
        checks.append('authenticated clean shutdown and runtime cleanup')
    finally:
        if process.poll() is None:
            if token:
                try:
                    request('/api/quit', {})
                    process.wait(timeout=10)
                except Exception:
                    pass
            if process.poll() is None:
                # Only the disposable child created above; never another app.
                process.terminate()
                process.wait(timeout=10)

report = {'success': True, 'bundle': bundle.name,
          'sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
          'extractedRoot': str(payload), 'checks': checks}
report_path = ROOT / 'build/qa-workspace-0.17.0-bundle.json'
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
