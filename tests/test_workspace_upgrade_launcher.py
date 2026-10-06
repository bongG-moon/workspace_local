"""Handoff only after authenticated idle/capture/cleanup evidence."""
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from local_app import upgrade_launcher as upgrade

SID = '12345678-1234-1234-1234-123456789abc'
RID = 'a' * 32
TOKEN = 'b' * 43


def health(protocol=1, version='0.21.4'):
    return {'application': 'company-workspace', 'workspaceVersion': version, 'demo': False,
            'upgradeProtocol': protocol, 'closing': False,
            'sessions': [{'id': SID, 'state': 'idle'}]}


def snapshot(stage):
    return {'upgrade': {'stage': stage, 'requestId': RID}}


class FakeClient:
    def __init__(self, *, protocol=1, stages=None, commit=None):
        self.health = health(protocol)
        self.stages = list(stages or ['captured'])
        self.commit = commit or (200, {'closed': True})
        self.calls = []
        self.task = {'state': 'idle', 'requests': [], 'choice': None}
        self.dispatch = {'queue': [], 'schedules': []}

    def request(self, path, data=None, **kwargs):
        self.calls.append((path, data))
        if path == '/api/bootstrap':
            return 200, self.health
        if path == '/api/window/open':
            return 200, {'ok': True}
        if path == '/api/session?id=' + SID:
            return 200, self.task
        if path == '/api/dispatch?id=' + SID:
            return 200, self.dispatch
        if path == '/api/quit' or path == '/api/upgrade' and data['action'] == 'commit':
            if isinstance(self.commit, Exception):
                raise self.commit
            return self.commit
        if path == '/api/upgrade' and data['action'] == 'cancel':
            return 200, snapshot('cancelled')
        return 200, snapshot(self.stages.pop(0) if len(self.stages) > 1 else self.stages[0])


