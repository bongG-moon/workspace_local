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
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid

from .history import HistoryStore, safe
from .windows_process import powershell_path
from .owned_process import run_owned

ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE = 2 * 1024 * 1024
BUSY = {'starting', 'running', 'approval', 'question'}
FAILURE_REASONS = frozenset({'unknown', 'preflight_failed', 'connection_unavailable',
    'handoff_prepare_failed', 'handoff_expired', 'handoff_failed',
    'draft_recovery_pending', 'draft_record_unreadable', 'draft_archive_failed', 'handoff_already_pending',
    'execution_mode_mismatch',
    'cleanup_unconfirmed', 'old_app_still_open', 'new_app_launch_failed', 'new_app_not_ready',
    'launcher_cleanup_pending'})
FAILURE_PHASES = frozenset({'preflight', 'connecting', 'waiting', 'preparing',
    'restarting', 'opening', 'ready_check'})


def failure_details(error, phase):
    """Only fixed diagnostic codes leave the coordinator; never exception text."""
    phase = phase if isinstance(phase, str) and phase in FAILURE_PHASES else 'preparing'
    reason = getattr(error, 'reason', None)
    if not isinstance(reason, str) or reason not in FAILURE_REASONS or reason == 'unknown':
        reason = {'preflight': 'preflight_failed', 'connecting': 'connection_unavailable',
                  'opening': 'new_app_launch_failed', 'ready_check': 'new_app_not_ready',
                  'restarting': 'old_app_still_open'}.get(phase, 'handoff_failed')
    return {'phase': phase, 'reason': reason}


class HandoffError(ValueError):
    def __init__(self, message, *, reason='unknown'):
        super().__init__(message)
        self.reason = reason if isinstance(reason, str) and reason in FAILURE_REASONS else 'unknown'


def prepare_failure_reason(code, reply):
    """Recognize fixed v0.23.32 errors without exporting arbitrary server text.

    Old apps expose no structured reason field. Exact matching lets the new
    coordinator explain a rejected prepare without changing, acknowledging or
    deleting the old app's recovery record. Unknown/authentication errors stay
    generic; a prefix, substring or a server-supplied code is not trusted.
    """
    known = {
        '이전 창의 작성 내용을 먼저 복원해 주세요.': 'draft_recovery_pending',
        '이전 창의 작성 내용을 확인하지 못했습니다. 저장 기록은 보존했습니다.': 'draft_record_unreadable',
        '다른 버전 전환을 준비하고 있습니다. 기존 요청을 먼저 마쳐 주세요.': 'handoff_already_pending',
        '다른 실행 권한으로 변경하려면 앱을 완전 종료한 뒤 Windows에서 다시 실행해 주세요.': 'execution_mode_mismatch',
    }
    error = reply.get('error') if isinstance(reply, dict) else None
    if code in (400, 409) and isinstance(error, str):
        return known.get(error, 'handoff_prepare_failed')
    return 'handoff_prepare_failed'


class LauncherExplainedError(HandoffError):
    """The startup helper already displayed its specific failure dialog."""


class LauncherCleanupPending(HandoffError):
    """Keep exact ownership and the coordinator lock after failed cleanup."""
    def __init__(self, process):
        super().__init__('새 앱 실행기의 종료를 확인하지 못했습니다. 중복 실행을 막고 기존 실행기가 끝나기를 기다립니다.',
                         reason='launcher_cleanup_pending')
        self.process = process

    def wait_for_exit(self):
        try:
            self.process.wait()
        except (OSError, subprocess.SubprocessError):
            # An unusable wait handle cannot prove exit. Keep one dormant
            # coordinator and its deduplication lock; never search by PID/name
            # or repeatedly start more PowerShell helpers to recover it.
            threading.Event().wait()


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


def read_status(client, path, *, sleep=time.sleep):
    """Tolerate brief loopback stalls only for observation, never a mutation.

    Each read already has the Client's five-second timeout. Two bounded retries
    let an overloaded old app respond without replaying prepare/stop/commit.
    HTTP/authentication errors and malformed responses are not retried.
    """
    for attempt in range(3):
        try:
            return client.request(path)
        except HTTPError:
            raise
        except (URLError, OSError):
            if attempt == 2:
                raise
            sleep(.25 * (attempt + 1))


