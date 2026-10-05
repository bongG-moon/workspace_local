"""Exercise the tray exit path with an isolated server and owned fixture child.

No real tray icon, desktop host, Claude profile, model request, or user process
is used. The native-loop double still processes messages on its own thread.
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.bridge import ClaudeSession
from local_app.server import LocalApp, Server
from local_app.tray import WorkspaceTray


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return bool(predicate())


class ResponsiveNative:
    """A real message thread without Windows Shell calls or desktop changes."""

    def __init__(self, owner):
        self.owner = owner
        self.messages = queue.Queue()
        self.update_handled = threading.Event()
        self.update_thread = None

    def run(self):
        self.owner._mark_ready(True)
        while self.messages.get() != 'stop':
            self.update_thread = threading.current_thread()
            self.update_handled.set()

    def post_update(self):
        self.messages.put('update')

    def request_stop(self):
        self.messages.put('stop')


class TrayShutdownIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', demo=True)
        self.sid = self.app.create(str(self.root), True)['id']
        self.server = Server(self.app)
        self.serving = threading.Event()
        self.server_finished = threading.Event()
        self.opened = threading.Event()
        self.exit_callbacks = []
        self.confirmations = []
        self.confirmation_shown = True
        self.children = []
        self.releases = []
        self.grace = patch('local_app.bridge.CLI_EOF_GRACE', 10)
        self.grace.start()
        self.tray = WorkspaceTray(str(self.root), self.opened.set,
                                  self.quit_from_tray, native_factory=ResponsiveNative)
        self.app.tray = self.tray
        self.assertTrue(self.tray.start())
        self.native = self.tray._backend
        self.server_thread = threading.Thread(target=self.serve, name='fixture-http', daemon=True)
        self.server_thread.start()
        self.assertTrue(self.serving.wait(3))

    def tearDown(self):
        for release in self.releases:
            release.write_text('release', encoding='utf-8')
        try:
            self.server.shutdown()
            self.server_thread.join(5)
            self.app.close()
            self.tray.stop()
            self.server.server_close()
        finally:
            for process in self.children:
                if process.poll() is None:
                    process.kill()  # Exact child created by this test only.
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()
            self.grace.stop()
            self.temp.cleanup()

    def quit_from_tray(self):
        # This is the production main() callback sequence, not the HTTP quit
        # endpoint. WorkspaceTray itself owns callback dispatch and failures.
        self.exit_callbacks.append(threading.current_thread())
        if not self.serving.wait(35):
            return False
        result = self.app.request_quit()
        if result.get('confirmationRequired'):
            identifier = self.app.begin_quit_confirmation()
            self.confirmations.append(identifier)
            if self.confirmation_shown:
                return None
            self.app.discard_quit_confirmation(identifier)
            return False
        if result.get('closed') is not True:
            return False
        self.server.shutdown()
        return True

    def serve(self):
        self.serving.set()
        try:
            self.server.serve_forever(poll_interval=.01)
        finally:
            # Same owner lifecycle as main(): retire the tray only after the
            # HTTP loop exits and the app has confirmed its owned cleanup.
            self.app.close()
            self.tray.stop()
            self.server.server_close()
            self.server_finished.set()

    def request(self, route, body=None):
        request = Request(self.server.origin + route,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={'Authorization': 'Bearer ' + self.app.token,
                                   'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def connected_child(self):
        ready, entered, release = (self.root / name for name in ('ready', 'closing', 'release'))
        self.releases.append(release)
        script = (
            'from pathlib import Path\n'
            'import sys,time\n'
            'ready,entered,release=map(Path,sys.argv[1:])\n'
            'ready.write_text("ready")\n'
            'sys.stdin.buffer.read()\n'
            'entered.write_text("stdin-closed")\n'
            'deadline=time.monotonic()+15\n'
            'while not release.exists() and time.monotonic()<deadline: time.sleep(.01)\n'
            'sys.exit(0 if release.exists() else 91)\n'
        )
        process = subprocess.Popen([sys.executable, '-B', '-u', '-c', script,
                                    str(ready), str(entered), str(release)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=self.root, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.children.append(process)
        self.assertTrue(eventually(ready.exists), 'owned fixture did not start')
        bridge = ClaudeSession(['fixture-not-started-through-cli'], {}, self.root,
                               lambda kind, data: self.app.emit(self.sid, kind, data))
        bridge.process = process
        bridge.ready.set()
        bridge.session_id = 'fixture-session'
        item = self.app.get(self.sid)
        item['bridge'] = bridge
        item['connection'] = {'connected': True, 'sessionId': bridge.session_id}
        item['state'] = 'idle'
        self.assertTrue(self.request('/api/session?id=' + self.sid)[1]['connection']['connected'])
        return bridge, process, entered, release

    def assert_pending_is_responsive(self):
        self.assertEqual('closing', self.app.shutdown_status()['shutdownState'])
        self.assertTrue(self.tray.available)
        self.assertIn('종료하는 중', self.tray._text())
        self.tray._dispatch('exit')
        self.tray._dispatch('open')
        self.assertFalse(self.opened.is_set())
        self.native.update_handled.clear()
        self.tray.update(running=2, waiting=1)
        self.assertTrue(self.native.update_handled.wait(2), 'tray message loop was blocked by exit')
        self.assertIs(self.native.update_thread, self.tray._thread)
        self.assertTrue(self.server_thread.is_alive())
        self.assertEqual(200, self.request('/api/bootstrap')[0])
        code, body = self.request('/api/send', {'id': self.sid, 'text': 'must never execute'})
        self.assertEqual(409, code)
        self.assertEqual([], self.app.get(self.sid)['messages'])
        self.assertFalse(self.app.shutdown_status()['closed'])

    def assert_completed(self, bridge, process):
        self.assertTrue(self.server_finished.wait(5), 'tray exit did not finish the server lifecycle')
        self.server_thread.join(1)
        self.assertFalse(self.server_thread.is_alive())
        self.assertFalse(self.tray.available)
        self.assertFalse(self.tray.status()['running'])
        self.assertTrue(bridge.cleanup_complete)
        self.assertEqual(0, process.poll())
        self.assertEqual('closed', self.app.shutdown_status()['shutdownState'])
        self.assertEqual([], self.app.shutdown_status()['shutdownIssues'])
        self.assertTrue(eventually(lambda: 'exit' not in self.tray._pending))
        self.assertTrue(all(worker.name == 'WorkspaceTray-exit' for worker in self.exit_callbacks))
        self.assertTrue(all(worker is not self.tray._thread for worker in self.exit_callbacks))

    def test_tray_exit_waits_for_real_owned_child_without_blocking_ui_or_replaying_work(self):
        bridge, process, entered, release = self.connected_child()
        self.tray._dispatch('exit')
        self.assertTrue(eventually(entered.exists), 'tray callback did not close the owned child input')
        self.assert_pending_is_responsive()
        self.assertEqual(1, len(self.exit_callbacks))
        self.assertIsNone(process.poll())
        release.write_text('release', encoding='utf-8')
        self.assert_completed(bridge, process)

    def test_failed_tray_cleanup_keeps_recovery_available_and_retry_reaps_same_child(self):
        bridge, process, entered, release = self.connected_child()
        real_close = bridge.close
        attempts = []

        def fail_once_then_close():
            attempts.append(threading.current_thread())
            return False if len(attempts) == 1 else real_close()

        with patch.object(bridge, 'close', side_effect=fail_once_then_close):
            self.tray._dispatch('exit')
            self.assertTrue(eventually(lambda: self.tray.status()['error'] == 'exit_failed'))
            self.assertTrue(self.tray.available)
            self.assertTrue(self.server_thread.is_alive())
            self.assertFalse(entered.exists())
            self.assertIsNone(process.poll())
            status, snapshot = self.request('/api/bootstrap')
            self.assertEqual(200, status)
            self.assertEqual('failed', snapshot['shutdownState'])
            self.assertEqual([{'sessionId': self.sid, 'code': 'process_cleanup_failed'}], snapshot['shutdownIssues'])
            self.assertFalse(snapshot['closed'])
            self.assertEqual(409, self.request('/api/send', {'id': self.sid, 'text': 'must never replay'})[0])
            self.tray._dispatch('open')
            self.assertTrue(self.opened.wait(2))
            self.assertTrue(eventually(lambda: 'open' not in self.tray._pending))
            self.opened.clear()
            self.tray._dispatch('exit')
            self.assertTrue(eventually(entered.exists))
            self.assert_pending_is_responsive()
            self.assertEqual(2, len(self.exit_callbacks))
            release.write_text('release', encoding='utf-8')
            self.assert_completed(bridge, process)
            self.assertEqual(2, len(attempts))

    def test_active_tray_cancel_keeps_owned_task_and_allows_new_confirmation(self):
        bridge, process, entered, _ = self.connected_child()
        self.app.get(self.sid)['state'] = 'running'
        bridge.busy = True
        self.tray._dispatch('exit')
        self.assertTrue(eventually(lambda: len(self.confirmations) == 1 and not self.tray._pending))
        first = self.confirmations[0]
        self.assertRegex(first, r'^[0-9a-f]{32}$')
        self.assertEqual('running', self.app.shutdown_status()['shutdownState'])
        self.assertFalse(self.tray.status()['error'])
        self.assertFalse(entered.exists())
        self.assertIsNone(process.poll())
        status, result = self.request('/api/quit', {'confirmed': False, 'confirmationId': first})
        self.assertEqual(200, status)
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['closing'])
        self.assertEqual('running', self.app.get(self.sid)['state'])
        self.assertTrue(bridge.busy)
        self.assertIsNone(process.poll())
        self.assertFalse(entered.exists())
        self.tray._dispatch('open')
        self.assertTrue(self.opened.wait(2))
        self.assertEqual(409, self.request('/api/quit', {'confirmed': True, 'confirmationId': first})[0])
        self.assertEqual('running', self.app.shutdown_status()['shutdownState'])
        self.tray._dispatch('exit')
        self.assertTrue(eventually(lambda: len(self.confirmations) == 2 and not self.tray._pending))
        self.assertNotEqual(first, self.confirmations[1])
        self.assertFalse(entered.exists())
        self.assertTrue(self.tray.available)

    def test_active_tray_confirmation_reaps_owned_child_before_success_and_exit(self):
        bridge, process, entered, release = self.connected_child()
        self.app.get(self.sid)['state'] = 'approval'
        bridge.busy = True
        self.tray._dispatch('exit')
        self.assertTrue(eventually(lambda: len(self.confirmations) == 1 and not self.tray._pending))
        self.assertFalse(entered.exists(), 'display acknowledgement must not close the task')
        results = []
        failures = []
        def confirm():
            try:
                results.append(self.request('/api/quit', {'confirmed': True,
                                                         'confirmationId': self.confirmations[0]}))
            except Exception as exc:
                failures.append(exc)
        request_thread = threading.Thread(target=confirm, daemon=True)
        request_thread.start()
        self.assertTrue(eventually(entered.exists), 'explicit confirmation did not start owned cleanup')
        self.assertEqual('closing', self.app.shutdown_status()['shutdownState'])
        self.assertEqual([], results)
        self.assertIsNone(process.poll())
        self.assertTrue(self.tray.available)
        self.native.update_handled.clear()
        self.tray.update(running=1, waiting=1)
        self.assertTrue(self.native.update_handled.wait(2))
        self.assertIs(self.native.update_thread, self.tray._thread)
        self.assertEqual(409, self.request('/api/send', {'id': self.sid, 'text': 'must not replay'})[0])
        self.assertEqual([], self.app.get(self.sid)['messages'])
        release.write_text('release', encoding='utf-8')
        request_thread.join(3)
        self.assertFalse(request_thread.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(1, len(results))
        self.assertEqual(200, results[0][0])
        self.assertTrue(results[0][1]['closed'])
        self.assert_completed(bridge, process)

    def test_unavailable_confirmation_discards_challenge_without_stopping_work(self):
        _, process, entered, _ = self.connected_child()
        self.app.get(self.sid)['state'] = 'running'
        self.confirmation_shown = False
        self.tray._dispatch('exit')
        self.assertTrue(eventually(lambda: self.tray.status()['error'] == 'exit_failed'))
        self.assertEqual(1, len(self.confirmations))
        self.assertEqual('running', self.app.shutdown_status()['shutdownState'])
        self.assertTrue(self.tray.available)
        self.assertFalse(entered.exists())
        self.assertIsNone(process.poll())
        status, result = self.request('/api/quit', {'confirmed': True,
                                                  'confirmationId': self.confirmations[0]})
        self.assertEqual(409, status)
        self.assertEqual('quit_confirmation_expired', result['code'])
        self.assertEqual('running', self.app.shutdown_status()['shutdownState'])
        self.assertFalse(entered.exists())


if __name__ == '__main__':
    unittest.main()