class UpgradeTransferTests(unittest.TestCase):
    def transfer(self, client, **kwargs):
        clock = [0]
        def sleep(duration):
            clock[0] += duration
        with patch.object(upgrade, 'wait_shutdown', return_value=True):
            return upgrade.transfer(client, Path('unused'), demo=False, target_version='0.21.5',
                request_id=RID, legacy_confirm=kwargs.pop('legacy_confirm', lambda: True),
                clock=lambda: clock[0], sleep=kwargs.pop('sleep', sleep), timeout=5, **kwargs)

    def test_waits_for_capture_then_commits_once(self):
        client = FakeClient(stages=['waiting', 'capture', 'captured'])
        self.assertEqual('closed', self.transfer(client))
        self.assertEqual(1, sum(bool(data and data.get('action') == 'commit') for _, data in client.calls))
        self.assertEqual(2, sum(path.startswith('/api/upgrade?') for path, _ in client.calls))

    def test_same_and_newer_versions_reuse_without_quit(self):
        for current in ('0.21.5', '0.22.0'):
            client = FakeClient(); client.health['workspaceVersion'] = current
            self.assertEqual('reused', self.transfer(client))
            self.assertEqual(['/api/bootstrap', '/api/window/open'], [path for path, _ in client.calls])

    def test_wrong_application_or_demo_rejected(self):
        for field, value in [('application', 'other'), ('demo', True), ('demo', 'false')]:
            client = FakeClient(); client.health[field] = value
            with self.assertRaises(upgrade.HandoffError):
                self.transfer(client)
            self.assertEqual(1, len(client.calls))

    def test_unknown_version_never_quits(self):
        client = FakeClient(); client.health['workspaceVersion'] = 'legacy-build'
        with self.assertRaises(upgrade.HandoffError):
            self.transfer(client)

    def test_cancelled_and_failed_captures_never_quit(self):
        for stage in ('cancelled', 'expired', 'failed'):
            client = FakeClient(stages=[stage])
            self.assertEqual('failed' if stage == 'failed' else 'cancelled', self.transfer(client))
            self.assertFalse(any(data and data.get('action') == 'commit' for _, data in client.calls))

    def test_ambiguous_commit_never_retries(self):
        client = FakeClient(commit=URLError('network lost'))
        with self.assertRaises(URLError):
            self.transfer(client)
        self.assertEqual(1, sum(bool(data and data.get('action') == 'commit') for _, data in client.calls))

    def test_cleanup_failure_never_reports_closed(self):
        for reply in [(503, {'closed': False}), (200, {'ok': True})]:
            with self.assertRaises(upgrade.HandoffError):
                self.transfer(FakeClient(commit=reply))

    def test_preclose_busy_commit_can_retry(self):
        client = FakeClient()
        original = client.request
        attempted = [False]
        def request(path, data=None, **kwargs):
            if path == '/api/upgrade' and data.get('action') == 'commit' and not attempted[0]:
                attempted[0] = True
                return 409, snapshot('waiting')
            return original(path, data, **kwargs)
        client.request = request
        self.assertEqual('closed', self.transfer(client))

    def test_timeout_cancels_prepared_transition(self):
        client = FakeClient(stages=['waiting'])
        self.assertEqual('expired', self.transfer(client))
        self.assertEqual('cancel', client.calls[-1][1]['action'])

    def test_mismatched_request_identity_never_commits(self):
        client = FakeClient()
        original = client.request
        def request(path, data=None, **kwargs):
            result = original(path, data, **kwargs)
            if path == '/api/upgrade':
                result[1]['upgrade']['requestId'] = 'c' * 32
            return result
        client.request = request
        with self.assertRaises(upgrade.HandoffError):
            self.transfer(client)

    def test_legacy_no_browser_has_no_implicit_confirmation(self):
        confirm = Mock(return_value=True)
        with self.assertRaises(upgrade.HandoffError):
            self.transfer(FakeClient(protocol=0), no_browser=True, legacy_confirm=confirm)
        confirm.assert_not_called()

    def test_legacy_confirms_once_at_idle(self):
        client = FakeClient(protocol=0)
        confirm = Mock(return_value=True)
        self.assertEqual('closed', self.transfer(client, legacy_confirm=confirm))
        confirm.assert_called_once()
        self.assertEqual(1, sum(path == '/api/quit' for path, _ in client.calls))
        self.assertEqual(2, sum(path.startswith('/api/session?') for path, _ in client.calls))

    def test_legacy_cancel_does_not_quit(self):
        client = FakeClient(protocol=0)
        self.assertEqual('cancelled', self.transfer(client, legacy_confirm=lambda: False))
        self.assertFalse(any(path == '/api/quit' for path, _ in client.calls))

    def test_legacy_work_started_during_confirmation_preserves_app(self):
        client = FakeClient(protocol=0)
        def confirm():
            client.task['state'] = 'running'
            return True
        with self.assertRaises(upgrade.HandoffError):
            self.transfer(client, legacy_confirm=confirm)
        self.assertFalse(any(path == '/api/quit' for path, _ in client.calls))

    def test_legacy_busy_announces_wait_only_once(self):
        client = FakeClient(protocol=0); client.task['state'] = 'running'
        notice, confirm = Mock(), Mock(return_value=True)
        self.assertEqual('expired', self.transfer(client, on_wait=notice, legacy_confirm=confirm))
        notice.assert_called_once(); confirm.assert_not_called()

    def test_legacy_pending_requests_choices_and_queue_block(self):
        cases = [{'requests': [{'id': 'permission'}]}, {'choice': {'id': 'design'}},
                 {'verification': {'state': 'checking'}}, {'verification': {'state': 'needs-review'}}]
        for change in cases:
            client = FakeClient(protocol=0); client.task.update(change)
            self.assertFalse(upgrade.legacy_idle(client, client.health))
        for status in ('queued', 'dispatching', 'submitted'):
            client = FakeClient(protocol=0); client.dispatch['queue'] = [{'status': status}]
            self.assertFalse(upgrade.legacy_idle(client, client.health))


