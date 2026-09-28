"""Deterministic process-lifecycle tests; no user CLI or process discovery."""
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_app.bridge import ClaudeSession


class WorkspaceCloseTests(unittest.TestCase):
    def session(self):
        events = []
        bridge = ClaudeSession(["fake-cli"], {}, Path.cwd(),
                               lambda kind, data: events.append((kind, data)))
        process = Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fake-cli", .3), 0]
        bridge.process = process
        return bridge, process, events

    def test_failed_tree_stop_kills_and_reaps_owned_handle_without_initial_wait(self):
        bridge, process, _ = self.session()
        calls = []
        process.kill.side_effect = lambda: calls.append("kill")
        def wait(**kwargs):
            if kwargs["timeout"] == .3:
                raise subprocess.TimeoutExpired("fake-cli", .3)
            calls.append("wait")
        process.wait.side_effect = wait
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=1)) as run:
            bridge.close()
        self.assertEqual(calls, ["kill", "wait"])
        self.assertEqual(run.call_args.args[0], ["taskkill.exe", "/PID", "12345", "/T", "/F"])
        self.assertEqual(process.wait.call_args_list[-1].kwargs, {"timeout": 5})

    def test_timeout_after_tree_stop_is_reaped_after_kill(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = [subprocess.TimeoutExpired("fake-cli", .3),
                                    subprocess.TimeoutExpired("fake-cli", 5), 0]
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)):
            bridge.close()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 3)

    def test_tree_stop_timeout_still_reaps_owned_handle(self):
        bridge, process, _ = self.session()
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", side_effect=subprocess.TimeoutExpired("taskkill", 10)):
            bridge.close()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_args_list[-1].kwargs, {"timeout": 5})

    def test_concurrent_close_waits_for_owned_process_to_be_reaped(self):
        bridge, process, _ = self.session()
        waiting, release, returned = threading.Event(), threading.Event(), threading.Event()
        def wait(**kwargs):
            waiting.set()
            self.assertTrue(release.wait(2))
        process.wait.side_effect = wait
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)):
            first = threading.Thread(target=bridge.close)
            second = threading.Thread(target=lambda: (bridge.close(), returned.set()))
            first.start()
            self.assertTrue(waiting.wait(2))
            second.start()
            try:
                self.assertFalse(returned.wait(.1))
            finally:
                release.set()
                first.join(2)
                second.join(2)
        self.assertTrue(returned.is_set())
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        process.wait.assert_called_once_with(timeout=.3)

    def test_same_thread_reentry_does_not_deadlock(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = lambda **kwargs: bridge.close()
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)):
            bridge.close()
        process.wait.assert_called_once_with(timeout=.3)

    def test_reader_callback_can_finish_during_another_threads_close(self):
        bridge, process, _ = self.session()
        waiting, reader_finished = threading.Event(), threading.Event()
        def wait(**kwargs):
            waiting.set()
            self.assertTrue(reader_finished.wait(2))
        def read_callback():
            self.assertTrue(waiting.wait(2))
            bridge.close()
            reader_finished.set()
        process.wait.side_effect = wait
        reader = threading.Thread(target=read_callback)
        bridge._readers = [reader]
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)):
            reader.start()
            bridge.close()
            reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(reader_finished.is_set())
        process.stdout.close.assert_not_called()

    def test_close_during_start_reaps_the_just_created_owned_process(self):
        bridge, process, _ = self.session()
        bridge.process = None
        launching, release, finished = threading.Event(), threading.Event(), threading.Event()
        def popen(*args, **kwargs):
            launching.set()
            self.assertTrue(release.wait(2))
            return process
        def close():
            bridge.close()
            finished.set()
        with patch("local_app.bridge.subprocess.Popen", side_effect=popen), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)), \
                patch.object(bridge, "_read"), patch.object(bridge, "_read_stderr"):
            starter = threading.Thread(target=bridge.start)
            closer = threading.Thread(target=close)
            starter.start()
            self.assertTrue(launching.wait(2))
            closer.start()
            try:
                self.assertFalse(finished.wait(.1))
            finally:
                release.set()
                starter.join(2)
                closer.join(2)
        self.assertTrue(finished.is_set())
        self.assertFalse(starter.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(process.wait.call_args_list[-1].kwargs, {"timeout": 5})

    def test_stdin_eof_reaps_wrapper_without_forced_tree_stop(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = lambda **kwargs: process.stdin.close.assert_called_once_with()
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run") as run:
            self.assertTrue(bridge.close())
        run.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_called_once_with(timeout=.3)

    def test_unconfirmed_exit_is_not_reported_as_stopped(self):
        bridge, process, events = self.session()
        process.wait.side_effect = subprocess.TimeoutExpired("fake-cli", 5)
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)), \
                patch("local_app.bridge.time.sleep"):
            bridge._finish_stop()
        self.assertFalse(any(kind == "status" and data.get("state") == "stopped" for kind, data in events))
        self.assertTrue(any(kind == "error" for kind, data in events))

    def test_interrupt_during_process_creation_does_not_report_failed_close_as_stopped(self):
        bridge, process, events = self.session()
        bridge.process = None
        launching, release, closing = threading.Event(), threading.Event(), threading.Event()
        process.wait.side_effect = subprocess.TimeoutExpired('fake-cli', 5)
        original_close = bridge.close
        def popen(*args, **kwargs):
            launching.set()
            self.assertTrue(release.wait(2))
            return process
        def close():
            closing.set()
            return original_close()
        with patch('local_app.bridge.subprocess.Popen', side_effect=popen), \
                patch('local_app.bridge.subprocess.run', return_value=Mock(returncode=1)), \
                patch.object(bridge, '_read'), patch.object(bridge, '_read_stderr'), \
                patch.object(bridge, 'close', side_effect=close):
            starter = threading.Thread(target=bridge.start)
            interrupter = threading.Thread(target=bridge.interrupt)
            starter.start()
            self.assertTrue(launching.wait(2))
            interrupter.start()
            try:
                self.assertTrue(closing.wait(2))
            finally:
                release.set()
                starter.join(2)
                interrupter.join(2)
        self.assertFalse(starter.is_alive())
        self.assertFalse(interrupter.is_alive())
        self.assertTrue(any(kind == 'error' for kind, data in events))
        self.assertFalse(any(kind == 'status' and data.get('state') == 'stopped' for kind, data in events))


if __name__ == "__main__":
    unittest.main()
