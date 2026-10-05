"""Deterministic process-lifecycle tests; no user CLI or process discovery."""
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_app.bridge import CLI_EOF_GRACE, ClaudeSession


class WorkspaceCloseTests(unittest.TestCase):
    def session(self):
        events = []
        bridge = ClaudeSession(["fake-cli"], {}, Path.cwd(),
                               lambda kind, data: events.append((kind, data)))
        process = Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fake-cli", CLI_EOF_GRACE), 0]
        bridge.process = process
        return bridge, process, events

    def test_failed_tree_stop_kills_and_reaps_owned_handle_without_initial_wait(self):
        bridge, process, _ = self.session()
        calls = []
        process.kill.side_effect = lambda: calls.append("kill")
        def wait(**kwargs):
            if kwargs["timeout"] == CLI_EOF_GRACE:
                raise subprocess.TimeoutExpired("fake-cli", CLI_EOF_GRACE)
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
        process.wait.side_effect = [subprocess.TimeoutExpired("fake-cli", CLI_EOF_GRACE),
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
        process.wait.assert_called_once_with(timeout=CLI_EOF_GRACE)

    def test_same_thread_reentry_does_not_deadlock(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = lambda **kwargs: bridge.close()
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)):
            bridge.close()
        process.wait.assert_called_once_with(timeout=CLI_EOF_GRACE)

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
                patch('local_app.bridge.WindowsJob', return_value=None), \
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
        process.wait.assert_called_once_with(timeout=CLI_EOF_GRACE)

    def test_delayed_eof_cleanup_finishes_without_forced_tree_stop(self):
        bridge = ClaudeSession(['unused'], {}, Path.cwd(), lambda *_: None)
        process = subprocess.Popen([sys.executable, '-B', '-c',
                                    'import sys,time; print("ready",flush=True); '
                                    'sys.stdin.buffer.read(); time.sleep(.65); sys.exit(23)'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        bridge.process = process
        try:
            self.assertEqual(process.stdout.readline().strip(), b'ready')
            with patch('local_app.bridge.subprocess.run') as tree_stop, \
                    patch.object(process, 'kill', wraps=process.kill) as kill, \
                    patch.object(process, 'terminate', wraps=process.terminate) as terminate:
                self.assertTrue(bridge.close())
            tree_stop.assert_not_called()
            kill.assert_not_called()
            terminate.assert_not_called()
            self.assertEqual(process.poll(), 23)
            self.assertTrue(bridge.cleanup_complete)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_unconfirmed_exit_is_not_reported_as_stopped(self):
        bridge, process, events = self.session()
        process.wait.side_effect = subprocess.TimeoutExpired("fake-cli", 5)
        with patch("local_app.bridge.os.name", "nt"), \
                patch("local_app.bridge.subprocess.run", return_value=Mock(returncode=0)), \
                patch("local_app.bridge.time.sleep"):
            bridge._finish_stop()
        self.assertFalse(any(kind == "status" and data.get("state") == "stopped" for kind, data in events))
        self.assertTrue(any(kind == "error" for kind, data in events))

    def test_completed_handle_failure_can_retry_and_reap_the_same_child(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = [subprocess.TimeoutExpired('fake-cli', CLI_EOF_GRACE),
                                   subprocess.TimeoutExpired('fake-cli', 5),
                                   subprocess.TimeoutExpired('fake-cli', 5), 0]
        with patch('local_app.bridge.os.name', 'nt'), \
                patch('local_app.bridge.subprocess.run', return_value=Mock(returncode=0)):
            self.assertFalse(bridge.close())
            self.assertFalse(bridge.cleanup_complete)
            self.assertFalse(bridge.descendant_cleanup_uncertain)
            self.assertTrue(bridge.close())
        self.assertTrue(bridge.cleanup_complete)
        self.assertIs(bridge.process, process)
        self.assertEqual(process.wait.call_count, 4)

    def test_retry_after_real_owned_child_exits_recovers_wait_failure(self):
        bridge = ClaudeSession(['unused'], {}, Path.cwd(), lambda *_: None)
        process = subprocess.Popen([sys.executable, '-B', '-c',
                                    'import sys; sys.stdout.write("ready\\n"); sys.stdout.flush(); sys.stdin.buffer.read()'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        bridge.process = process
        try:
            self.assertEqual(process.stdout.readline().strip(), b'ready')
            with patch.object(process, 'wait', side_effect=OSError('transient wait failure')):
                self.assertFalse(bridge.close())
            process.wait(timeout=5)
            self.assertFalse(bridge.cleanup_complete)
            # The original PID may already have been reused. Recheck the owned
            # handle and never send a new tree-stop request for that old PID.
            with patch('local_app.bridge.subprocess.run') as tree_stop:
                self.assertTrue(bridge.close())
            tree_stop.assert_not_called()
            self.assertTrue(bridge.cleanup_complete)
            self.assertIs(bridge.process, process)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_parent_exit_does_not_clear_unverified_descendant_failure(self):
        bridge, process, _ = self.session()
        with patch('local_app.bridge.os.name', 'nt'), \
                patch('local_app.bridge.subprocess.run', return_value=Mock(returncode=1)) as tree_stop:
            self.assertFalse(bridge.close())
            process.poll.return_value = 0
            self.assertFalse(bridge.close())
        self.assertFalse(bridge.cleanup_complete)
        self.assertTrue(bridge.descendant_cleanup_uncertain)
        self.assertEqual(tree_stop.call_count, 1)

    def test_concurrent_retry_callers_share_one_cleanup(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = OSError('transient wait failure')
        self.assertFalse(bridge.close())
        entered, release = threading.Event(), threading.Event()
        results = []
        def wait(**kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return 0
        process.wait.side_effect = wait
        first = threading.Thread(target=lambda: results.append(bridge.close()))
        second = threading.Thread(target=lambda: results.append(bridge.close()))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        try:
            self.assertFalse(bridge.cleanup_complete)
        finally:
            release.set()
            first.join(2)
            second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(results, [True, True])
        self.assertEqual(process.wait.call_count, 2)

    def test_finishing_reader_does_not_retry_failed_cleanup(self):
        bridge, process, _ = self.session()
        process.wait.side_effect = OSError('transient wait failure')
        self.assertFalse(bridge.close())
        bridge._readers = [threading.current_thread()]
        self.assertFalse(bridge.close())
        self.assertEqual(process.wait.call_count, 1)
        self.assertTrue(bridge._close_done.is_set())

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
                patch('local_app.bridge.WindowsJob', return_value=None), \
                patch('local_app.bridge.subprocess.run', return_value=Mock(returncode=1)), \
                patch.object(bridge, '_read'), patch.object(bridge, '_read_stderr'), \
                patch.object(bridge, 'close', side_effect=close):
            starter = threading.Thread(target=bridge.start)
            interrupter = threading.Thread(target=lambda: bridge.interrupt(disconnect=True))
            starter.start()
            self.assertTrue(launching.wait(2))
            interrupter.start()
            try:
                release.set()
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