class UpgradeBoundaryTests(unittest.TestCase):
    def test_native_probe_uses_sysnative_when_exposed_to_32bit_caller(self):
        with tempfile.TemporaryDirectory(prefix="probe ' & ") as raw:
            root = Path(raw)
            default = root / 'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
            native = root / 'Windows/Sysnative/WindowsPowerShell/v1.0/powershell.exe'
            native.parent.mkdir(parents=True); native.write_bytes(b'fixture')
            directory = root / 'desktop'; directory.mkdir()
            runner = Mock(return_value=Mock(returncode=0))
            with patch.object(upgrade, 'powershell_path', return_value=str(default)):
                upgrade.native_runtime_probe(directory, runner=runner)
            arguments = runner.call_args.args[0]
            self.assertEqual(str(native), arguments[0])
            self.assertIn('-NoProfile', arguments)
            self.assertIn(str(directory).replace("'", "''"), arguments[-1])
            self.assertEqual(4, runner.call_args.kwargs['timeout'])

    def test_native_probe_uses_system_powershell_without_sysnative(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            default = root / 'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
            runner = Mock(return_value=Mock(returncode=0))
            with patch.object(upgrade, 'powershell_path', return_value=str(default)):
                upgrade.native_runtime_probe(root, runner=runner)
            self.assertEqual(str(default), runner.call_args.args[0][0])

    def test_native_probe_rejects_unavailable_runtime_before_old_app_shutdown(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            default = root / 'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
            with patch.object(upgrade, 'powershell_path', return_value=str(default)):
                with self.assertRaises(upgrade.HandoffError):
                    upgrade.native_runtime_probe(root, runner=Mock(return_value=Mock(returncode=46)))

    def test_real_server_protocol_closes_only_after_headless_capture(self):
        from local_app.server import LocalApp, Server, WORKSPACE_VERSION
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            app = LocalApp(state, demo=True)
            app._upgrade_headless = True
            server = Server(app)
            runtime = state / 'runtime.json'
            runtime.write_text(json.dumps({'url': server.origin + '/#token=' + app.token}))
            def run():
                try:
                    server.serve_forever(poll_interval=.02)
                finally:
                    server.server_close()
                    runtime.unlink(missing_ok=True)
            thread = threading.Thread(target=run, daemon=True); thread.start()
            try:
                current = upgrade.version(WORKSPACE_VERSION)
                target = '.'.join(map(str, (current[0], current[1], current[2] + 1)))
                result = upgrade.transfer(upgrade.Client(server.origin, app.token), runtime,
                    demo=True, target_version=target, request_id=RID, no_browser=True,
                    legacy_confirm=lambda: self.fail('modern handoff must not confirm'), timeout=5)
                self.assertEqual('closed', result)
                self.assertTrue(app.shutdown_status()['closed'])
                self.assertFalse(runtime.exists())
            finally:
                server.shutdown(); thread.join(2); app.close()

    def test_endpoint_accepts_only_exact_loopback_capability(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'runtime.json'
            path.write_text(json.dumps({'url': 'http://127.0.0.1:32123/#token=' + TOKEN}))
            self.assertEqual(('http://127.0.0.1:32123', TOKEN), upgrade.endpoint(path))
            for uri in ['http://localhost:32123/', 'https://127.0.0.1:32123/',
                        'http://user@127.0.0.1:32123/', 'http://127.0.0.1:32123/other',
                        'http://127.0.0.1:32123/?query=1']:
                path.write_text(json.dumps({'url': uri + '#token=' + TOKEN}))
                with self.assertRaises(upgrade.HandoffError):
                    upgrade.endpoint(path)

    def test_shutdown_requires_endpoint_and_runtime_disappearance(self):
        for online, record in ((True, True), (True, False), (False, True), (False, False)):
            with tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / 'runtime.json'
                if record: path.write_text('{}')
                client = Mock()
                if not online: client.request.side_effect = URLError(ConnectionRefusedError(10061, 'refused'))
                ticks = iter([0, .1, 1])
                actual = upgrade.wait_shutdown(client, path, clock=lambda: next(ticks), sleep=lambda _: None, timeout=.5)
                self.assertEqual(not online and not record, actual)

    def test_timeout_is_not_shutdown_evidence_even_without_runtime(self):
        client = Mock(); client.request.side_effect = URLError(TimeoutError('timeout'))
        ticks = iter([0, .1, 1])
        self.assertFalse(upgrade.wait_shutdown(client, Path('missing-runtime-fixture'),
            clock=lambda: next(ticks), sleep=lambda _: None, timeout=.5))

    def test_only_stale_owned_acknowledgements_are_pruned(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            stale = state / ('upgrade-launch-' + 'a' * 32 + '.json'); stale.write_text('{}')
            fresh = state / ('upgrade-launch-' + 'b' * 32 + '.json'); fresh.write_text('{}')
            other = state / 'upgrade-launch-other.json'; other.write_text('{}')
            os.utime(stale, (0, 0)); os.utime(other, (0, 0))
            upgrade.prune_acknowledgements(state)
            self.assertFalse(stale.exists()); self.assertTrue(fresh.exists()); self.assertTrue(other.exists())

    def test_lock_deduplicates_without_stale_pid_handling(self):
        with tempfile.TemporaryDirectory() as raw:
            with upgrade.update_lock(raw) as first:
                with upgrade.update_lock(raw) as second:
                    self.assertTrue(first); self.assertFalse(second)
            with upgrade.update_lock(raw) as third:
                self.assertTrue(third)

    def test_launcher_uses_fixed_argv_preserving_spaces_without_shell(self):
        with tempfile.TemporaryDirectory(prefix='update 한글 & ') as raw:
            python = Path(raw) / 'python.exe'; python.write_bytes(b'fixture')
            popen = Mock()
            popen.return_value.wait.return_value = 0
            with patch.object(upgrade, 'powershell_path', return_value='C:/Windows/PowerShell.exe'):
                upgrade.launch(Path(raw), str(python), demo=True, popen=popen)
            args, kwargs = popen.call_args
            self.assertIn(str(python), args[0]); self.assertIn(str(Path(raw)), args[0])
            self.assertNotIn('shell', kwargs); self.assertIn('-Demo', args[0])
            popen.return_value.wait.assert_called_once_with(timeout=90)
            popen.return_value.kill.assert_not_called()

    def test_hung_replacement_launcher_is_stopped_and_reaped(self):
        import subprocess
        with tempfile.TemporaryDirectory() as raw:
            python = Path(raw) / 'python.exe'; python.write_bytes(b'fixture')
            process = Mock()
            process.wait.side_effect = [subprocess.TimeoutExpired(['powershell'], 90), 1]
            process.poll.return_value = None
            with patch.object(upgrade, 'powershell_path', return_value='C:/Windows/PowerShell.exe'):
                with self.assertRaises(upgrade.HandoffError):
                    upgrade.launch(Path(raw), str(python), popen=Mock(return_value=process))
            process.kill.assert_called_once_with()
            self.assertEqual([90, 2], [call.kwargs['timeout'] for call in process.wait.call_args_list])

    def test_replacement_launcher_error_is_not_reported_as_completed(self):
        with tempfile.TemporaryDirectory() as raw:
            python = Path(raw) / 'python.exe'; python.write_bytes(b'fixture')
            process = Mock()
            process.wait.return_value = 40
            with patch.object(upgrade, 'powershell_path', return_value='C:/Windows/PowerShell.exe'):
                with self.assertRaises(upgrade.HandoffError):
                    upgrade.launch(Path(raw), str(python), popen=Mock(return_value=process))
            process.kill.assert_not_called()

    def test_failed_launcher_termination_preserves_handle_and_reports_unconfirmed_cleanup(self):
        with tempfile.TemporaryDirectory() as raw:
            python = Path(raw) / 'python.exe'; python.write_bytes(b'fixture')
            process = Mock()
            process.wait.side_effect = subprocess.TimeoutExpired(['powershell'], 90)
            process.poll.return_value = None
            process.kill.side_effect = OSError('fixture termination denied')
            with patch.object(upgrade, 'powershell_path', return_value='C:/Windows/PowerShell.exe'):
                with self.assertRaises(upgrade.LauncherCleanupPending) as error:
                    upgrade.launch(Path(raw), str(python), popen=Mock(return_value=process))
            self.assertIs(error.exception.process, process)
            self.assertIn('종료를 확인하지 못했습니다', str(error.exception))
            self.assertNotIn('정리했습니다', str(error.exception))
            process.kill.assert_called_once_with()

    def test_coordinator_retains_deduplication_lock_until_exact_failed_launcher_exits(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            process = Mock()
            def confirm_lock_while_waiting():
                with upgrade.update_lock(state) as acquired:
                    self.assertFalse(acquired)
                return 1
            process.wait.side_effect = confirm_lock_while_waiting
            arguments = ['upgrade', '--state', raw, '--python', sys.executable,
                         '--request-id', RID, '--no-browser']
            with patch.object(sys, 'argv', arguments), \
                    patch('local_app.startup.verify_process', return_value='normal'), \
                    patch.object(upgrade, 'preflight'), \
                    patch.object(upgrade, 'endpoint', return_value=('http://127.0.0.1:12345', TOKEN)), \
                    patch.object(upgrade, 'transfer', return_value='closed'), \
                    patch.object(upgrade, 'launch', side_effect=upgrade.LauncherCleanupPending(process)) as launch:
                self.assertEqual(upgrade.main(), 39)
            process.wait.assert_called_once_with()
            launch.assert_called_once()
            self.assertEqual(json.loads((state / 'upgrade-last-result.json').read_text())['status'], 'failed')
            with upgrade.update_lock(state) as acquired:
                self.assertTrue(acquired)

    def test_failed_wait_handle_keeps_one_dormant_owner_without_retry_loop(self):
        process = Mock()
        process.wait.side_effect = OSError('fixture invalid wait handle')
        signal = Mock()
        with patch.object(upgrade.threading, 'Event', return_value=signal):
            upgrade.LauncherCleanupPending(process).wait_for_exit()
        process.wait.assert_called_once_with()
        signal.wait.assert_called_once_with()

    def test_real_http_client_authenticates_and_bounds_json(self):
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                calls.append(self.headers.get('Authorization'))
                body = json.dumps(health()).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            client = upgrade.Client('http://127.0.0.1:' + str(server.server_port), TOKEN)
            self.assertEqual(200, client.request('/api/bootstrap')[0])
            self.assertEqual(['Bearer ' + TOKEN], calls)
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_http_redirect_never_forwards_bearer_to_destination(self):
        destinations = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if self.path == '/destination':
                    destinations.append(self.headers.get('Authorization'))
                self.send_response(302)
                self.send_header('Location', '/destination'); self.end_headers(); self.wfile.write(b'{}')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            client = upgrade.Client('http://127.0.0.1:' + str(server.server_port), TOKEN)
            self.assertEqual(302, client.request('/api/bootstrap')[0])
            self.assertEqual([], destinations)
        finally:
            server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__':
    unittest.main()