def checked_health(client, *, demo, target_version, execution_mode=None, sleep=time.sleep):
    code, value = read_status(client, '/api/bootstrap', sleep=sleep)
    if (code != 200 or value.get('application') != 'company-workspace'
            or type(value.get('demo')) is not bool or value['demo'] != demo):
        raise HandoffError('실행 중인 앱 연결을 확인하지 못했습니다.')
    current, target = version(value.get('workspaceVersion')), version(target_version)
    if execution_mode is not None:
        from .execution_mode import checked_mode, EXECUTION_MODE_PROTOCOL
        checked_mode(execution_mode)
    if execution_mode is not None and value.get('executionMode') in {'normal', 'administrator'} and value['executionMode'] != execution_mode:
        raise HandoffError('다른 실행 권한의 앱이 열려 있습니다. 기존 앱에서 완전 종료한 뒤 원하는 Windows 권한으로 다시 실행해 주세요.',
                           reason='execution_mode_mismatch')
    policy = value.get('executionModeProtocol')
    mode_change = current == target and execution_mode is not None and (
        type(policy) is not int or policy < EXECUTION_MODE_PROTOCOL)
    if current >= target and not mode_change:
        return value, False
    return value, True


def legacy_idle(client, health, *, sleep=time.sleep):
    rows = health.get('sessions')
    if not isinstance(rows, list) or len(rows) > 500 or health.get('closing'):
        return False
    for row in rows:
        sid = row.get('id') if isinstance(row, dict) else None
        if not isinstance(sid, str) or str(uuid.UUID(sid)) != sid or row.get('state') in BUSY:
            return False
        status, task = read_status(client, '/api/session?id=' + sid, sleep=sleep)
        if status != 200 or task.get('state') not in {'idle', 'done', 'stopped', 'error'}:
            return False
        if task.get('requests') or task.get('choice') or (task.get('verification') or {}).get('state') in {'checking', 'needs-review'}:
            return False
        status, dispatch = read_status(client, '/api/dispatch?id=' + sid, sleep=sleep)
        if status != 200 or not isinstance(dispatch.get('queue'), list) or not isinstance(dispatch.get('schedules'), list):
            return False
        if any(item.get('status') in {'queued', 'dispatching', 'submitted'} for item in dispatch['queue']):
            return False
    return True


def run_helper(function, *, failure_reason=None, runner=run_owned):
    helper = safe(ROOT / 'deploy/CompanyWorkspace.Startup.ps1')
    # Function comes only from this fixed internal allowlist. No URL, token or
    # untrusted server text becomes script source or a command-line argument.
    if function not in {'Confirm-WorkspaceLegacyUpgrade', 'Show-WorkspaceUpgradeFailure', 'Show-WorkspaceUpgradeWaiting'}:
        raise HandoffError('전환 안내를 확인하지 못했습니다.')
    argument = ''
    if failure_reason is not None:
        if function != 'Show-WorkspaceUpgradeFailure' or not isinstance(failure_reason, str) or failure_reason not in FAILURE_REASONS:
            raise HandoffError('전환 안내를 확인하지 못했습니다.')
        argument = " -Reason '" + failure_reason + "'"
    source = ". '" + str(helper).replace("'", "''") + "'; if (" + function + argument + ') { exit 0 } else { exit 1 }'
    result = runner([powershell_path(), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                     '-WindowStyle', 'Hidden', '-Command', source],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=3600)
    return result.returncode == 0


def native_runtime_probe(directory, *, runner=run_owned):
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


