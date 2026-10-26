"""Handoff only after authenticated idle/capture/cleanup evidence."""
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
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
        if path == '/api/quit' or path == '/api/upgrade' and data and data['action'] == 'commit':
            if isinstance(self.commit, Exception):
                raise self.commit
            return self.commit
        if path == '/api/upgrade' and data and data['action'] == 'cancel':
            return 200, snapshot('cancelled')
        return 200, snapshot(self.stages.pop(0) if len(self.stages) > 1 else self.stages[0])


class LegacyRecoveryClient(FakeClient):
    """Published old-app rejection/ack contract, with all mutations observed."""
    def __init__(self):
        super().__init__()
        self.restore = {'requestId': 'd' * 32, 'savedTargetVersion': '0.23.31',
                        'snapshot': {'sessionId': SID, 'drafts': [
                            {'id': SID, 'text': '이전 초안\n아직 보내지 않음', 'attachments': ['D:/보고서.xlsx']}]}}
        self.before_ack = lambda: None
        self.ack_result = None
        self.prepare_count = 0
        self.keep_blocked = False

    def request(self, path, data=None, **kwargs):
        if path == '/api/upgrade' and data is None:
            self.calls.append((path, data))
            return 200, {'upgrade': None, 'upgradeRestore': deepcopy(self.restore), 'warning': None}
        if path == '/api/upgrade' and data and data.get('action') == 'prepare':
            self.prepare_count += 1
            if self.restore is not None or self.keep_blocked:
                self.calls.append((path, data))
                return 400, {'error': '이전 창의 작성 내용을 먼저 복원해 주세요.'}
        if path == '/api/upgrade' and data and data.get('action') == 'restored':
            self.calls.append((path, data))
            self.before_ack()
            if isinstance(self.ack_result, Exception):
                raise self.ack_result
            if self.ack_result is not None:
                return self.ack_result
            assert data['requestId'] == self.restore['requestId']
            self.restore = None
            return 200, {'ok': True, 'upgradeRestore': None}
        return super().request(path, data, **kwargs)


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

    def test_transient_status_reads_recover_without_replaying_prepare_or_commit(self):
        client = FakeClient(stages=['waiting', 'captured'])
        original, failures = client.request, {'/api/bootstrap': 1, '/api/upgrade?requestId=' + RID: 2}
        attempts = []
        def request(path, data=None, **kwargs):
            attempts.append((path, data))
            if data is None and failures.get(path, 0):
                failures[path] -= 1
                raise URLError(TimeoutError('temporary local overload'))
            return original(path, data, **kwargs)
        client.request = request
        self.assertEqual('closed', self.transfer(client))
        self.assertEqual(1, sum(bool(data and data.get('action') == 'prepare') for _, data in attempts))
        self.assertEqual(1, sum(bool(data and data.get('action') == 'commit') for _, data in attempts))
        self.assertEqual(3, sum(path.startswith('/api/upgrade?') for path, _ in attempts))

    def test_permanent_status_transport_failure_is_bounded_and_does_not_mutate_snapshot(self):
        client = FakeClient(stages=['waiting'])
        original, polls = client.request, []
        def request(path, data=None, **kwargs):
            if path.startswith('/api/upgrade?'):
                polls.append(path)
                raise URLError(ConnectionResetError('offline'))
            return original(path, data, **kwargs)
        client.request = request
        with self.assertRaises(URLError):
            self.transfer(client)
        self.assertEqual(3, len(polls))
        # No cancel, quit or commit is inferred from a failed read. The old
        # app retains the pending handoff/snapshot and the user can cancel it.
        self.assertEqual(['prepare'], [data['action'] for _, data in client.calls if data])

    def test_authentication_malformed_response_and_prepare_failure_are_not_retried(self):
        for failure in (HTTPError('http://127.0.0.1', 401, 'unauthorized', {}, None),
                        ValueError('malformed response')):
            reader = Mock()
            reader.request.side_effect = failure
            with self.assertRaises(type(failure)):
                upgrade.read_status(reader, '/api/bootstrap', sleep=Mock())
            reader.request.assert_called_once_with('/api/bootstrap')
        client = FakeClient()
        original, prepares = client.request, []
        def request(path, data=None, **kwargs):
            if data and data.get('action') == 'prepare':
                prepares.append(data)
                raise URLError(TimeoutError('prepare acknowledgement unknown'))
            return original(path, data, **kwargs)
        client.request = request
        with self.assertRaises(URLError):
            self.transfer(client)
        self.assertEqual(1, len(prepares))

    def test_progress_appears_before_commit_and_waits_through_shutdown(self):
        client = FakeClient(stages=['waiting', 'capture', 'captured'])
        phases = []
        original = client.request
        def request(path, data=None, **kwargs):
            if path == '/api/upgrade' and data and data.get('action') == 'commit':
                self.assertEqual(['preparing'], phases)
            return original(path, data, **kwargs)
        client.request = request
        self.assertEqual('closed', self.transfer(client, on_phase=phases.append))
        self.assertEqual(['preparing', 'restarting'], phases)

    def test_progress_failure_never_closes_the_old_app(self):
        client = FakeClient()
        with self.assertRaises(OSError):
            self.transfer(client, on_phase=Mock(side_effect=OSError('native card unavailable')))
        self.assertFalse(any(data and data.get('action') == 'commit' for _, data in client.calls))
        self.assertEqual('cancel', client.calls[-1][1]['action'])

    def test_native_card_failure_does_not_block_handoff_or_mask_cleanup_failure(self):
        from local_app import update_progress as progress_module
        for commit, expected in ((None, 'closed'), ((503, {'closed': False}), None)):
            with self.subTest(commit=commit):
                card = progress_module.UpdateProgress(factory=Mock(side_effect=OSError('GDI unavailable')))
                card.enabled = True
                self.addCleanup(card.close)
                client = FakeClient(commit=commit)
                if expected:
                    self.assertEqual(expected, self.transfer(client, on_phase=card.phase))
                else:
                    with self.assertRaises(upgrade.HandoffError) as error:
                        self.transfer(client, on_phase=card.phase)
                    self.assertEqual('cleanup_unconfirmed', error.exception.reason)
                self.assertEqual('progress_unavailable', card.diagnostic)
                self.assertEqual(1, sum(bool(data and data.get('action') == 'commit') for _, data in client.calls))

    def test_no_progress_popup_while_busy_or_cancelled(self):
        for stages in (['waiting'], ['cancelled'], ['capture', 'cancelled']):
            phases = Mock()
            self.transfer(FakeClient(stages=stages), on_phase=phases)
            phases.assert_not_called()

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
            with self.assertRaises(upgrade.HandoffError) as failure:
                self.transfer(FakeClient(commit=reply))
            self.assertEqual('cleanup_unconfirmed', failure.exception.reason)

    def test_rejected_prepare_reports_safe_reason_without_server_text(self):
        client = FakeClient()
        original = client.request
        def request(path, data=None, **kwargs):
            if path == '/api/upgrade' and data.get('action') == 'prepare':
                return 400, {'error': 'private prompt and bearer secret'}
            return original(path, data, **kwargs)
        client.request = request
        with self.assertRaises(upgrade.HandoffError) as failure:
            self.transfer(client)
        self.assertEqual('handoff_prepare_failed', failure.exception.reason)
        self.assertNotIn('private', str(failure.exception))
        self.assertFalse(any(data and data.get('action') == 'commit' for _, data in client.calls))

    def test_old_032_prepare_blockers_are_distinguished_without_recovery_mutations(self):
        cases = (
            ('이전 창의 작성 내용을 먼저 복원해 주세요.', 'draft_recovery_pending'),
            ('이전 창의 작성 내용을 확인하지 못했습니다. 저장 기록은 보존했습니다.', 'draft_record_unreadable'),
            ('다른 버전 전환을 준비하고 있습니다. 기존 요청을 먼저 마쳐 주세요.', 'handoff_already_pending'),
            ('다른 실행 권한으로 변경하려면 앱을 완전 종료한 뒤 Windows에서 다시 실행해 주세요.', 'execution_mode_mismatch'),
        )
        for message, reason in cases:
            with self.subTest(reason=reason):
                client = FakeClient()
                original = client.request
                mutations = []
                def request(path, data=None, **kwargs):
                    if data:
                        mutations.append(data['action'])
                        return 400, {'error': message}
                    return original(path, data, **kwargs)
                client.request = request
                with self.assertRaises(upgrade.HandoffError) as failure:
                    self.transfer(client)
                self.assertEqual(reason, failure.exception.reason)
                self.assertEqual(['prepare'], mutations)
                self.assertNotIn(message, str(failure.exception))

    def test_prepare_reason_requires_exact_known_text_and_rejection_status(self):
        message = '이전 창의 작성 내용을 먼저 복원해 주세요.'
        for code, reply in (
            (400, {'error': message + ' secret'}), (400, {'error': 'prefix ' + message}),
            (400, {'error': [message]}), (400, {'reason': 'draft_recovery_pending'}),
            (403, {'error': message}), (500, {'error': message}), (400, None),
        ):
            with self.subTest(code=code, reply=reply):
                self.assertEqual('handoff_prepare_failed', upgrade.prepare_failure_reason(code, reply))

    def test_legacy_recovery_is_archived_before_ack_then_prepared_once(self):
        from local_app import upgrade_handoff
        client = LegacyRecoveryClient()
        original = deepcopy(client.restore)
        preserved = []
        def preserve(state, restore):
            self.assertEqual(Path('.'), state)
            preserved.append(deepcopy(restore))
        client.before_ack = lambda: self.assertEqual([original], preserved)
        with patch.object(upgrade_handoff, 'preserve_recovery', side_effect=preserve):
            self.assertEqual('closed', self.transfer(client))
        self.assertEqual(2, client.prepare_count)
        self.assertEqual(['prepare', 'restored', 'prepare', 'commit'],
                         [data['action'] for _, data in client.calls if data])
        self.assertEqual(original, preserved[0])

    def test_legacy_recovery_archive_failure_never_acknowledges_or_closes(self):
        from local_app import upgrade_handoff
        client = LegacyRecoveryClient()
        original = deepcopy(client.restore)
        with patch.object(upgrade_handoff, 'preserve_recovery', side_effect=OSError('private disk path')):
            with self.assertRaises(upgrade.HandoffError) as failure:
                self.transfer(client)
        self.assertEqual('draft_archive_failed', failure.exception.reason)
        self.assertNotIn('private', str(failure.exception))
        self.assertEqual(original, client.restore)
        self.assertEqual(['prepare'], [data['action'] for _, data in client.calls if data])

    def test_legacy_recovery_uncertain_or_incomplete_ack_never_retries_mutation(self):
        from local_app import upgrade_handoff
        for response in (URLError('ack lost'), (200, {'ok': True}),
                         (200, {'ok': False, 'upgradeRestore': None}),
                         (200, {'ok': True, 'upgradeRestore': {'requestId': 'e' * 32}}),
                         (400, {'error': 'record changed'})):
            with self.subTest(response=response):
                client = LegacyRecoveryClient()
                client.ack_result = response
                with patch.object(upgrade_handoff, 'preserve_recovery') as preserved:
                    with self.assertRaises((upgrade.HandoffError, URLError)):
                        self.transfer(client)
                preserved.assert_called_once()
                self.assertEqual(['prepare', 'restored'], [data['action'] for _, data in client.calls if data])

    def test_legacy_recovery_migration_does_not_loop_on_another_rejection(self):
        from local_app import upgrade_handoff
        client = LegacyRecoveryClient()
        client.keep_blocked = True
        with patch.object(upgrade_handoff, 'preserve_recovery') as preserved:
            with self.assertRaises(upgrade.HandoffError):
                self.transfer(client)
        preserved.assert_called_once()
        self.assertEqual(['prepare', 'restored', 'prepare'], [data['action'] for _, data in client.calls if data])

    def test_execution_mode_mismatch_is_reported_before_prepare(self):
        client = FakeClient()
        client.health['executionMode'] = 'normal'
        with self.assertRaises(upgrade.HandoffError) as failure:
            self.transfer(client, execution_mode='administrator')
        self.assertEqual('execution_mode_mismatch', failure.exception.reason)
        self.assertEqual(['/api/bootstrap'], [path for path, _ in client.calls])

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
        phases = []
        self.assertEqual('closed', self.transfer(client, on_phase=phases.append))
        self.assertEqual(['preparing', 'waiting', 'preparing', 'restarting'], phases)

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
    def test_failure_helper_accepts_only_fixed_reason_arguments(self):
        runner = Mock(return_value=Mock(returncode=0))
        self.assertTrue(upgrade.run_helper('Show-WorkspaceUpgradeFailure',
                                         failure_reason='cleanup_unconfirmed', runner=runner))
        source = runner.call_args.args[0][-1]
        self.assertIn("Show-WorkspaceUpgradeFailure -Reason 'cleanup_unconfirmed'", source)
        for reason in ("'; Write-Host secret; '", 'http://private?token=secret', [], {'reason': 'unknown'}):
            runner.reset_mock()
            with self.assertRaises(upgrade.HandoffError):
                upgrade.run_helper('Show-WorkspaceUpgradeFailure', failure_reason=reason, runner=runner)
            runner.assert_not_called()
        with self.assertRaises(upgrade.HandoffError):
            upgrade.run_helper('Confirm-WorkspaceLegacyUpgrade', failure_reason='unknown', runner=runner)

    def test_failure_popup_has_distinct_new_app_messages_and_exact_reason_allowlist(self):
        source = (upgrade.ROOT / 'deploy/CompanyWorkspace.Startup.ps1').read_text(encoding='utf-8')
        block = source.split('function Show-WorkspaceUpgradeFailure {', 1)[1].split('\nfunction ', 1)[0]
        import re
        labels = dict(re.findall(r"^\s+(\w+) = '([^']*)'", block, re.MULTILINE))
        self.assertEqual(upgrade.FAILURE_REASONS, set(labels))
        for reason in ('new_app_launch_failed', 'new_app_not_ready', 'launcher_cleanup_pending'):
            self.assertIn('기존 앱은 종료했지만', labels[reason])
            self.assertNotIn('강제로 종료하지 않았습니다', labels[reason])
        self.assertIn('기존 앱의 종료 처리를', labels['cleanup_unconfirmed'])
        self.assertIn('초안을 지울 필요는 없습니다', labels['draft_recovery_pending'])

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
            request_index = args[0].index('-ExecutionRequestId')
            self.assertRegex(args[0][request_index + 1], r'^[a-f0-9]{32}$')
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

    def test_launcher_already_explained_failure_is_not_success(self):
        with tempfile.TemporaryDirectory() as raw:
            python = Path(raw) / 'python.exe'; python.write_bytes(b'fixture')
            process = Mock()
            process.wait.return_value = 20
            with patch.object(upgrade, 'powershell_path', return_value='C:/Windows/PowerShell.exe'):
                with self.assertRaises(upgrade.LauncherExplainedError):
                    upgrade.launch(Path(raw), str(python), popen=Mock(return_value=process))

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


