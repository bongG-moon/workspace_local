"""Owned window lifecycle contracts; synthetic HWNDs only, no desktop changes."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from local_app.attention import AttentionNotifier, WindowsAttention
from local_app.server import LocalApp, Server


class SyntheticWindows:
    def __init__(self):
        self.hwnd = 0x100000002
        self.foreground = self.hwnd
        self.title = 'Company Workspace · synthetic-window'
        self.pid = 77
        self.exists = self.visible = True
        self.ancestor = self.hwnd
        self.show_allowed = self.focus_allowed = True
        self.shows, self.focuses, self.flashes = [], [], []

    def GetForegroundWindow(self): return self.foreground
    def IsWindow(self, hwnd): return self.exists
    def IsWindowVisible(self, hwnd): return self.visible
    def GetAncestor(self, hwnd, flag): return self.ancestor
    def GetWindowTextLengthW(self, hwnd): return len(self.title)
    def GetWindowTextW(self, hwnd, text, length):
        text.value = self.title
        return len(self.title)
    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = self.pid
        return 9
    def ShowWindow(self, hwnd, command):
        self.shows.append((hwnd, command))
        previous = self.visible
        if self.show_allowed:
            self.visible = command != 0
        return int(previous)  # The result is the previous state, not success.
    def SetForegroundWindow(self, hwnd):
        self.focuses.append(hwnd)
        if self.focus_allowed:
            self.foreground = hwnd
        return int(self.focus_allowed)
    def FlashWindowEx(self, value):
        self.flashes.append(value._obj.hwnd)
        return 0


def native_window():
    native = object.__new__(WindowsAttention)
    native.user = SyntheticWindows()
    native.wt = wintypes
    native.own_session, native.own_sid = 2, b'this-user'
    native.allowed_images = {'verified-edge.exe'}
    native.identity = (1234567, 2, b'this-user', 'verified-edge.exe')
    native._process = lambda pid: native.identity
    class FlashInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('hwnd', wintypes.HWND), ('dwFlags', wintypes.DWORD),
                    ('uCount', wintypes.UINT), ('dwTimeout', wintypes.DWORD)]
    native.FlashInfo = FlashInfo
    return native


class VerifiedWindowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.native = native_window()
        self.title = self.native.user.title
        self.binding = self.native.bind(self.title)

    def test_hide_and_restore_reuse_exact_owned_window_including_hidden_identity(self):
        user = self.native.user
        self.assertTrue(self.native.visibility(self.binding, self.title, show=False))
        self.assertFalse(user.visible)
        self.assertEqual([(user.hwnd, 0)], user.shows)
        self.assertEqual([], user.focuses)
        user.foreground = 900  # A different app remains untouched during hide.
        self.assertTrue(self.native.visibility(self.binding, self.title, show=True))
        self.assertTrue(user.visible)
        self.assertEqual([(user.hwnd, 0), (user.hwnd, 9)], user.shows)
        self.assertEqual([user.hwnd], user.focuses)

    def test_hidden_window_is_not_rebound_from_foreground_or_unbound_by_attention(self):
        notifier = AttentionNotifier(native=self.native)
        notifier.window_title = self.title
        self.assertTrue(notifier.bind()['bound'])
        self.assertTrue(notifier.set_visible(False))
        before = len(self.native.user.flashes)
        self.assertIsNone(self.native.bind(self.title))
        notifier.update({'revision': 'one', 'items': [{'id': 'pending-request'}]})
        self.assertEqual(before, len(self.native.user.flashes))
        self.assertTrue(notifier.native_state['bound'])
        self.assertTrue(notifier.set_visible(True))

    def test_recycled_or_changed_identity_never_hides_restores_or_focuses(self):
        mutations = {
            'pid': lambda n: setattr(n.user, 'pid', 78),
            'created': lambda n: setattr(n, 'identity', (9999999, *n.identity[1:])),
            'session': lambda n: setattr(n, 'identity', (1234567, 3, b'this-user', 'verified-edge.exe')),
            'user': lambda n: setattr(n, 'identity', (1234567, 2, b'other-user', 'verified-edge.exe')),
            'executable': lambda n: setattr(n, 'identity', (1234567, 2, b'this-user', 'unverified.exe')),
            'title': lambda n: setattr(n.user, 'title', 'Another app'),
            'child_window': lambda n: setattr(n.user, 'ancestor', 123),
            'destroyed': lambda n: setattr(n.user, 'exists', False),
            'unqueryable': lambda n: setattr(n, 'identity', None),
        }
        for name, mutate in mutations.items():
            for show in (False, True):
                with self.subTest(identity=name, show=show):
                    native = native_window()
                    binding = native.bind(native.user.title)
                    mutate(native)
                    self.assertFalse(native.visibility(binding, self.title, show=show))
                    self.assertEqual([], native.user.shows)
                    self.assertEqual([], native.user.focuses)

    def test_visibility_failure_does_not_claim_hidden_or_restored(self):
        self.native.user.show_allowed = False
        self.assertFalse(self.native.visibility(self.binding, self.title, show=False))
        self.assertTrue(self.native.user.visible)
        self.native.user.visible = False
        self.assertFalse(self.native.visibility(self.binding, self.title, show=True))
        self.assertFalse(self.native.user.visible)

    def test_focus_policy_denial_does_not_retarget_another_window(self):
        self.native.user.visible = False
        self.native.user.foreground = 900
        self.native.user.focus_allowed = False
        self.assertTrue(self.native.visibility(self.binding, self.title, show=True))
        self.assertTrue(self.native.user.visible)
        self.assertEqual(900, self.native.user.foreground)
        self.assertEqual([self.binding.hwnd], self.native.user.focuses)

    def test_notifier_absent_closed_or_failing_native_returns_false(self):
        absent = AttentionNotifier(enabled=False)
        self.assertFalse(absent.set_visible(False))
        notifier = AttentionNotifier(native=self.native)
        notifier.window_title = self.title
        self.assertFalse(notifier.set_visible(False))
        notifier.bind()
        with patch.object(self.native, 'visibility', side_effect=OSError('private diagnostic')):
            self.assertFalse(notifier.set_visible(False))
        notifier.close()
        self.native.user.shows.clear()
        self.assertFalse(notifier.set_visible(True))
        self.assertEqual([], self.native.user.shows)


class PlainClaudeWindowServerTests(unittest.TestCase):
    """No CLI execution, registration/auth lookup, or Company Agent prerequisite."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-window-')
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state', command=['plain-claude-fixture'],
                            managed_workspace_root=self.root / 'tasks')
        self.harness = patch('local_app.harness_client.HarnessClient._installation',
                                    side_effect=AssertionError('Window/upload must not require Company Agent'))
        self.harness.start()
        self.native = native_window()
        self.app.notifier = AttentionNotifier(native=self.native)
        self.app.notifier.window_title = self.native.user.title
        self.app.tray = Mock(available=True)
        self.sid = self.app.create(str(self.root), True)['id']
        self.server = Server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.app.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.harness.stop()
        self.temp.cleanup()

    def request(self, path, value=None, *, authorized=True, binary=None, filename=None):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['Authorization'] = 'Bearer ' + self.app.token
        body = json.dumps(value).encode() if value is not None else None
        if binary is not None:
            body = binary
            headers['Content-Type'] = 'application/octet-stream'
            headers['X-File-Name'] = quote(filename)
        request = Request(self.server.origin + path, data=body, headers=headers)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_hide_api_requires_auth_and_ignores_client_native_handles(self):
        status, _ = self.request('/api/window/hide', {}, authorized=False)
        self.assertEqual(403, status)
        self.assertEqual([], self.native.user.shows)
        status, payload = self.request('/api/window/hide', {'hwnd': 900, 'pid': 88, 'title': 'Other'})
        self.assertEqual(200, status)
        self.assertTrue(payload['hidden'])
        self.assertEqual([(self.native.user.hwnd, 0)], self.native.user.shows)
        self.assertEqual([], self.native.user.focuses)

    def test_unavailable_tray_never_hides_and_bootstrap_reports_fallback(self):
        for tray in (None, Mock(available=False)):
            self.app.tray = tray
            status, bootstrap = self.request('/api/bootstrap')
            self.assertEqual(200, status)
            self.assertFalse(bootstrap['window']['hideSupported'])
            self.assertFalse(bootstrap['window']['traySupported'])
            status, result = self.request('/api/window/hide', {})
            self.assertEqual(200, status)
            self.assertFalse(result['supported'])
            self.assertFalse(result['hidden'])
        self.assertEqual([], self.native.user.shows)

    def test_hide_preserves_running_work_and_readable_server_until_explicit_quit(self):
        item = self.app.get(self.sid)
        bridge = Mock(closed=False)
        bridge.close.return_value = True
        item.update(bridge=bridge, state='running')
        self.assertTrue(self.request('/api/window/hide', {})[1]['hidden'])
        self.assertEqual('running', item['state'])
        bridge.close.assert_not_called()
        self.assertTrue(self.thread.is_alive())
        self.assertFalse(self.request('/api/bootstrap')[1]['closing'])
        self.assertTrue(self.app.notifier.set_visible(True))
        bridge.close.assert_not_called()
        status, result = self.request('/api/quit', {})
        self.assertEqual(200, status)
        self.assertTrue(result['closed'])
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        bridge.close.assert_called_once()

    def test_failed_native_hide_is_truthful_and_keeps_server_and_work(self):
        self.native.user.show_allowed = False
        status, result = self.request('/api/window/hide', {})
        self.assertEqual(200, status)
        self.assertFalse(result['hidden'])
        self.assertTrue(self.native.user.visible)
        self.assertTrue(self.thread.is_alive())
        self.assertIn(self.sid, self.app.sessions)

    def test_plain_claude_attachment_and_tray_counts_need_no_company_agent(self):
        content = '한글 검증 자료'.encode('utf-8')
        status, result = self.request('/api/attachments/upload?id=' + self.sid,
                                      binary=content, filename='검증 자료.txt')
        self.assertEqual(200, status, result)
        target = Path(result['path'])
        self.assertEqual(content, target.read_bytes())
        self.assertTrue(target.is_relative_to(self.root / 'state' / 'attachments'))
        item = self.app.get(self.sid)
        item.update(state='approval', requests={'one': {'id': 'one', 'tool': 'Bash'}})
        self.app.update_tray()
        self.app.tray.update.assert_called_with(running=0, waiting=1)
        self.assertTrue(self.request('/api/bootstrap')[1]['window']['hideSupported'])


if __name__ == '__main__':
    unittest.main()