def retain_legacy_recovery(client, runtime_path, *, sleep=time.sleep):
    """Migrate a blocking old-app record without losing or restoring its text.

    v0.23.32 cannot accept prepare while its previous recovery record exists.
    The new coordinator first preserves the exact authenticated record in the
    new app's durable recovery archive, then acknowledges only that old record.
    An uncertain acknowledgement is never retried and never permits commit.
    """
    from .upgrade_handoff import preserve_recovery
    code, status = read_status(client, '/api/upgrade', sleep=sleep)
    restore = status.get('upgradeRestore') if isinstance(status, dict) else None
    if code != 200 or not isinstance(restore, dict):
        raise HandoffError('이전 초안의 보관 상태를 확인하지 못했습니다. 기존 앱에서 다시 시도해 주세요.',
                           reason='draft_recovery_pending')
    try:
        preserve_recovery(Path(runtime_path).parent, restore)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise HandoffError('이전 초안을 별도로 보관하지 못했습니다. 기존 앱과 작성 내용은 유지됩니다.',
                           reason='draft_archive_failed') from error
    code, reply = client.request('/api/upgrade', {'action': 'restored', 'requestId': restore['requestId']})
    if (code != 200 or reply.get('ok') is not True or 'upgradeRestore' not in reply
            or reply['upgradeRestore'] is not None):
        raise HandoffError('이전 초안의 보관 전환을 확인하지 못했습니다. 보관 기록은 유지됩니다.',
                           reason='draft_recovery_pending')


def transfer(client, runtime_path, *, demo, target_version, request_id, legacy_confirm,
             no_browser=False, execution_mode=None, on_wait=lambda: None, on_phase=lambda phase: None,
             sleep=time.sleep, clock=time.monotonic, timeout=7200):
    health, needs_upgrade = checked_health(client, demo=demo, target_version=target_version,
                                           execution_mode=execution_mode, sleep=sleep)
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
        if prepare_failure_reason(code, reply) == 'draft_recovery_pending':
            # A rejected prepare has not admitted any shutdown. This one
            # compatibility migration is bounded; no other failure, storage
            # warning or ambiguous mutation is treated as permission to retry.
            retain_legacy_recovery(client, runtime_path, sleep=sleep)
            code, reply = client.request('/api/upgrade', {'action': 'prepare', **request})
        if code not in (200, 202):
            raise HandoffError('새 버전 전환을 준비하지 못했습니다.', reason=prepare_failure_reason(code, reply))
        while clock() < deadline:
            snapshot = reply.get('upgrade')
            if not isinstance(snapshot, dict) or snapshot.get('requestId') != request_id:
                raise HandoffError('전환 요청의 소유자를 확인하지 못했습니다.')
            stage = snapshot.get('stage')
            if stage == 'captured':
                try:
                    on_phase('preparing')
                except (OSError, RuntimeError):
                    # Failed UI preparation is still before shutdown. Release
                    # the captured frontend using this exact owned request.
                    client.request('/api/upgrade', {'action': 'cancel', **request})
                    raise
                code, reply = client.request('/api/upgrade', {'action': 'commit', **request}, timeout=60)
                if code in (200, 202) and reply.get('closed') is True:
                    break
                # Only an explicit pre-close busy reply permits another attempt.
                if code != 409 or (reply.get('upgrade') or {}).get('stage') not in {'waiting', 'capture'}:
                    raise HandoffError('이전 앱의 종료 완료를 확인하지 못했습니다.', reason='cleanup_unconfirmed')
                on_phase('waiting')
            elif stage not in {'waiting', 'capture'}:
                return 'cancelled' if stage in {'cancelled', 'expired'} else 'failed'
            sleep(1)
            code, reply = read_status(client, '/api/upgrade?requestId=' + request_id, sleep=sleep)
            if code != 200:
                raise HandoffError('전환 상태를 확인하지 못했습니다.')
        else:
            client.request('/api/upgrade', {'action': 'cancel', **request})
            return 'expired'
    else:
        wait_announced = False
        while clock() < deadline:
            health, needed = checked_health(client, demo=demo, target_version=target_version, sleep=sleep)
            if not needed:
                return 'reused'
            if legacy_idle(client, health, sleep=sleep):
                if not legacy_confirm():
                    return 'cancelled'
                # Consent is for this idle moment, not arbitrary later work.
                health, needed = checked_health(client, demo=demo, target_version=target_version, sleep=sleep)
                if not needed or not legacy_idle(client, health, sleep=sleep):
                    raise HandoffError('전환 확인 중 업무 상태가 바뀌었습니다. 기존 업무를 유지합니다.')
                on_phase('preparing')
                code, reply = client.request('/api/quit', {}, timeout=60)
                if code != 200 or reply.get('closed') is not True:
                    raise HandoffError('이전 앱의 종료 완료를 확인하지 못했습니다.', reason='cleanup_unconfirmed')
                break
            if not wait_announced:
                on_wait()
                wait_announced = True
            sleep(1)
        else:
            return 'expired'
    on_phase('restarting')
    if not wait_shutdown(client, runtime_path, sleep=sleep, clock=clock):
        raise HandoffError('이전 앱 창의 정리가 끝나지 않았습니다.', reason='old_app_still_open')
    return 'closed'


