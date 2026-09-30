"""Private desktop protocol and window identity; no real UI or Claude."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_app.native_window import DesktopHost, DesktopError
from local_app.attention import AttentionNotifier, WindowBinding, WindowsAttention

FAKE = r'''
import json, os, sys
config = json.loads(sys.stdin.readline())
mode = sys.argv[1]
if mode == 'missing':
 print(json.dumps({'type':'error','code':46}), flush=True); sys.exit(46)
print(json.dumps({'type':'ready','pid':os.getpid() + (1 if mode=='wrong' else 0),'hwnd':123,'runtime':'fixture'}), flush=True)
for line in sys.stdin:
 request=json.loads(line)
 if request['command']=='close': break
 print(json.dumps({'type':'ack','id':request['id'],'ok':True}), flush=True)
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
        self.url = 'http://127.0.0.1:54321/#token=' + 'x'*43
        def popen(command, **kwargs):
            self.launches.append((command, kwargs))
            process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, self.mode], **kwargs)
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
