"""A detached coordinator failure reaches the running updater before dialog dismissal."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_app import app_updates, update_progress, upgrade_launcher
from local_app.server import WORKSPACE_VERSION
from tests.test_workspace_app_updates import Transport


class UpdateFailureIntegrationTests(unittest.TestCase):
    def test_real_terminal_report_stops_progress_while_failure_dialog_is_still_open(self):
        for outcome in ('prepare-error', 'failed'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as raw, ExitStack() as stack:
                state = Path(raw)
                request_id = 'a' * 32
                dialog_open, dismiss_dialog = threading.Event(), threading.Event()
                coordinator_results, coordinator_errors, coordinators = [], [], []
                secret = 'Bearer secret; C:/private/draft.txt'
                card = Mock(diagnostic=None)
                wrapper = Mock()
                wrapper.poll.return_value = 0  # The accepted wrapper already exited.

                def show_dialog(function, **kwargs):
                    self.assertEqual('Show-WorkspaceUpgradeFailure', function)
                    self.assertEqual('handoff_failed' if outcome == 'failed' else 'handoff_prepare_failed',
                                     kwargs['failure_reason'])
                    dialog_open.set()
                    self.assertTrue(dismiss_dialog.wait(5), 'Test did not dismiss its simulated dialog')
                    return True

                def coordinator():
                    try:
                        coordinator_results.append(upgrade_launcher.main())
                    except BaseException as error:
                        coordinator_errors.append(error)

                def installer(version, *_args, **_kwargs):
                    self.assertEqual(WORKSPACE_VERSION, version)
                    thread = threading.Thread(target=coordinator, daemon=True)
                    coordinators.append(thread)
                    thread.start()
                    return {'status': 'launching', '_process': wrapper, '_requestId': request_id}

                stack.enter_context(patch.object(sys, 'argv', ['upgrade_launcher', '--state', str(state),
                    '--python', sys.executable, '--request-id', request_id,
                    '--target-version', WORKSPACE_VERSION, '--execution-mode', 'normal']))
                stack.enter_context(patch('local_app.startup.verify_process', return_value='normal'))
                stack.enter_context(patch.object(update_progress, 'UpdateProgress', return_value=card))
                stack.enter_context(patch.object(upgrade_launcher, 'preflight'))
                stack.enter_context(patch.object(upgrade_launcher, 'endpoint',
                    return_value=('http://127.0.0.1:12345', 'b' * 43)))
                transfer = stack.enter_context(patch.object(upgrade_launcher, 'transfer',
                    side_effect=upgrade_launcher.HandoffError(secret, reason='handoff_prepare_failed')
                    if outcome == 'prepare-error' else None, return_value='failed'))
                helper = stack.enter_context(patch.object(upgrade_launcher, 'run_helper', side_effect=show_dialog))
                stack.enter_context(patch.object(app_updates, 'LAUNCH_POLL_INTERVAL', .01))
                manager = app_updates.UpdateManager(state, '0.0.0', installer=installer,
                    transport=Transport(WORKSPACE_VERSION), handoff_status=lambda: {'upgrade': None})
                try:
                    manager.check(manual=True)
                    manager._thread.join(2)
                    self.assertTrue(manager.snapshot()['canInstall'])
                    started = time.monotonic()
                    manager.install(WORKSPACE_VERSION)
                    self.assertTrue(dialog_open.wait(2), coordinator_errors)
                    manager._thread.join(1)
                    self.assertFalse(manager._thread.is_alive(), 'Updater still waits for the detached coordinator')
                    self.assertLess(time.monotonic() - started, 1.5)
                    self.assertTrue(coordinators[0].is_alive(), 'Failure dialog was already dismissed')
                    result = manager.snapshot()
                    self.assertEqual('error', result['status'])
                    self.assertIsNone(result['progress'])
                    self.assertTrue(result['canInstall'])
                    self.assertNotIn(secret, json.dumps(result))
                    saved = json.loads((state / 'upgrade-last-result.json').read_text())
                    self.assertEqual(request_id, saved['requestId'])
                    self.assertEqual(WORKSPACE_VERSION, saved['targetVersion'])
                    self.assertEqual('failed', saved['status'])
                    self.assertNotIn(secret, json.dumps(saved))
                    # Retry UI is safe before dialog dismissal: actual OS lock
                    # must already be released, not just a mocked lock callback.
                    with upgrade_launcher.update_lock(state) as held:
                        self.assertTrue(held)
                    transfer.assert_called_once()
                    helper.assert_called_once()
                    wrapper.terminate.assert_not_called()
                finally:
                    dismiss_dialog.set()
                    for thread in coordinators:
                        thread.join(2)
                    manager.close()
                self.assertEqual([], coordinator_errors)
                self.assertEqual([39 if outcome == 'prepare-error' else 0], coordinator_results)


if __name__ == '__main__':
    unittest.main()