def wait_replacement(runtime_path, *, previous_token, demo, target_version, execution_mode,
                     no_browser=False, client_factory=Client, sleep=time.sleep, clock=time.monotonic, timeout=60):
    """A launcher exit is not success: authenticate the fresh app and its UI.

    Polling exists only during this bounded handoff. Credentials stay in memory;
    neither the visible card nor result files contain endpoints or tokens.
    """
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            origin, token = endpoint(runtime_path)
            if token != previous_token:
                code, value = client_factory(origin, token).request('/api/bootstrap', timeout=3)
                if (code == 200 and value.get('application') == 'company-workspace'
                        and value.get('demo') is demo and value.get('workspaceVersion') == target_version
                        and value.get('executionMode') == execution_mode and not value.get('closing')):
                    if no_browser:
                        return True
                    window = value.get('window') or {}
                    if isinstance(window, dict) and re.fullmatch(r'[a-f0-9]{32}', str(window.get('launchId', ''))):
                        if window.get('startupState') == 'ready':
                            return True
                        if window.get('startupState') == 'failed':
                            raise HandoffError('새 버전의 화면을 준비하지 못했습니다. 앱을 다시 열어 주세요.', reason='new_app_not_ready')
        except HandoffError:
            raise
        except (OSError, ValueError, KeyError, URLError):
            # Runtime is atomically replaced. A transient missing/partial file,
            # refused socket or early startup response is not ready evidence.
            pass
        sleep(.5)
    raise HandoffError('새 버전의 화면이 준비되었는지 확인하지 못했습니다. 앱을 다시 열어 주세요.', reason='new_app_not_ready')


