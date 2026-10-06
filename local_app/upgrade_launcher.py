"""Detached, same-user version handoff. Never terminates a process by PID.

The running server owns idle admission and draft capture. Legacy releases get
one compatibility confirmation immediately before their ordinary quit API.
Only loopback endpoints read from the selected state directory are contacted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from itertools import islice
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid

from .history import safe
from .windows_process import powershell_path

ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE = 2 * 1024 * 1024
BUSY = {'starting', 'running', 'approval', 'question'}


class HandoffError(ValueError):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', value):
        raise HandoffError('실행 중인 앱 버전을 확인하지 못했습니다.')
    return tuple(map(int, value.split('.')))


def endpoint(path):
    raw = safe(Path(path)).read_bytes()
    if len(raw) > 16384:
        raise HandoffError('앱 연결 정보를 확인하지 못했습니다.')
    value = json.loads(raw.decode('utf-8-sig'))
    uri = urlsplit(value['url'])
    if (uri.scheme != 'http' or uri.hostname != '127.0.0.1' or uri.path != '/' or uri.query
            or uri.username or uri.password or not uri.port or uri.port == 80
            or not re.fullmatch(r'token=[A-Za-z0-9_-]{40,100}', uri.fragment)):
        raise HandoffError('앱 연결 정보를 확인하지 못했습니다.')
    return 'http://127.0.0.1:' + str(uri.port), uri.fragment[6:]


class Client:
    def __init__(self, origin, token):
        self.origin, self.token = origin, token
        # Local app traffic must not travel through a configured HTTP proxy.
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, data=None, *, timeout=5):
        request = Request(self.origin + path, data=None if data is None else json.dumps(data).encode('utf-8'),
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise HandoffError('앱 응답의 확인 범위를 초과했습니다.')
            value = json.loads(raw.decode('utf-8'))
            if not isinstance(value, dict):
                raise HandoffError('앱 응답 형식을 확인하지 못했습니다.')
            return response.status, value


def checked_health(client, *, demo, target_version, execution_mode=None):
    code, value = client.request('/api/bootstrap')
    if (code != 200 or value.get('application') != 'company-workspace'
            or type(value.get('demo')) is not bool or value['demo'] != demo):
        raise HandoffError('실행 중인 앱 연결을 확인하지 못했습니다.')
    current, target = version(value.get('workspaceVersion')), version(target_version)
    if execution_mode is not None:
        from .execution_mode import checked_mode, EXECUTION_MODE_PROTOCOL
        checked_mode(execution_mode)
    if execution_mode is not None and value.get('executionMode') in {'normal', 'administrator'} and value['executionMode'] != execution_mode:
        raise HandoffError('다른 실행 권한의 앱이 열려 있습니다. 기존 앱에서 완전 종료한 뒤 원하는 Windows 권한으로 다시 실행해 주세요.')
    policy = value.get('executionModeProtocol')
    mode_change = current == target and execution_mode is not None and (
        type(policy) is not int or policy < EXECUTION_MODE_PROTOCOL)
    if current >= target and not mode_change:
        return value, False
    return value, True


def legacy_idle(client, health):
    rows = health.get('sessions')
    if not isinstance(rows, list) or len(rows) > 500 or health.get('closing'):
        return False
    for row in rows:
        sid = row.get('id') if isinstance(row, dict) else None
        if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid or row.get('state') in BUSY:
            return False
        status, task = client.request('/api/session?id=' + sid)
        if status != 200 or task.get('state') not in {'idle', 'done', 'stopped', 'error'}:
            return False
        if task.get('requests') or task.get('choice') or (task.get('verification') or {}).get('state') in {'checking', 'needs-review'}:
            return False
        status, dispatch = client.request('/api/dispatch?id=' + sid)
        if status != 200 or not isinstance(dispatch.get('queue'), list) or not isinstance(dispatch.get('schedules'), list):
            return False
        if any(item.get('status') in {'queued', 'dispatching', 'submitted'} for item in dispatch['queue']):
            return False
    return True


def run_helper(function, *, runner=subprocess.run):
    helper = safe(ROOT / 'deploy/CompanyWorkspace.Startup.ps1')
    # Function comes only from this fixed internal allowlist. No URL, token or
    # untrusted server text becomes script source or a command-line argument.
    if function not in {'Confirm-WorkspaceLegacyUpgrade', 'Show-WorkspaceUpgradeFailure', 'Show-WorkspaceUpgradeWaiting'}:
        raise HandoffError('전환 안내를 확인하지 못했습니다.')
    source = ". '" + str(helper).replace("'", "''") + "'; if (" + function + ') { exit 0 } else { exit 1 }'
    result = runner([powershell_path(), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                     '-WindowStyle', 'Hidden', '-Command', source],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=3600)
    return result.returncode == 0


def native_runtime_probe(directory, *, runner=subprocess.run):
    """Query through system PowerShell matching the bundled x64 desktop host.

    Python may legitimately be 32-bit. Loading the desktop's x64 native DLL
    into Python would reject an otherwise usable installation. Sysnative is
    exposed only to 32-bit callers on 64-bit Windows and avoids redirection.
    """
    powershell = Path(powershell_path())
    native = powershell.parents[3] / 'Sysnative/WindowsPowerShell/v1.0/powershell.exe'
    if native.is_file():
        powershell = native
    core = safe(Path(directory) / 'Microsoft.Web.WebView2.Core.dll')
    source = ("$ErrorActionPreference='Stop'; try { [Reflection.Assembly]::LoadFrom('"
              + str(core).replace("'", "''") + "') | Out-Null; "
              "$value=[Microsoft.Web.WebView2.Core.CoreWebView2Environment]::GetAvailableBrowserVersionString(); "
              "if ([string]::IsNullOrWhiteSpace($value)) { exit 46 }; exit 0 } catch { exit 46 }")
    result = runner([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                     '-WindowStyle', 'Hidden', '-Command', source], cwd=directory,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=4)
    if result.returncode != 0:
        raise HandoffError('WebView2 실행 조건을 확인하지 못했습니다.')


def preflight(*, no_browser=False):
    for file in ('deploy/Start-CompanyWorkspace.ps1', 'deploy/CompanyWorkspace.Startup.ps1',
                 'local_app/server.py', 'local_app/upgrade_launcher.py'):
        if not safe(ROOT / file).is_file():
            raise HandoffError('새 앱 파일을 확인하지 못했습니다.')
    # Import the selected payload before asking a healthy old app to stop.
    from . import server  # noqa: F401
    if no_browser:
        return
    from .native_window import desktop_executable
    executable = safe(desktop_executable())
    for name in ('Workspace.Desktop.exe', 'Microsoft.Web.WebView2.Core.dll',
                 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll'):
        if not safe(executable.parent / name).is_file():
            raise HandoffError('전용 앱 창의 실행 파일을 확인하지 못했습니다.')
    if os.name == 'nt':
        native_runtime_probe(executable.parent)


@contextmanager
def update_lock(state):
    """OS-owned lock; a crashed coordinator cannot leave a permanent lock."""
    path = safe(Path(state) / 'upgrade-launch.lock')
    with path.open('a+b') as stream:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def wait_shutdown(client, runtime_path, *, sleep=time.sleep, clock=time.monotonic, timeout=60):
    deadline = clock() + timeout
    while clock() < deadline:
        alive = True
        try:
            # HTTP errors still prove that the endpoint is occupied.
            # Windows can take just over two seconds to return WSAECONNREFUSED.
            client.request('/api/bootstrap', timeout=5)
        except (URLError, OSError) as error:
            reason = error.reason if isinstance(error, URLError) else error
            # A timeout or malformed response is not evidence of termination.
            # Only an explicit refusal proves the old loopback listener is gone.
            alive = not (isinstance(reason, ConnectionRefusedError)
                         or getattr(reason, 'errno', None) in {61, 111, 10061}
                         or getattr(reason, 'winerror', None) == 10061)
        if not alive and not Path(runtime_path).exists():
            return True
        sleep(.25)
    return False


def transfer(client, runtime_path, *, demo, target_version, request_id, legacy_confirm,
             no_browser=False, execution_mode=None, on_wait=lambda: None, sleep=time.sleep, clock=time.monotonic, timeout=7200):
    health, needs_upgrade = checked_health(client, demo=demo, target_version=target_version, execution_mode=execution_mode)
    if not needs_upgrade:
        if not no_browser:
            client.request('/api/window/open', {})
        return 'reused'
    protocol = health.get('upgradeProtocol') == 1
    if not protocol and no_browser:
        raise HandoffError('이전 버전은 화면에서 전환 확인이 필요합니다.')
    request = {'requestId': request_id, 'targetVersion': target_version}
    if execution_mode is not None:
        request['executionMode'] = execution_mode
    deadline = clock() + timeout
    if protocol:
        code, reply = client.request('/api/upgrade', {'action': 'prepare', **request})
        if code not in (200, 202):
            raise HandoffError('새 버전 전환을 준비하지 못했습니다.')
        while clock() < deadline:
            snapshot = reply.get('upgrade')
            if not isinstance(snapshot, dict) or snapshot.get('requestId') != request_id:
                raise HandoffError('전환 요청의 소유자를 확인하지 못했습니다.')
            stage = snapshot.get('stage')
            if stage == 'captured':
                code, reply = client.request('/api/upgrade', {'action': 'commit', **request}, timeout=60)
                if code in (200, 202) and reply.get('closed') is True:
                    break
                # Only an explicit pre-close busy reply permits another attempt.
                if code != 409 or (reply.get('upgrade') or {}).get('stage') not in {'waiting', 'capture'}:
                    raise HandoffError('이전 앱의 종료 완료를 확인하지 못했습니다.')
            elif stage not in {'waiting', 'capture'}:
                return 'cancelled' if stage in {'cancelled', 'expired'} else 'failed'
            sleep(1)
            code, reply = client.request('/api/upgrade?requestId=' + request_id)
            if code != 200:
                raise HandoffError('전환 상태를 확인하지 못했습니다.')
        else:
            client.request('/api/upgrade', {'action': 'cancel', **request})
            return 'expired'
    else:
        wait_announced = False
        while clock() < deadline:
            health, needed = checked_health(client, demo=demo, target_version=target_version)
            if not needed:
                return 'reused'
            if legacy_idle(client, health):
                if not legacy_confirm():
                    return 'cancelled'
                # Consent is for this idle moment, not arbitrary later work.
                health, needed = checked_health(client, demo=demo, target_version=target_version)
                if not needed or not legacy_idle(client, health):
                    raise HandoffError('전환 확인 중 업무 상태가 바뀌었습니다. 기존 업무를 유지합니다.')
                code, reply = client.request('/api/quit', {}, timeout=60)
                if code != 200 or reply.get('closed') is not True:
                    raise HandoffError('이전 앱의 종료 완료를 확인하지 못했습니다.')
                break
            if not wait_announced:
                on_wait()
                wait_announced = True
            sleep(1)
        else:
            return 'expired'
    if not wait_shutdown(client, runtime_path, sleep=sleep, clock=clock):
        raise HandoffError('이전 앱 창의 정리가 끝나지 않았습니다.')
    return 'closed'


def launch(state, python, *, demo=False, no_browser=False, execution_mode=None, popen=subprocess.Popen):
    executable = safe(Path(python))
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != '.exe':
        raise HandoffError('확인한 Python 경로를 찾지 못했습니다.')
    arguments = [powershell_path(), '-NoLogo', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                 '-File', str(safe(ROOT / 'deploy/Start-CompanyWorkspace.ps1')),
                 '-PythonCommand', str(executable), '-StateRoot', str(state)]
    if execution_mode is not None:
        from .execution_mode import checked_mode
        arguments.extend(['-ExecutionMode', checked_mode(execution_mode)])
    if demo:
        arguments.append('-Demo')
    if no_browser:
        arguments.append('-NoBrowser')
    return popen(arguments, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def prune_acknowledgements(state, *, now=time.time):
    # Normal launchers consume their own ACK. Bounded recovery for a launcher
    # that was interrupted before consumption; never scan other directories.
    for path in islice(Path(state).glob('upgrade-launch-*.json'), 128):
        if not re.fullmatch(r'upgrade-launch-[a-f0-9]{32}\.json', path.name):
            continue
        try:
            if safe(path).stat().st_mtime < now() - 86400:
                path.unlink()
        except (ValueError, OSError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--state', type=Path)
    parser.add_argument('--python')
    parser.add_argument('--request-id')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--execution-mode', choices=['auto', 'normal', 'administrator'], default='auto')
    args = parser.parse_args()
    if args.preflight:
        try:
            preflight(no_browser=args.no_browser)
            print('{"ok":true}')
            return 0
        except (ValueError, OSError, ImportError, AttributeError, subprocess.SubprocessError):
            print('{"ok":false}')
            return 47
    if (args.state is None or not args.state.is_absolute() or not args.python
            or not re.fullmatch(r'[a-f0-9]{32}', args.request_id or '')):
        return 42
    runtime_state = args.state / 'demo' if args.demo else args.state
    status_path = safe(runtime_state / ('upgrade-launch-' + args.request_id + '.json'))
    def report(status):
        # No endpoint credentials, paths, prompt contents or server error text.
        path = status_path if status in {'accepted', 'already_waiting'} else safe(runtime_state / 'upgrade-last-result.json')
        path.write_text(json.dumps({'requestId': args.request_id, 'status': status}), encoding='utf-8')
    try:
        prune_acknowledgements(runtime_state)
        with update_lock(runtime_state) as held:
            if not held:
                report('already_waiting')
                return 0
            report('accepted')
            from .startup import verify_process
            execution_mode = verify_process(execution_mode=args.execution_mode)
            preflight(no_browser=args.no_browser)
            runtime = runtime_state / 'runtime.json'
            origin, token = endpoint(runtime)
            from .server import WORKSPACE_VERSION
            outcome = transfer(Client(origin, token), runtime, demo=args.demo, target_version=WORKSPACE_VERSION,
                               execution_mode=execution_mode,
                               request_id=args.request_id, no_browser=args.no_browser,
                               legacy_confirm=lambda: run_helper('Confirm-WorkspaceLegacyUpgrade'),
                               on_wait=lambda: run_helper('Show-WorkspaceUpgradeWaiting'))
            report(outcome)
            if outcome == 'closed':
                launch(args.state, args.python, demo=args.demo, no_browser=args.no_browser, execution_mode=execution_mode)
            elif outcome in {'failed', 'expired'} and not args.no_browser:
                run_helper('Show-WorkspaceUpgradeFailure')
            return 0
    except (ValueError, OSError, KeyError, ImportError, RuntimeError, subprocess.SubprocessError):
        report('failed')
        if not args.no_browser:
            try:
                run_helper('Show-WorkspaceUpgradeFailure')
            except (OSError, subprocess.SubprocessError):
                pass
        return 39


if __name__ == '__main__':
    raise SystemExit(main())