class ReplacementReadinessTests(unittest.TestCase):
    def wait(self, snapshots, *, token='c' * 43, no_browser=False):
        values = list(snapshots)
        clock = [0.]
        client = Mock()
        def response(*args, **kwargs):
            value = values.pop(0) if len(values) > 1 else values[0]
            if isinstance(value, Exception):
                raise value
            return 200, value
        client.request.side_effect = response
        def sleep(delay):
            clock[0] += delay
        with patch.object(upgrade, 'endpoint', return_value=('http://127.0.0.1:32123', token)):
            return upgrade.wait_replacement('unused', previous_token=TOKEN, demo=False,
                target_version='0.23.26', execution_mode='normal', no_browser=no_browser,
                client_factory=Mock(return_value=client), clock=lambda: clock[0], sleep=sleep, timeout=2)

    def ready(self, state='ready', **changes):
        return {**health(version='0.23.26'), 'executionMode': 'normal',
                'window': {'startupState': state, 'launchId': 'd' * 32}, **changes}

    def test_delayed_frontend_readiness_keeps_waiting(self):
        self.assertTrue(self.wait([URLError('starting'), self.ready('pending'), self.ready()]))

    def test_runtime_or_backend_ready_is_not_interactive_success(self):
        for value in (self.ready('pending'), self.ready(window={}), self.ready(window={'startupState': 'ready'})):
            with self.assertRaises(upgrade.HandoffError):
                self.wait([value])

    def test_failed_startup_and_wrong_app_never_report_success(self):
        for change in ({'application': 'other'}, {'workspaceVersion': '0.23.25'}, {'demo': True},
                       {'executionMode': 'administrator'}, {'closing': True}):
            with self.assertRaises(upgrade.HandoffError):
                self.wait([self.ready(**change)])
        with self.assertRaisesRegex(upgrade.HandoffError, '준비하지 못했습니다'):
            self.wait([self.ready('failed')])

    def test_old_runtime_token_never_counts_as_new_app(self):
        with self.assertRaises(upgrade.HandoffError):
            self.wait([self.ready()], token=TOKEN)

    def test_no_browser_waits_for_authenticated_backend_only(self):
        self.assertTrue(self.wait([self.ready('pending')], no_browser=True))