def launch(state, python, *, demo=False, no_browser=False, execution_mode=None, popen=subprocess.Popen):
    executable = safe(Path(python))
    if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() != '.exe':
        raise HandoffError('확인한 Python 경로를 찾지 못했습니다.')
    arguments = [powershell_path(), '-NoLogo', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                 '-File', str(safe(ROOT / 'deploy/Start-CompanyWorkspace.ps1')),
                 '-PythonCommand', str(executable), '-StateRoot', str(state),
                 '-ExecutionRequestId', uuid.uuid4().hex]
    # The coordinator owns progress and errors. This existing launcher handoff
    # marker suppresses its blocking error dialog so it cannot sit behind our
    # progress card; it does not change the requested Windows execution role.
    if execution_mode is not None:
        from .execution_mode import checked_mode
        arguments.extend(['-ExecutionMode', checked_mode(execution_mode)])
    if demo:
        arguments.append('-Demo')
    if no_browser:
        arguments.append('-NoBrowser')
    process = popen(arguments, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        # Keep the coordinator's deduplication lock until its startup helper
        # exits. The server is deliberately detached and has its own lifetime.
        code = process.wait(timeout=90)
    except subprocess.TimeoutExpired:
        # Only this retained startup handle is ours to stop. Killing its tree
        # would also stop a successfully detached app or upgrade coordinator.
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            raise LauncherCleanupPending(process) from None
        raise HandoffError('새 앱 실행기 응답 시간이 초과되어 실행기만 정리했습니다.') from None
    if code == 20:
        raise LauncherExplainedError('새 앱 실행기의 안내를 확인해 주세요.')
    if code != 0:
        raise HandoffError('새 앱 실행기를 완료하지 못했습니다.')
    return process


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
    parser.add_argument('--target-version')
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
            or not re.fullmatch(r'[a-f0-9]{32}', args.request_id or '')
            or args.target_version is not None and not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', args.target_version)):
        return 42
    runtime_state = args.state / 'demo' if args.demo else args.state
    status_path = safe(runtime_state / ('upgrade-launch-' + args.request_id + '.json'))
    phase = 'preflight'
    def report(status, failure=None):
        # No endpoint credentials, paths, prompt contents or server error text.
        path = status_path if status in {'accepted', 'already_waiting'} else safe(runtime_state / 'upgrade-last-result.json')
        record = {'requestId': args.request_id, 'status': status}
        if args.target_version is not None:
            record['targetVersion'] = args.target_version
        if failure is not None:
            record.update(failure_details(failure, phase))
        if getattr(progress, 'diagnostic', None) == 'progress_unavailable':
            record['progressWarning'] = 'progress_unavailable'
        HistoryStore(runtime_state)._write(path, json.dumps(record).encode('utf-8'))
    from .update_progress import UpdateProgress
    from .appearance import Appearance
    progress = UpdateProgress(enabled=not args.no_browser,
                              theme=Appearance(runtime_state).snapshot()['effective'])
    def show_phase(value):
        nonlocal phase
        if value in FAILURE_PHASES:
            phase = value
        progress.phase(value)
    def explain_failure(error):
        if not args.no_browser:
            run_helper('Show-WorkspaceUpgradeFailure', failure_reason=failure_details(error, phase)['reason'])
    try:
        prune_acknowledgements(runtime_state)
        terminal_report = None
        with update_lock(runtime_state) as held:
            if not held:
                report('already_waiting')
                return 0
            report('accepted')
            from .startup import verify_process
            execution_mode = verify_process(execution_mode=args.execution_mode)
            preflight(no_browser=args.no_browser)
            phase = 'connecting'
            runtime = runtime_state / 'runtime.json'
            origin, token = endpoint(runtime)
            from .server import WORKSPACE_VERSION
            if args.target_version is not None and args.target_version != WORKSPACE_VERSION:
                raise HandoffError('실행할 앱 버전이 요청과 다릅니다.', reason='preflight_failed')
            phase = 'preparing'
            outcome = transfer(Client(origin, token), runtime, demo=args.demo, target_version=WORKSPACE_VERSION,
                               execution_mode=execution_mode,
                               request_id=args.request_id, no_browser=args.no_browser,
                               legacy_confirm=lambda: run_helper('Confirm-WorkspaceLegacyUpgrade'),
                               on_wait=lambda: run_helper('Show-WorkspaceUpgradeWaiting'), on_phase=show_phase)
            outcome_failure = HandoffError('', reason='handoff_expired' if outcome == 'expired' else 'handoff_failed')
            if outcome in {'failed', 'expired', 'cancelled'}:
                terminal_report = (outcome, outcome_failure if outcome in {'failed', 'expired'} else None)
            else:
                report(outcome)
            if outcome == 'closed':
                try:
                    show_phase('opening')
                    launch(args.state, args.python, demo=args.demo, no_browser=args.no_browser, execution_mode=execution_mode)
                    phase = 'ready_check'
                    wait_replacement(runtime, previous_token=token, demo=args.demo, target_version=WORKSPACE_VERSION,
                                     execution_mode=execution_mode, no_browser=args.no_browser)
                    report('restarted')
                except LauncherCleanupPending as exc:
                    # Stay inside update_lock until this exact launcher exits.
                    # A failed termination must not allow another replacement
                    # PowerShell to be created by each repeated app launch.
                    report('failed', exc)
                    progress.close()
                    if not args.no_browser:
                        try:
                            explain_failure(exc)
                        except (OSError, subprocess.SubprocessError):
                            pass
                    exc.wait_for_exit()
                    return 39
        # A visible failure dialog must not retain the deduplication lock.
        # Publish a retryable terminal result only after that lock is released.
        if terminal_report:
            report(*terminal_report)
            if terminal_report[1] is not None and not args.no_browser:
                progress.close()
                explain_failure(terminal_report[1])
        return 0
    except LauncherExplainedError as exc:
        report('failed', exc)
        return 39
    except (ValueError, OSError, KeyError, ImportError, RuntimeError, subprocess.SubprocessError) as exc:
        report('failed', exc)
        progress.close()
        if not args.no_browser:
            try:
                explain_failure(exc)
            except (OSError, subprocess.SubprocessError):
                pass
        return 39
    finally:
        progress.close()


if __name__ == '__main__':
    raise SystemExit(main())
