"""Real isolated Windows descendants, with no installed CLI or user processes."""
import os
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_app.bridge import BridgeError, ClaudeSession
from local_app.windows_job import CREATE_SUSPENDED, WindowsJob, _ThreadEntry


TREE = r'''
from pathlib import Path
import subprocess, sys, time
role, mode = sys.argv[1:3]
if role == 'leaf':
    Path('leaf-ready').touch()
    time.sleep(60)
else:
    child = subprocess.Popen([sys.executable, '-B', __file__,
                              'middle' if role == 'root' else 'leaf', mode],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    if role == 'root' and mode == 'orphan':
        while not Path('leaf-ready').exists():
            time.sleep(.01)
        sys.exit(23)
    time.sleep(60)
'''


@unittest.skipUnless(os.name == 'nt', 'Windows job ownership')
class WindowsJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-job-')
        def remove_fixture():
            # Windows may finish releasing a terminated orphan's cwd after
            # the job's active count reaches zero. Bound that filesystem wait.
            deadline = time.monotonic() + 3
            while True:
                try:
                    temporary.cleanup()
                    return
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
        self.addCleanup(remove_fixture)
        self.root = Path(temporary.name)

    def own(self, command):
        job = WindowsJob()
        process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED)
        bridge = ClaudeSession(['unused'], {}, self.root, lambda *_: None)
        bridge.process, bridge._process_job = process, job
        def cleanup():
            if job.assigned:
                job.terminate()
                self.assertTrue(job.wait_empty(5))
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            job.release_empty()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        self.addCleanup(cleanup)
        job.assign_and_resume(process)
        return bridge, job, process

    def tree(self, mode='live'):
        script = self.root / 'tree.py'
        script.write_text(TREE)
        bridge, job, process = self.own([sys.executable, '-B', str(script), 'root', mode])
        deadline = time.monotonic() + 5
        while not (self.root / 'leaf-ready').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.root / 'leaf-ready').exists())
        return bridge, job, process

    def test_root_exits_before_close_but_job_reaps_child_and_grandchild(self):
        bridge, job, process = self.tree('orphan')
        self.assertEqual(process.wait(timeout=5), 23)
        self.assertGreaterEqual(job.active_processes(), 2)
        with patch('local_app.bridge.CLI_EOF_GRACE', .05), \
                patch('local_app.bridge.subprocess.run', side_effect=AssertionError('no taskkill')):
            self.assertTrue(bridge.close())
        self.assertTrue(bridge.cleanup_complete)
        self.assertEqual(job.active_processes(), 0)
        self.assertIsNone(job._handle)
        self.assertTrue(bridge.close())

    def test_forced_close_reaps_entire_job_and_leaves_unrelated_fixture_running(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: (unrelated.kill(), unrelated.wait(timeout=5)))
        bridge, job, process = self.tree()
        self.assertGreaterEqual(job.active_processes(), 3)
        with patch('local_app.bridge.CLI_EOF_GRACE', .05), \
                patch('local_app.bridge.subprocess.run', side_effect=AssertionError('no taskkill')):
            self.assertTrue(bridge.close())
        self.assertIsNotNone(process.poll())
        self.assertEqual(job.active_processes(), 0)
        self.assertIsNone(unrelated.poll())

    def test_failed_query_retains_exact_job_and_retry_cleans_orphan_descendants(self):
        bridge, job, process = self.tree('orphan')
        process.wait(timeout=5)
        handle = job._handle
        with patch.object(job, 'active_processes', side_effect=OSError('query denied')):
            self.assertFalse(bridge.close())
        self.assertEqual(job._handle, handle)
        self.assertFalse(bridge.cleanup_complete)
        self.assertTrue(bridge.cleanup_retryable)
        self.assertFalse(bridge.descendant_cleanup_uncertain)
        with patch('local_app.bridge.CLI_EOF_GRACE', .05):
            self.assertTrue(bridge.close())
        self.assertIsNone(job._handle)

    def test_failed_termination_can_retry_concurrently_using_retained_job(self):
        bridge, job, process = self.tree()
        with patch('local_app.bridge.CLI_EOF_GRACE', .05):
            with patch.object(job, 'terminate', side_effect=OSError('temporary denial')):
                self.assertFalse(bridge.close())
            self.assertTrue(bridge.cleanup_retryable)
            self.assertIsNone(process.poll())
            results = []
            threads = [threading.Thread(target=lambda: results.append(bridge.close())) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(results, [True, True])
        self.assertIsNone(job._handle)

    def test_bridge_assigns_job_before_first_child_instruction(self):
        marker = self.root / 'ran'
        command = [sys.executable, '-B', '-c',
            'from pathlib import Path; import sys; Path("ran").touch(); sys.stdin.buffer.read()']
        bridge = ClaudeSession(command, {}, self.root, lambda *_: None)
        self.addCleanup(bridge.close)
        assign = WindowsJob.assign_and_resume
        def checked_assign(job, process):
            self.assertFalse(marker.exists())
            return assign(job, process)
        with patch.object(WindowsJob, 'assign_and_resume', checked_assign):
            bridge.start()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(marker.exists())
        self.assertTrue(bridge.close())
        self.assertTrue(bridge.cleanup_complete)

    def test_graceful_delayed_eof_preserves_natural_exit_without_forcing_job(self):
        bridge, job, process = self.own([sys.executable, '-B', '-c',
            'import sys,time; print("ready",flush=True); sys.stdin.buffer.read(); time.sleep(.65); sys.exit(23)'])
        self.assertEqual(process.stdout.readline().strip(), b'ready')
        with patch.object(job, 'terminate', side_effect=AssertionError('natural EOF needs no force')):
            self.assertTrue(bridge.close())
        self.assertEqual(process.returncode, 23)
        self.assertTrue(bridge.cleanup_complete)

    def test_nested_child_job_is_owned_by_outer_job(self):
        repository = str(Path(__file__).resolve().parents[1])
        child = (
            'import subprocess,sys,time; from pathlib import Path; '
            f'sys.path.insert(0,{repository!r}); '
            'from local_app.windows_job import WindowsJob,CREATE_SUSPENDED; '
            'job=WindowsJob(); '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
            'creationflags=subprocess.CREATE_NO_WINDOW|CREATE_SUSPENDED); '
            'job.assign_and_resume(p); Path("nested-ready").touch(); time.sleep(60)'
        )
        bridge, job, process = self.own([sys.executable, '-B', '-c', child])
        deadline = time.monotonic() + 5
        while not (self.root / 'nested-ready').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.root / 'nested-ready').exists())
        self.assertGreaterEqual(job.active_processes(), 2)
        with patch('local_app.bridge.CLI_EOF_GRACE', .05):
            self.assertTrue(bridge.close())
        self.assertIsNotNone(process.poll())
        self.assertEqual(job.active_processes(), 0)

    def test_assignment_denied_never_runs_child_and_reaps_exact_handle(self):
        job = WindowsJob()
        bridge = ClaudeSession([sys.executable, '-B', '-c',
            'from pathlib import Path; Path("must-not-run").touch()'], {}, self.root, lambda *_: None)
        self.addCleanup(bridge.close)
        with patch('local_app.bridge.WindowsJob', return_value=job), \
                patch.object(job._api, 'AssignProcessToJobObject', return_value=0):
            with self.assertRaises(BridgeError) as caught:
                bridge.start()
        self.assertEqual(caught.exception.code, 'cli_process_ownership_failed')
        self.assertFalse((self.root / 'must-not-run').exists())
        self.assertIsNotNone(bridge.process.poll())
        self.assertTrue(bridge.cleanup_complete)
        self.assertIsNone(job._handle)

    def test_resume_failure_keeps_assigned_child_suspended_until_job_cleanup(self):
        job = WindowsJob()
        bridge = ClaudeSession([sys.executable, '-B', '-c',
            'from pathlib import Path; Path("must-not-run").touch()'], {}, self.root, lambda *_: None)
        self.addCleanup(bridge.close)
        with patch('local_app.bridge.WindowsJob', return_value=job), \
                patch.object(job, '_resume_primary_thread', side_effect=OSError('ambiguous primary thread')), \
                patch('local_app.bridge.CLI_EOF_GRACE', .05):
            with self.assertRaises(BridgeError):
                bridge.start()
        self.assertFalse((self.root / 'must-not-run').exists())
        self.assertTrue(bridge.cleanup_complete)
        self.assertIsNone(job._handle)

    def test_first_send_reports_startup_failure_after_safe_suspended_cleanup(self):
        events = []
        job = WindowsJob()
        bridge = ClaudeSession([sys.executable, '-B', '-c',
            'from pathlib import Path; Path("must-not-run").touch()'], {}, self.root,
            lambda kind, data: events.append((kind, data)))
        self.addCleanup(bridge.close)
        with patch('local_app.bridge.WindowsJob', return_value=job), \
                patch.object(job._api, 'AssignProcessToJobObject', return_value=0):
            bridge._send_when_ready('must not submit')
        self.assertFalse((self.root / 'must-not-run').exists())
        self.assertTrue(bridge.cleanup_complete)
        errors = [data for kind, data in events if kind == 'error']
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['code'], 'cli_process_ownership_failed')

    def test_ambiguous_primary_thread_is_never_guessed_or_resumed(self):
        job = WindowsJob()
        self.addCleanup(job.release_empty)
        original_api = job._api
        api = Mock()
        api.CreateToolhelp32Snapshot.return_value = 77
        rows = iter([(11, 123), (12, 123)])
        def advance(_snapshot, pointer):
            try:
                tid, pid = next(rows)
            except StopIteration:
                ctypes.set_last_error(18)
                return 0
            entry = ctypes.cast(pointer, ctypes.POINTER(_ThreadEntry)).contents
            entry.dwSize = ctypes.sizeof(_ThreadEntry)
            entry.th32ThreadID, entry.th32OwnerProcessID = tid, pid
            return 1
        api.Thread32First.side_effect = api.Thread32Next.side_effect = advance
        try:
            job._api = api
            with self.assertRaises(OSError):
                job._resume_primary_thread(123)
            api.OpenThread.assert_not_called()
            api.ResumeThread.assert_not_called()
            api.CloseHandle.assert_called_once_with(77)
        finally:
            job._api = original_api

    def test_changed_thread_owner_or_unexpected_suspend_count_never_starts_cli(self):
        for function, value in [('GetProcessIdOfThread', 0), ('ResumeThread', 0), ('ResumeThread', 2)]:
            with self.subTest(function=function, value=value):
                job = WindowsJob()
                bridge = ClaudeSession([sys.executable, '-B', '-c',
                    'from pathlib import Path; Path("must-not-run").touch()'], {}, self.root, lambda *_: None)
                self.addCleanup(bridge.close)
                with patch('local_app.bridge.WindowsJob', return_value=job), \
                        patch.object(job._api, function, return_value=value), \
                        patch('local_app.bridge.CLI_EOF_GRACE', .05):
                    with self.assertRaises(BridgeError):
                        bridge.start()
                self.assertFalse((self.root / 'must-not-run').exists())
                self.assertTrue(bridge.cleanup_complete)
                self.assertIsNone(job._handle)

    def test_termination_failure_is_success_only_when_exact_job_is_already_empty(self):
        job = WindowsJob()
        self.addCleanup(job.release_empty)
        with patch.object(job._api, 'TerminateJobObject', return_value=0):
            job.terminate()
            with patch.object(job, 'active_processes', return_value=1):
                with self.assertRaises(OSError):
                    job.terminate()
        self.assertIsNotNone(job._handle)

    def test_empty_job_handle_close_failure_retains_handle_for_retry(self):
        job = WindowsJob()
        handle = job._handle
        self.addCleanup(job.release_empty)
        with patch.object(job._api, 'CloseHandle', return_value=0):
            with self.assertRaises(OSError):
                job.release_empty()
        self.assertEqual(job._handle, handle)
        job.release_empty()
        self.assertIsNone(job._handle)


if __name__ == '__main__':
    unittest.main()