class CoordinatorProgressTests(unittest.TestCase):
    def run_main(self, state, *, outcome='closed', launch_error=None, readiness_error=None,
                 preflight_error=None, transfer_error=None, no_browser=False, card=None,
                 target_version=None, notice_callback=None):
        progress = card or Mock()
        arguments = ['upgrade', '--state', str(state), '--python', sys.executable, '--request-id', RID]
        if no_browser:
            arguments.append('--no-browser')
        if target_version is not None:
            arguments.extend(['--target-version', target_version])
        with patch.object(sys, 'argv', arguments), \
                patch('local_app.startup.verify_process', return_value='normal'), \
                patch('local_app.update_progress.UpdateProgress', return_value=progress) as factory, \
                patch.object(upgrade, 'preflight', side_effect=preflight_error), \
                patch.object(upgrade, 'endpoint', return_value=('http://127.0.0.1:12345', TOKEN)), \
                patch.object(upgrade, 'transfer', return_value=outcome, side_effect=transfer_error), \
                patch.object(upgrade, 'launch', side_effect=launch_error) as launch, \
                patch.object(upgrade, 'wait_replacement', side_effect=readiness_error) as ready, \
                patch.object(upgrade, 'run_helper', side_effect=notice_callback) as helper:
            result = upgrade.main()
        return result, progress, factory, launch, ready, helper

    def test_failure_is_published_for_exact_attempt_after_lock_release_before_dialog(self):
        from local_app.server import WORKSPACE_VERSION
        for options in ({'outcome': 'failed'}, {'outcome': 'expired'},
                        {'transfer_error': upgrade.HandoffError('fixture')},
                        {'preflight_error': OSError('fixture')}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as raw:
                state = Path(raw)
                notices = []
                def notice(*args, **kwargs):
                    record = json.loads((state / 'upgrade-last-result.json').read_text())
                    self.assertEqual(RID, record['requestId'])
                    self.assertEqual(WORKSPACE_VERSION, record['targetVersion'])
                    self.assertIn(record['status'], {'failed', 'expired'})
                    with upgrade.update_lock(state) as available:
                        self.assertTrue(available)
                    notices.append(record)
                    return True
                result, _, _, launch, ready, _ = self.run_main(state, target_version=WORKSPACE_VERSION,
                                                              notice_callback=notice, **options)
                self.assertIn(result, (0, 39))
                self.assertEqual(1, len(notices))
                launch.assert_not_called(); ready.assert_not_called()

    def test_cancelled_result_keeps_attempt_identity_without_failure_dialog(self):
        from local_app.server import WORKSPACE_VERSION
        with tempfile.TemporaryDirectory() as raw:
            result, _, _, launch, ready, helper = self.run_main(Path(raw), outcome='cancelled',
                                                               target_version=WORKSPACE_VERSION)
            self.assertEqual(0, result)
            record = json.loads((Path(raw) / 'upgrade-last-result.json').read_text())
            self.assertEqual({'requestId': RID, 'targetVersion': WORKSPACE_VERSION, 'status': 'cancelled'}, record)
            helper.assert_not_called(); launch.assert_not_called(); ready.assert_not_called()

    def test_launcher_propagates_target_without_another_version_literal(self):
        source = (upgrade.ROOT / 'deploy/Start-CompanyWorkspace.ps1').read_text(encoding='utf-8')
        self.assertIn("'--target-version', $targetVersion.ToString()", source)

    def test_success_closes_card_only_after_readiness(self):
        with tempfile.TemporaryDirectory() as raw:
            result, card, _, launch, ready, helper = self.run_main(Path(raw))
            self.assertEqual(0, result)
            card.phase.assert_called_once_with('opening')
            launch.assert_called_once(); ready.assert_called_once(); card.close.assert_called_once()
            helper.assert_not_called()
            self.assertEqual('restarted', json.loads((Path(raw) / 'upgrade-last-result.json').read_text())['status'])

    def test_degraded_progress_records_only_safe_code_and_still_checks_new_app(self):
        from local_app.update_progress import UpdateProgress
        secret = 'C:\\private\\draft.txt token=secret'
        card = UpdateProgress(factory=Mock(side_effect=OSError(secret)))
        card.enabled = True
        with tempfile.TemporaryDirectory() as raw:
            result, _, _, launch, ready, helper = self.run_main(Path(raw), card=card)
            self.assertEqual(0, result)
            launch.assert_called_once(); ready.assert_called_once(); helper.assert_not_called()
            saved = json.loads((Path(raw) / 'upgrade-last-result.json').read_text())
            self.assertEqual({'requestId': RID, 'status': 'restarted',
                              'progressWarning': 'progress_unavailable'}, saved)
            self.assertNotIn(secret, json.dumps(saved))

    def test_failed_launch_or_screen_readiness_closes_card_and_explains(self):
        for kind in ('launch_error', 'readiness_error'):
            with tempfile.TemporaryDirectory() as raw:
                result, card, _, _, _, helper = self.run_main(Path(raw), **{kind: upgrade.HandoffError('fixture')})
                self.assertEqual(39, result)
                self.assertGreaterEqual(card.close.call_count, 1)
                reason = 'new_app_launch_failed' if kind == 'launch_error' else 'new_app_not_ready'
                helper.assert_called_once_with('Show-WorkspaceUpgradeFailure', failure_reason=reason)
                saved = json.loads((Path(raw) / 'upgrade-last-result.json').read_text())
                self.assertEqual({'requestId': RID, 'status': 'failed', 'reason': reason,
                    'phase': 'opening' if kind == 'launch_error' else 'ready_check'}, saved)

    def test_preflight_and_cleanup_failure_records_do_not_store_exception_or_server_text(self):
        secret = 'Bearer secret; C:\\private\\prompt.txt https://private/?token=secret'
        for kind, error, reason, phase in (
                ('preflight_error', OSError(secret), 'preflight_failed', 'preflight'),
                ('transfer_error', upgrade.HandoffError(secret, reason='cleanup_unconfirmed'),
                 'cleanup_unconfirmed', 'preparing'),
                ('transfer_error', upgrade.HandoffError(secret, reason='draft_recovery_pending'),
                 'draft_recovery_pending', 'preparing'),
                ('transfer_error', upgrade.HandoffError(secret, reason='draft_record_unreadable'),
                 'draft_record_unreadable', 'preparing'),
                ('transfer_error', upgrade.HandoffError(secret, reason=secret), 'handoff_failed', 'preparing')):
            with self.subTest(kind=kind, reason=reason), tempfile.TemporaryDirectory() as raw:
                result, _, _, launch, ready, helper = self.run_main(Path(raw), **{kind: error})
                self.assertEqual(39, result)
                launch.assert_not_called(); ready.assert_not_called()
                saved = json.loads((Path(raw) / 'upgrade-last-result.json').read_text())
                self.assertEqual({'requestId': RID, 'status': 'failed', 'phase': phase, 'reason': reason}, saved)
                helper.assert_called_once_with('Show-WorkspaceUpgradeFailure', failure_reason=reason)
                self.assertNotIn(secret, json.dumps(saved))

    def test_failed_or_expired_transfer_records_safe_reason(self):
        for outcome in ('failed', 'expired'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as raw:
                result, _, _, launch, ready, helper = self.run_main(Path(raw), outcome=outcome)
                self.assertEqual(0, result)
                launch.assert_not_called(); ready.assert_not_called()
                reason = 'handoff_' + outcome
                saved = json.loads((Path(raw) / 'upgrade-last-result.json').read_text())
                self.assertEqual({'requestId': RID, 'status': outcome, 'phase': 'preparing', 'reason': reason}, saved)
                helper.assert_called_once_with('Show-WorkspaceUpgradeFailure', failure_reason=reason)

    def test_cancelled_transfer_never_launches_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as raw:
            result, card, _, launch, ready, helper = self.run_main(Path(raw), outcome='cancelled')
            self.assertEqual(0, result)
            launch.assert_not_called(); ready.assert_not_called(); helper.assert_not_called()
            card.close.assert_called_once()

    def test_no_browser_disables_native_card_and_failure_dialog(self):
        with tempfile.TemporaryDirectory() as raw:
            result, _, factory, _, _, helper = self.run_main(Path(raw), no_browser=True,
                launch_error=upgrade.HandoffError('fixture'))
            self.assertEqual(39, result)
            self.assertFalse(factory.call_args.kwargs['enabled'])
            helper.assert_not_called()

    def test_explained_launcher_failure_never_opens_second_error_dialog(self):
        with tempfile.TemporaryDirectory() as raw:
            result, card, _, _, ready, helper = self.run_main(Path(raw),
                launch_error=upgrade.LauncherExplainedError('fixture'))
            self.assertEqual(39, result)
            card.close.assert_called_once()
            ready.assert_not_called(); helper.assert_not_called()


if __name__ == '__main__':
    unittest.main()
