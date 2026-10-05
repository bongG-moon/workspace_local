"""Private desktop protocol and window identity; no real UI or Claude."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_app.native_window import DesktopHost, DesktopError
from local_app.attention import DEFAULT_REQUEST_SUMMARY, AttentionNotifier, WindowBinding, WindowsAttention
from local_app.ui_health import UiHealthLog

FAKE = r'''
import json, os, sys
config = json.loads(sys.stdin.readline())
mode = sys.argv[1]
if mode == 'missing':
 print(json.dumps({'type':'error','code':46}), flush=True); sys.exit(46)
print(json.dumps({'type':'ready','pid':os.getpid() + (1 if mode=='wrong' else 0),'hwnd':123,'runtime':'fixture',
                  'notificationCards':mode!='unsupported'}), flush=True)
for line in sys.stdin:
 request=json.loads(line)
 with open(sys.argv[2], 'a', encoding='utf-8') as record:
  record.write(json.dumps(request)+'\n')
 if request['command']=='close': break
 if request['command']=='fixture_event':
  print(json.dumps({'type':request['eventType'],'notificationId':request['notificationId']}), flush=True)
 reply={'type':'ack','id':request['id'],'ok':True}
 if request['command']=='confirm_shutdown':
  if mode=='exit_on_confirm': sys.exit(0)
  if mode!='confirm_missing':
   reply['confirmationShown']='true' if mode=='confirm_string' else mode!='confirm_reject'
 if request['command']=='notify':
  if mode=='exit_on_notify': sys.exit(0)
  if mode in ('open_first', 'dismiss_first'):
   print(json.dumps({'type':'notification_opened' if mode=='open_first' else 'notification_dismissed',
                     'notificationId':request['notificationId']}), flush=True)
  if mode=='reject': reply['ok']=False
  reply['notificationAccepted']=mode not in ('suppressed','busy','reject','unavailable')
  reply['notificationReason']=mode
 print(json.dumps(reply), flush=True)
'''


@unittest.skipUnless(os.name == 'nt', 'Windows desktop process controller')
class NativeWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'Workspace.Desktop.exe'
        self.binary.write_bytes(b'fixture')
        self.mode, self.processes, self.launches = 'ok', [], []
        self.requests = self.root / 'requests.jsonl'
        self.url = 'http://127.0.0.1:54321/#token=' + 'x'*43
        def popen(command, **kwargs):
            self.launches.append((command, kwargs))
            process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, self.mode, str(self.requests)], **kwargs)
            self.processes.append(process)
            return process
        self.notifier = Mock()
        self.notifier.bind_owned.return_value = True
        self.host = DesktopHost(self.notifier, self.url, self.root, popen=popen)
        self.addCleanup(self.host.close)
        patcher = patch('local_app.native_window.desktop_executable', return_value=self.binary)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_one_owned_process_private_url_repeated_activation_and_clean_exit(self):
        self.assertEqual('opened', self.host.open()['action'])
        for _ in range(4):
            self.assertEqual('activated', self.host.open()['action'])
        self.assertEqual(1, len(self.launches))
        self.assertEqual([str(self.binary)], self.launches[0][0])
        self.assertNotIn(self.url, repr(self.launches))
        self.notifier.bind_owned.assert_called_once_with(123, self.processes[0].pid)
        self.host.close()
        self.assertEqual(0, self.processes[0].poll())
        with self.assertRaises(DesktopError): self.host.open()

    def test_diagnostics_correlate_existing_host_without_breaking_pipe(self):
        events = []
        self.host.on_event = events.append
        self.host.open()
        self.host.open()
        self.wait_for(lambda: any(row['type'] == 'activated' for row in events))
        ready = next(row for row in events if row['type'] == 'ready')
        activated = next(row for row in events if row['type'] == 'activated')
        self.assertEqual(ready['pid'], activated['pid'])
        self.assertEqual(123, activated['hwnd'])
        self.assertNotIn(self.url, json.dumps(events))
        self.host.on_event = failed_callback = Mock(side_effect=OSError('diagnostics unavailable'))
        self.assertEqual('activated', self.host.open()['action'])
        self.wait_for(lambda: failed_callback.called)
        self.assertEqual(1, len(self.launches))

    def test_blocked_diagnostic_storage_does_not_delay_ack_notifications_or_close(self):
        entered, release = threading.Event(), threading.Event()
        replace = os.replace
        def blocked_replace(*args):
            entered.set()
            release.wait(10)
            return replace(*args)
        self.host.on_event = UiHealthLog(self.root, '0.21.1').native
        try:
            with patch('local_app.ui_health.os.replace', side_effect=blocked_replace):
                self.assertEqual('opened', self.host.open()['action'])
                self.assertTrue(entered.wait(2))
                self.assertEqual('activated', self.host.open()['action'])
                clicked = threading.Event()
                self.assertTrue(self.notify(on_click=clicked.set))
                self.emit('notification_opened')
                self.assertTrue(clicked.wait(2))
                before_close = time.monotonic()
                self.host.close()
                self.assertLess(time.monotonic() - before_close, 2)
                self.assertFalse(release.is_set())
                self.assertIsNotNone(self.processes[0].poll())
                self.assertFalse(self.host.notification_available)
        finally:
            release.set()
            if self.host._diagnostic_worker is not None:
                self.host._diagnostic_worker.join(2)
        self.assertFalse(self.host._diagnostic_worker.is_alive())

    def test_slow_diagnostics_use_one_bounded_worker_and_drop_pending_on_close(self):
        entered, release = threading.Event(), threading.Event()
        delivered = []
        def blocked_callback(row):
            delivered.append(row)
            entered.set()
            release.wait(10)
        self.host.on_event = blocked_callback
        try:
            self.host.open()
            self.assertTrue(entered.wait(2))
            worker = self.host._diagnostic_worker
            for _ in range(1000):
                self.host._diagnostic(self.host.process, {'type': 'loaded'})
            self.assertIs(worker, self.host._diagnostic_worker)
            self.assertEqual(64, len(self.host._diagnostic_rows))
            self.assertEqual(1, len(delivered))
            self.assertEqual('activated', self.host.open()['action'])
            self.host.close()
            self.assertEqual(0, len(self.host._diagnostic_rows))
        finally:
            release.set()
            if self.host._diagnostic_worker is not None:
                self.host._diagnostic_worker.join(2)
        self.assertFalse(self.host._diagnostic_worker.is_alive())
        self.assertEqual(1, len(delivered))

    def test_missing_runtime_reports_ws46_and_does_not_open_a_browser(self):
        self.mode = 'missing'
        with self.assertRaises(DesktopError) as caught: self.host.open()
        self.assertEqual(46, caught.exception.code)
        self.assertIsNone(self.host.process)
        self.notifier.bind_owned.assert_not_called()
        self.assertEqual(1, len(self.launches))

    def test_wrong_pid_or_unverified_hwnd_is_rejected_and_child_cleaned_up(self):
        self.mode = 'wrong'
        with self.assertRaises(DesktopError): self.host.open()
        self.assertIsNotNone(self.processes[-1].poll())
        self.notifier.bind_owned.assert_not_called()
        self.mode = 'ok'; self.notifier.bind_owned.return_value = False
        with self.assertRaises(DesktopError): self.host.open()
        self.assertIsNotNone(self.processes[-1].poll())

    def test_unresponsive_live_host_does_not_create_another_window(self):
        self.host.open()
        with patch.object(self.host, '_command', side_effect=OSError('fixture')):
            with self.assertRaises(DesktopError): self.host.open()
        self.assertEqual(1, len(self.launches))

    def test_crashed_host_can_be_reopened_without_a_new_ai_request(self):
        self.host.open()
        self.processes[0].terminate(); self.processes[0].wait(5)
        self.assertEqual('opened', self.host.open()['action'])
        self.assertEqual(2, len(self.launches))
        self.assertNotEqual(self.processes[0].pid, self.processes[1].pid)

    def test_missing_payload_never_launches_an_alternative_app(self):
        self.binary.unlink()
        with self.assertRaises(DesktopError): self.host.open()
        self.assertEqual([], self.launches)

    def test_shutdown_confirmation_ack_only_means_displayed_and_keeps_host_usable(self):
        close_requested = Mock()
        self.host.on_close = close_requested
        self.host.open()
        with self.host.lock:
            self.host._command('hide')
        self.assertTrue(self.host.confirm_shutdown('a' * 32))
        self.assertEqual({'command': 'confirm_shutdown', 'confirmationId': 'a' * 32, 'id': 2},
                         self.request_log()[-1])
        self.assertIsNone(self.processes[0].poll())
        close_requested.assert_not_called()
        self.assertEqual('activated', self.host.open()['action'])
        self.assertEqual(1, len(self.launches))
        self.assertNotIn('close', [row['command'] for row in self.request_log()])

    def test_shutdown_confirmation_rejects_invalid_ids_before_ipc(self):
        self.host.open()
        for invalid in (None, False, 1, 'a' * 31, 'a' * 33, 'A' * 32, 'a' * 32 + '\n',
                        "');globalThis.close();//"):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.host.confirm_shutdown(invalid))
        self.assertFalse(self.requests.exists())
        self.assertIsNone(self.processes[0].poll())

    def test_shutdown_confirmation_unavailable_never_launches_or_reopens_host(self):
        self.assertFalse(self.host.confirm_shutdown('b' * 32))
        self.assertEqual([], self.launches)
        self.host.open()
        self.host.close()
        self.assertFalse(self.host.confirm_shutdown('b' * 32))
        self.assertEqual(1, len(self.launches))

    def test_shutdown_confirmation_requires_explicit_boolean_display_ack(self):
        for mode in ('confirm_reject', 'confirm_missing', 'confirm_string'):
            with self.subTest(mode=mode):
                self.mode = mode
                self.host.open()
                self.assertFalse(self.host.confirm_shutdown('c' * 32))
                self.assertIsNone(self.processes[-1].poll())
                self.assertEqual('activated', self.host.open()['action'])
                with self.host.lock:
                    self.host._dispose()

    def test_shutdown_confirmation_lost_ack_does_not_approve_close_or_duplicate_host(self):
        self.host.open()
        for error in (queue.Empty(), OSError('fixture')):
            with self.subTest(error=type(error).__name__):
                with patch.object(self.host, '_command', side_effect=error):
                    self.assertFalse(self.host.confirm_shutdown('d' * 32))
        self.assertIsNone(self.processes[0].poll())
        self.assertEqual(1, len(self.launches))
        self.assertEqual('activated', self.host.open()['action'])

    def test_shutdown_confirmation_pipe_exit_is_failure_without_relaunch(self):
        self.mode = 'exit_on_confirm'
        self.host.open()
        self.assertFalse(self.host.confirm_shutdown('e' * 32))
        self.processes[0].wait(2)
        self.assertFalse(self.host.confirm_shutdown('e' * 32))
        self.assertEqual(1, len(self.launches))

    def notify(self, **kwargs):
        values = {'title': '검증 업무', 'message': '작업이 완료됐어요.', 'kind': 'completed',
                  'notification_id': 'a' * 64, 'on_click': Mock()}
        values.update(kwargs)
        return self.host.notify(**values)

    def request_log(self):
        return [json.loads(line) for line in self.requests.read_text(encoding='utf-8').splitlines()]

    def emit(self, kind, key='a' * 64):
        with self.host.lock:
            return self.host._command('fixture_event', eventType=kind, notificationId=key)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_card_unavailable_never_starts_a_host_or_opens_a_window(self):
        self.assertFalse(self.host.notification_available)
        self.assertIsNone(self.notify())
        self.assertEqual([], self.launches)
        self.mode = 'unsupported'
        self.host.open()
        self.assertFalse(self.host.notification_available)
        self.assertIsNone(self.notify())
        self.assertEqual([], self.request_log() if self.requests.exists() else [])
        self.host.close()
        self.assertIsNone(self.notify())
        self.assertEqual(1, len(self.launches))

    def test_card_payload_is_bounded_sanitized_and_contains_no_launch_credentials(self):
        self.host.open()
        self.assertTrue(self.host.notification_available)
        self.assertTrue(self.notify(title='검증\n\x00\u202e' + '업' * 130,
                                    message='완료\r\t' + '가' * 300, kind='attention',
                                    summary='형식 확인\n token=glpat-abcdefghijk ' + '가' * 150))
        request = self.request_log()[-1]
        self.assertEqual({'command', 'id', 'notificationId', 'kind', 'title', 'message', 'summary'}, set(request))
        self.assertEqual('notify', request['command'])
        self.assertEqual('attention', request['kind'])
        self.assertEqual('a' * 64, request['notificationId'])
        self.assertEqual(100, len(request['title']))
        self.assertEqual(255, len(request['message']))
        self.assertLessEqual(len(request['summary']), 100)
        self.assertTrue(request['summary'].endswith('…'))
        self.assertNotIn('glpat-', request['summary'])
        self.assertNotRegex(request['title'] + request['message'], r'[\x00-\x1f\u202e]')
        self.assertNotIn(self.url, json.dumps(request))
        self.assertNotIn(self.host.profile, json.dumps(request))
        self.assertEqual(1, len(self.launches))
        self.assertEqual(['notify'], [row['command'] for row in self.request_log()])

    def test_legacy_attention_receipt_without_summary_keeps_an_action_line(self):
        self.host.open()
        self.assertTrue(self.notify(kind='attention'))
        self.assertEqual(DEFAULT_REQUEST_SUMMARY, self.request_log()[-1]['summary'])

    def test_card_invalid_inputs_are_rejected_before_any_ipc(self):
        self.host.open()
        for changes in ({'notification_id': 'a' * 63}, {'notification_id': 'A' * 64},
                        {'notification_id': '../' + 'a' * 64}, {'notification_id': []},
                        {'kind': 'other'}, {'kind': []}, {'title': None},
                        {'message': {}}, {'on_click': None}):
            with self.subTest(changes=changes):
                self.assertFalse(self.notify(**changes))
        self.assertFalse(self.requests.exists())

    def test_one_card_refuses_replacement_and_only_exact_click_routes_once(self):
        self.host.open()
        clicked = threading.Event()
        callback = Mock(side_effect=clicked.set)
        self.assertTrue(self.notify(on_click=callback))
        self.assertEqual('busy', self.notify(notification_id='b' * 64))
        self.assertEqual(1, sum(row['command'] == 'notify' for row in self.request_log()))
        self.emit('notification_opened', 'b' * 64)
        callback.assert_not_called()
        self.emit('notification_opened')
        self.assertTrue(clicked.wait(2))
        self.emit('notification_opened')
        callback.assert_called_once_with()
        self.assertTrue(self.notify(notification_id='b' * 64))

    def test_dismissal_releases_card_without_marking_it_open(self):
        self.host.open()
        callback = Mock()
        self.assertTrue(self.notify(on_click=callback))
        self.emit('notification_dismissed')
        callback.assert_not_called()
        self.emit('notification_opened')
        callback.assert_not_called()
        self.assertTrue(self.notify(notification_id='b' * 64))

    def test_suppressed_or_rejected_card_returns_false_and_releases_callback(self):
        for mode in ('suppressed', 'reject'):
            with self.subTest(mode=mode):
                self.mode = mode
                self.host.open()
                callback = Mock()
                self.assertIs(self.notify(on_click=callback), False)
                self.emit('notification_opened')
                callback.assert_not_called()
                self.assertIsNone(self.host._notification)
                with self.host.lock:
                    self.host._dispose()

    def test_explicit_native_busy_defers_and_unavailable_allows_tray_fallback(self):
        for mode, expected in (('busy', 'busy'), ('unavailable', None)):
            with self.subTest(mode=mode):
                self.mode = mode
                self.host.open()
                self.assertEqual(expected, self.notify())
                self.assertIsNone(self.host._notification)
                with self.host.lock:
                    self.host._dispose()

    def test_hidden_main_window_keeps_host_and_private_command_delivery_alive(self):
        self.host.background = True
        self.host.on_close = Mock()
        self.host.open()
        process = self.host.process
        self.emit('hidden')
        self.host.on_close.assert_not_called()
        self.assertIsNone(process.poll())
        self.assertTrue(self.notify(kind='attention'))
        self.assertEqual('activated', self.host.open()['action'])
        self.assertIs(process, self.host.process)
        self.host.close()
        self.assertIsNotNone(process.poll())

    def test_explicit_close_request_reaches_shutdown_callback_without_hidden_event(self):
        closed = threading.Event()
        self.host.on_close = closed.set
        self.host.open()
        self.emit('close_requested')
        self.assertTrue(closed.wait(2))

    def test_lost_ack_is_not_reported_as_safe_for_balloon_fallback(self):
        self.host.open()
        with patch.object(self.host, '_command', side_effect=OSError('lost acknowledgement')):
            self.assertIs(self.notify(), False)
        self.assertIsNone(self.host._notification)
        self.assertEqual(1, len(self.launches))

    def test_pipe_exit_during_delivery_does_not_fall_back_or_keep_callback(self):
        self.mode = 'exit_on_notify'
        self.host.open()
        callback = Mock()
        self.assertIs(self.notify(on_click=callback), False)
        self.assertIsNone(self.host._notification)
        self.assertFalse(self.host.notification_available)
        callback.assert_not_called()
        self.assertEqual(1, len(self.launches))

    def test_click_before_ack_does_not_block_reader_or_callback_navigation(self):
        self.mode = 'open_first'
        self.host.open()
        activated = threading.Event()
        def callback():
            self.assertEqual('activated', self.host.open()['action'])
            activated.set()
        self.assertTrue(self.notify(on_click=callback))
        self.assertTrue(activated.wait(2))
        self.assertIsNone(self.host._notification)
        self.assertEqual(1, len(self.launches))

    def test_early_dismissal_and_callback_exception_leave_ipc_usable(self):
        self.mode = 'dismiss_first'
        self.host.open()
        callback = Mock()
        self.assertTrue(self.notify(on_click=callback))
        callback.assert_not_called()
        self.assertIsNone(self.host._notification)
        with self.host.lock:
            self.host._dispose()
        self.mode = 'open_first'
        self.host.open()
        raised = threading.Event()
        def broken_callback():
            raised.set()
            raise RuntimeError('navigation fixture')
        self.assertTrue(self.notify(on_click=broken_callback))
        self.assertTrue(raised.wait(2))
        self.assertEqual('activated', self.host.open()['action'])

    def test_retired_process_events_cannot_consume_a_new_card_callback(self):
        self.host.open()
        old_process = self.host.process
        old_callback = Mock()
        self.assertTrue(self.notify(on_click=old_callback))
        with self.host.lock:
            self.host._dispose()
        self.assertFalse(self.host.notification_available)
        self.assertIsNone(self.host._notification)
        self.host.open()
        clicked = threading.Event()
        callback = Mock(side_effect=clicked.set)
        self.assertTrue(self.notify(on_click=callback))
        self.host._notification_event(old_process, {'type': 'notification_opened', 'notificationId': 'a' * 64})
        old_callback.assert_not_called()
        callback.assert_not_called()
        self.emit('notification_opened')
        self.assertTrue(clicked.wait(2))
        callback.assert_called_once_with()

    def test_process_exit_and_close_release_pending_callback_and_capability(self):
        self.host.open()
        callback = Mock()
        self.assertTrue(self.notify(on_click=callback))
        self.host.process.terminate()
        self.host.process.wait(5)
        self.wait_for(lambda: self.host._notification is None)
        self.assertFalse(self.host.notification_available)
        self.assertIsNone(self.notify())
        self.host.open()
        self.assertTrue(self.notify(on_click=callback))
        self.host.close()
        self.assertIsNone(self.host._notification)
        self.assertFalse(self.host.notification_available)
        callback.assert_not_called()


class NativeOwnershipTests(unittest.TestCase):
    def test_friendly_caption_alone_is_not_an_identity(self):
        native = object.__new__(WindowsAttention)
        binding = WindowBinding(101, 20, 300, 1, b'own', os.path.normcase(str(Path('owned.exe').resolve())))
        native._window = Mock(return_value=binding)
        with patch('local_app.native_window.desktop_executable', return_value=Path('owned.exe').resolve()):
            self.assertEqual(binding, native.owned(101, 20))
            self.assertIsNone(native.owned(101, 21))
        with patch('local_app.native_window.desktop_executable', return_value=Path('other.exe').resolve()):
            self.assertIsNone(native.owned(101, 20))

    def test_owned_binding_does_not_find_another_window_with_the_same_caption(self):
        native = Mock()
        native.owned.return_value = 'owned'
        native.visibility.return_value = False
        notifier = AttentionNotifier(native=native)
        self.assertTrue(notifier.bind_owned(101, 20))
        notifier.bind()
        self.assertFalse(notifier.set_visible(True))
        native.find.assert_not_called(); native.bind.assert_not_called()
        native.visibility.assert_called_once_with('owned', 'Workspace', show=True)


if __name__ == '__main__': unittest.main()
