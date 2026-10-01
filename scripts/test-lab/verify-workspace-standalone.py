"""Validate the real one-file EXE using isolated cache/state and existing Claude.

Run outside the sandbox as the signed-in Windows user. AI prompt is an explicit
separate action; start/prepare send no user messages and do not alter settings.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['start', 'prepare', 'controls', 'prompt', 'activity', 'status', 'finish'])
parser.add_argument('--exe', type=Path, default=ROOT / 'dist/Company-Workspace-0.21.8.exe')
parser.add_argument('--run', default='final')
parser.add_argument('--python', type=Path, help='Explicit existing interpreter for an EXE startup check')
parser.add_argument('--desktop', action='store_true', help='Open the real owned WebView2 window')
args = parser.parse_args()
if not re.fullmatch('[a-z0-9-]{1,30}', args.run):
    parser.error('Invalid validation run name')
OUT = ROOT / ('build/qa-standalone-0.21.8-' + args.run)
STATE = OUT / 'app-state'
CACHE = OUT / '실행 캐시'
COPY = OUT / 'EXE만 있는 한글 폴더' / args.exe.name
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def settings():
    config = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    paths = [config / item for item in ('settings.json', 'settings.local.json', 'CLAUDE.md', '.mcp.json', 'plugins/installed_plugins.json')]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in paths}


def request(route, data=None, timeout=75):
    runtime = json.loads((STATE / 'runtime.json').read_text(encoding='utf-8'))
    parsed = urlsplit(runtime['url'])
    headers = {'Authorization': 'Bearer ' + parse_qs(parsed.fragment)['token'][0]}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    query = Request(parsed.scheme + '://' + parsed.netloc + route,
                    data=json.dumps(data).encode() if data is not None else None, headers=headers)
    with urlopen(query, timeout=timeout) as response:
        return json.load(response)


def task_id():
    return json.loads((OUT / 'task.json').read_text(encoding='utf-8'))['id']


if args.action == 'start':
    OUT.mkdir(parents=True, exist_ok=False)
    COPY.parent.mkdir()
    shutil.copy2(args.exe, COPY)
    assert list(COPY.parent.iterdir()) == [COPY]
    save('settings-before.json', settings())
    env = os.environ.copy()
    command = [str(COPY), '--state', str(STATE), '--cache-root', str(CACHE)]
    if not args.desktop:
        command.append('--no-browser')
    if args.python is not None:
        command.extend(['--python', str(args.python.resolve(strict=True))])
    first = subprocess.run(command, cwd=COPY.parent, env=env, capture_output=True, timeout=110, creationflags=HIDDEN)
    assert first.returncode == 0, f'EXE exit {first.returncode}'
    runtime = json.loads((STATE / 'runtime.json').read_text(encoding='utf-8'))
    boot = request('/api/bootstrap')
    if args.desktop:
        assert boot['window']['mode'] == 'desktop' and boot['window']['engine'] == 'WebView2'
        assert request('/api/window/open', {})['action'] == 'activated'
    assert boot['workspaceVersion'] == '0.21.8' and boot['demo'] is False and not boot['error']
    app_root = Path(boot['appRoot'])
    assert app_root.is_relative_to(CACHE) and not (app_root / 'runtime').exists()
    assert not list(app_root.rglob('python*.exe'))
    # Read the owned server's executable, never expose its command line or auth.
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    process = subprocess.run([str(shell), '-NoProfile', '-Command',
        f'$p=(Get-CimInstance Win32_Process -Filter "ProcessId={int(runtime["pid"])}").ExecutablePath; [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($p))'],
        capture_output=True, text=True, timeout=15, creationflags=HIDDEN)
    server_python = base64.b64decode(process.stdout.strip()).decode('utf-8')
    assert process.returncode == 0 and server_python
    installed_python = Path(server_python).resolve()
    assert installed_python.is_file() and not installed_python.is_relative_to(CACHE)
    probe_python = installed_python.with_name('python.exe') if installed_python.name.lower() == 'pythonw.exe' else installed_python
    diagnostic = subprocess.run([str(probe_python), '-X', 'utf8', '-c',
        'import json,sys,ctypes,ssl,local_app.server; print(json.dumps({"executable":sys.executable,"version":list(sys.version_info[:3])}))'],
        cwd=app_root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=20, creationflags=HIDDEN)
    assert diagnostic.returncode == 0, diagnostic.stderr
    python_info = json.loads(diagnostic.stdout)
    assert tuple(python_info['version']) >= (3, 11, 0)
    assert Path(python_info['executable']).resolve() == probe_python
    second = subprocess.run(command, cwd=COPY.parent, env=env, capture_output=True, timeout=110, creationflags=HIDDEN)
    assert second.returncode == 0
    assert json.loads((STATE / 'runtime.json').read_text())['pid'] == runtime['pid']
    for route in ('/', '/app.js', '/inline-controls.js', '/rendering.js', '/attachments.js', '/path-picker.js', '/path-picker.css',
                  '/workflow.js', '/desktop.js', '/session-import.js', '/app.css',
                  '/rich-content.js', '/rich-content.css', '/execution-view.js', '/execution-view.css',
                  '/tool-activity.js', '/tool-activity.css',
                  '/upgrade-handoff.js', '/upgrade-handoff.css',
                  '/input-keys.js', '/chat-shortcuts.js', '/chat-shortcuts.css',
                  '/startup-health.js', '/startup-health.css',
                  '/fonts/NotoSansKR-Variable.woff', '/manual/guide'):
        parsed = urlsplit(runtime['url'])
        query = Request(parsed.scheme + '://' + parsed.netloc + route,
            headers={'Authorization': 'Bearer ' + parse_qs(parsed.fragment)['token'][0]})
        with urlopen(query) as response:
            assert response.status == 200 and response.read(1)
    workspace = OUT / '검증용 업무'
    workspace.mkdir()
    task = request('/api/create', {'workspace': str(workspace), 'trusted': True, 'title': '단일 EXE 연결 검증'})
    save('task.json', {'id': task['id'], 'workspace': str(workspace)})
    result = {'success': True, 'version': boot['workspaceVersion'], 'cliVersion': boot['version'],
        'cliRuntime': boot['runtime'], 'installedPythonVersion': '.'.join(map(str, python_info['version'])),
        'serverUsesExistingPython': True, 'pythonBundled': False,
        'explicitPythonRequested': args.python is not None,
        'exeOnlyFolder': True, 'nativeDesktop': args.desktop,
        'stableCacheAndPidReused': True, 'appRoot': str(app_root), 'personalSettingsUnchanged': settings() == json.loads((OUT / 'settings-before.json').read_text())}
    save('startup-result.json', result)
    print(json.dumps(result, ensure_ascii=False))
elif args.action == 'prepare':
    prepared = request('/api/connect', {'id': task_id()})
    item = request('/api/session?id=' + task_id())
    connection = prepared['connection']
    assert prepared['ok'] and connection.get('connected') and not item['messages']
    assert connection.get('model') and connection.get('slashCommands')
    result = {'initializeSucceeded': True, 'model': connection['model'], 'effort': connection.get('effort'),
        'commandCount': len(connection['slashCommands']), 'userMessageCount': 0,
        'personalSettingsUnchanged': settings() == json.loads((OUT / 'settings-before.json').read_text())}
    save('prepare-result.json', result)
    print(json.dumps(result, ensure_ascii=False))
elif args.action == 'controls':
    # Exercise only SDK controls: never synthesize a conversation turn.
    initial = request('/api/session?id=' + task_id())
    assert not initial['messages']
    baseline = initial['connection']
    checks = []

    def inspect(label, mode=None, effort=None):
        item = request('/api/session?id=' + task_id())
        connection = item['connection']
        assert item['state'] not in {'starting', 'running', 'approval', 'question'}, item['state']
        assert not item['messages'] and not item.get('requests')
        if mode is not None:
            assert connection['permissionMode'] == mode, connection['permissionMode']
        if effort is not None:
            assert connection['effort'] == effort, connection['effort']
        checks.append({'check': label, 'state': item['state'], 'messageCount': 0,
                       'permissionMode': connection.get('permissionMode'),
                       'effort': connection.get('effort'), 'model': connection.get('model')})
        return connection

    def reconnect():
        request('/api/stop', {'id': task_id()})
        deadline = time.monotonic() + 30
        while not request('/api/session?id=' + task_id()).get('connectionStopped'):
            assert time.monotonic() < deadline, 'Owned CLI did not stop'
            time.sleep(.2)
        request('/api/connect', {'id': task_id()})

    available = {row['value'] for row in baseline['availablePermissionModes']}
    manual = 'default' if 'default' in available else 'manual'
    request('/api/permission-mode', {'id': task_id(), 'mode': manual})
    selected = inspect('manual-alias-applied')
    assert selected['permissionMode'] in {'manual', 'default'}
    reconnect()
    selected = inspect('manual-alias-restored')
    assert selected['permissionMode'] in {'manual', 'default'}
    for mode in ('plan', 'acceptEdits', 'auto'):
        request('/api/permission-mode', {'id': task_id(), 'mode': mode})
        inspect('permission-' + mode, mode=mode)
    request('/api/effort', {'id': task_id(), 'effort': 'high'})
    inspect('effort-high', mode='auto', effort='high')
    request('/api/effort', {'id': task_id(), 'effort': None})
    restored = inspect('effort-reset', mode='auto', effort=baseline['effort'])
    assert restored['model'] == baseline['model'] and restored['effortOverride'] is None
    request('/api/effort', {'id': task_id(), 'effort': 'high'})
    reconnect()
    inspect('reconnected-with-selections', mode='auto', effort='high')
    result = {'checks': checks, 'userMessagesSent': 0,
              'personalSettingsUnchanged': settings() == json.loads((OUT / 'settings-before.json').read_text())}
    assert result['personalSettingsUnchanged']
    save('control-result.json', result)
    print(json.dumps(result, ensure_ascii=False))
elif args.action == 'prompt':
    before = request('/api/session?id=' + task_id())
    save('pre-prompt-controls.json', {key: before['connection'].get(key) for key in ('model', 'effort', 'permissionMode')})
    print(json.dumps(request('/api/send', {'id': task_id(), 'trusted': True, 'attachments': [],
        'text': '단일 EXE의 기존 Claude 인증 연결 검증입니다. 파일을 읽거나 수정하지 말고 도구나 외부 검색 없이 WORKSPACE_EXE_OK 한 줄만 답해 주세요.'})))
elif args.action == 'activity':
    # One explicitly requested read-only AI probe, confined to our own fixture.
    # No permission/settings overrides and no automatic permission responses.
    task = json.loads((OUT / 'task.json').read_text(encoding='utf-8'))
    probe = Path(task['workspace']) / 'activity-probe.txt'
    probe.write_text('WORKSPACE_ACTIVITY_OK\n', encoding='utf-8')
    before = request('/api/session?id=' + task_id())
    assert not before['messages'] and before['state'] not in {'starting', 'running', 'approval', 'question'}
    save('pre-prompt-controls.json', {key: before['connection'].get(key) for key in ('model', 'effort', 'permissionMode')})
    print(json.dumps(request('/api/send', {'id': task_id(), 'trusted': True, 'attachments': [],
        'text': '활동 표시 기능 검증입니다. 현재 업무 폴더의 activity-probe.txt 파일을 Read 도구로 한 번 읽고 파일 안의 한 줄만 답하세요. 파일을 수정하거나 외부에 연결하지 마세요.'})))
elif args.action == 'status':
    item = request('/api/session?id=' + task_id())
    result = {'state': item['state'], 'messageCount': len(item['messages']),
        'markerReceived': any('WORKSPACE_EXE_OK' in msg.get('text', '') for msg in item['messages'] if msg.get('role') == 'assistant'),
        'pendingTools': [row.get('tool') for row in item.get('requests', [])],
        'verification': item.get('verification'),
        'activity': [{key: row.get(key) for key in ('id', 'tool', 'state', 'action', 'target', 'runId')}
                     for row in item.get('toolActivity', [])],
        'activityMarkerReceived': any('WORKSPACE_ACTIVITY_OK' in msg.get('text', '')
                                     for msg in item['messages'] if msg.get('role') == 'assistant'),
        'controls': {key: item.get('connection', {}).get(key) for key in ('model', 'effort', 'permissionMode')}}
    before_path = OUT / 'pre-prompt-controls.json'
    if before_path.exists():
        result['controlsPreservedAfterPrompt'] = result['controls'] == json.loads(before_path.read_text(encoding='utf-8'))
    save('response-result.json', result)
    print(json.dumps(result, ensure_ascii=False))
elif args.action == 'finish':
    assert request('/api/quit', {})['ok']
    for _ in range(100):
        if not (STATE / 'runtime.json').exists():
            break
        time.sleep(.1)
    before = json.loads((OUT / 'settings-before.json').read_text())
    after = settings()
    result = {'serverClosed': not (STATE / 'runtime.json').exists(), 'personalSettingsUnchanged': before == after,
        'changedConfigFileCount': sum(before.get(key) != value for key, value in after.items())}
    assert result['serverClosed'] and result['personalSettingsUnchanged']
    save('finish-result.json', result)
    print(json.dumps(result))
