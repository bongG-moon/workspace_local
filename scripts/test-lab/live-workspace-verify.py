"""Additional live-PC validation, without replacing personal settings.

Start the extracted production launcher with isolated app state, exercise the
installed CLI with one synthetic no-tool prompt, then cleanly stop our server.
Desktop UI checks are performed separately with Computer Use.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['start', 'prepare', 'prompt', 'status', 'finish'])
parser.add_argument('--version', choices=['0.12.0', '0.12.1', '0.12.2', '0.12.3', '0.12.4', '0.12.5', '0.12.6', '0.12.7', '0.12.8', '0.12.9', '0.12.10', '0.12.11', '0.12.12', '0.13.0', '0.14.0', '0.15.0', '0.16.0', '0.17.0'], default='0.17.0')
parser.add_argument('--run', default='', help='Optional distinct validation run name')
args = parser.parse_args()
if args.run and not re.fullmatch(r'[a-z0-9-]{1,30}', args.run):
    parser.error('run must be a short lowercase name')
OUT = ROOT / ('build/qa-workspace-' + args.version + '-live' + ('-' + args.run if args.run else ''))
payload = OUT / 'Company-Workspace'
state = OUT / 'app-state'


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def settings_snapshot():
    config = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    paths = [config / 'settings.json', config / 'settings.local.json', config / 'CLAUDE.md',
             config / '.mcp.json', config / 'plugins/installed_plugins.json']
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths}


def request(route, data=None, timeout=45):
    runtime = json.loads((state / 'runtime.json').read_text(encoding='utf-8'))
    address = urlsplit(runtime['url'])
    headers = {'Authorization': 'Bearer ' + parse_qs(address.fragment)['token'][0]}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = Request(address.scheme + '://' + address.netloc + route,
                  data=json.dumps(data).encode() if data is not None else None, headers=headers)
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


if args.action == 'start':
    OUT.mkdir(parents=True, exist_ok=False)
    save('settings-before.json', settings_snapshot())
    archives = sorted((ROOT / 'dist').glob('company-workspace-preview-' + args.version + '-*.zip'))
    if not archives:
        raise FileNotFoundError('Build the requested bundle first')
    archive = archives[-1]
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(OUT)
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    diagnostic = subprocess.run([str(shell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(payload / 'Check-Workspace.ps1')],
                                capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    reports = sorted(payload.glob('Workspace-Diagnostic-*.json'))
    assert diagnostic.returncode == 0 and reports
    observed = json.loads(reports[-1].read_text(encoding='utf-8-sig'))
    save('startup-diagnostic.json', observed)
    command = [str(shell), '-NoLogo', '-ExecutionPolicy', 'Bypass', '-File', str(payload / 'deploy/Start-CompanyWorkspace.ps1'),
               '-StateRoot', str(state), '-NoBrowser']
    first = subprocess.run(command, capture_output=True, timeout=65, creationflags=subprocess.CREATE_NO_WINDOW)
    if first.returncode:
        print(json.dumps({'launcherExit': first.returncode, 'diagnostic': observed['predictedGuard']}))
        raise SystemExit(1)
    runtime = json.loads((state / 'runtime.json').read_text(encoding='utf-8'))
    boot = request('/api/bootstrap')
    assert boot['workspaceVersion'] == args.version and boot['demo'] is False
    assert Path(boot['appRoot']).resolve() == payload.resolve()
    second = subprocess.run(command, capture_output=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    assert second.returncode == 0
    assert runtime['pid'] == json.loads((state / 'runtime.json').read_text(encoding='utf-8'))['pid']
    workspace = OUT / '검증용 업무'; workspace.mkdir()
    (workspace / '한글 확인.txt').write_text('Company Workspace 실제 연동 검증용 문서입니다.\n값: 42', encoding='utf-8')
    (workspace / '외부 열기 검증.html').write_text('<!doctype html><meta charset="utf-8"><title>Workspace 외부 HTML 검증</title><h1>HTML 열기 성공</h1><p>한글 확인 · 42</p>', encoding='utf-8')
    task = request('/api/create', {'workspace': str(workspace), 'trusted': True, 'title': '실제 CLI 연결 검증'})
    save('task.json', {'id': task['id'], 'workspace': str(workspace)})
    save('startup-result.json', {'success': True, 'version': boot['workspaceVersion'],
        'cliVersion': boot['version'], 'cliProbeError': boot['error'],
        'guard': observed['predictedGuard'], 'uacEnabled': observed['uacEnabled'],
        'samePidReused': True, 'personalSettingsUnchanged': settings_snapshot() == json.loads((OUT / 'settings-before.json').read_text(encoding='utf-8'))})
    print((OUT / 'startup-result.json').read_text(encoding='utf-8'))
elif args.action == 'prepare':
    task = json.loads((OUT / 'task.json').read_text(encoding='utf-8'))
    prepared = request('/api/connect', {'id': task['id']}, timeout=75)
    item = request('/api/session?id=' + task['id'])
    commands = (prepared.get('connection') or {}).get('slashCommands') or []
    assert prepared.get('ok') and commands
    assert not item.get('messages') and item['state'] == 'idle'
    rows = request('/api/completions', {'id': task['id'], 'kind': 'slash', 'query': ''})
    assert rows.get('items') and not rows.get('connectRequired')
    summary = {'initializeSucceeded': True, 'commandCount': len(commands),
               'completionCount': len(rows['items']), 'messageCount': len(item.get('messages') or []),
               'state': item['state'], 'personalSettingsUnchanged': settings_snapshot() == json.loads((OUT / 'settings-before.json').read_text(encoding='utf-8'))}
    save('prepare-result.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
elif args.action == 'prompt':
    task = json.loads((OUT / 'task.json').read_text(encoding='utf-8'))
    result = request('/api/send', {'id': task['id'], 'trusted': True,
        'text': 'GUI 연결 검증용 요청입니다. 파일을 읽거나 수정하지 말고, 도구나 외부 검색을 사용하지 마세요. WORKSPACE_LIVE_OK 라는 한 줄만 답해 주세요.', 'attachments': []})
    print(json.dumps(result))
elif args.action == 'status':
    task = json.loads((OUT / 'task.json').read_text(encoding='utf-8'))
    item = request('/api/session?id=' + task['id'])
    connection = item.get('connection') or {}
    messages = item.get('messages') or []
    result = {'state': item['state'], 'messageRoles': [m.get('role') for m in messages],
        'markerReceived': any('WORKSPACE_LIVE_OK' in m.get('text', '') for m in messages if m.get('role') == 'assistant'),
        'pendingTools': [r.get('tool') for r in (item.get('requests') or {}).values()],
        'connectionReported': connection.get('reported'), 'permissionMode': connection.get('permissionMode'),
        'modelReported': bool(connection.get('model')), 'commands': len(connection.get('slashCommands') or []),
        'tools': len(connection.get('tools') or []), 'skills': len(connection.get('skills') or []),
        'verification': item.get('verification')}
    save('cli-result.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
elif args.action == 'finish':
    result = request('/api/quit', {})
    assert result['ok'] is True
    for _ in range(100):
        if not (state / 'runtime.json').exists():
            break
        time.sleep(.1)
    after = settings_snapshot()
    before = json.loads((OUT / 'settings-before.json').read_text(encoding='utf-8'))
    summary = {'serverClosed': not (state / 'runtime.json').exists(),
               'personalSettingsUnchanged': before == after,
               'changedConfigFileCount': sum(before.get(k) != v for k, v in after.items())}
    save('finish-result.json', summary)
    print(json.dumps(summary, ensure_ascii=False))
