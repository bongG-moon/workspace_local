"""Short helpers own their children; fixtures never touch installed Claude."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_app.bridge import probe_cli
from local_app.harness_client import HarnessClient
from local_app.owned_process import CancelledError, OutputLimitExceeded, run_owned
from local_app.windows_job import WindowsJob
from local_app.windows_process import powershell_path


FIXTURE = r'''
import json, subprocess, sys, time
from pathlib import Path
mode, marker, shell = sys.argv[1:4]
child = subprocess.Popen([shell, '-NoLogo', '-NoProfile', '-NonInteractive',
    '-Command', 'Start-Sleep -Seconds 60'], stdin=subprocess.DEVNULL,
    stdout=None, stderr=None,
    creationflags=subprocess.CREATE_NO_WINDOW)
Path(marker).write_text(str(child.pid))
if mode == 'timeout':
    time.sleep(60)
elif mode == 'error':
    sys.exit(23)
elif mode == 'call':
    print(json.dumps({'request': json.loads(sys.stdin.buffer.read())}, ensure_ascii=False))
elif mode == 'error_json':
    print(json.dumps({'error': 'fixture request rejected'}), file=sys.stderr)
    sys.exit(23)
else:
    print('test-cli 1.0' if sys.argv[-1] == '--version' else
          '--input-format --output-format --permission-prompt-tool', flush=True)
'''


@unittest.skipUnless(os.name == 'nt', 'Windows helper process ownership')
class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='workspace-helper-')
        def remove_fixture():
            # Windows can release a killed descendant's cwd just after the
            # kernel reports an empty job. Bound that filesystem-only wait.
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
        self.script = self.root / 'fixture.py'
        self.script.write_text(FIXTURE, encoding='utf-8')
        self.jobs = []

    def command(self, mode='success'):
        return [sys.executable, '-X', 'utf8', '-B', str(self.script), mode,
                str(self.root / 'child-pid'), powershell_path()]

    def job(self):
        job = WindowsJob()
        self.jobs.append(job)
        # Always close the exact fixture ownership handle if an assertion fails.
        self.addCleanup(job.close_handle)
        return job

    def assert_released(self):
        self.assertTrue(self.jobs)
        self.assertTrue(all(job._handle is None for job in self.jobs))

    def test_successful_probe_reaps_profile_children_after_wrapper_exits(self):
        active_before_close = []
        original_terminate = WindowsJob.terminate
        def record_terminate(job):
            active_before_close.append(job.active_processes())
            return original_terminate(job)
        unrelated = subprocess.Popen([powershell_path(), '-NoProfile', '-NonInteractive',
            '-Command', 'Start-Sleep -Seconds 60'], creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: (unrelated.kill(), unrelated.wait(timeout=5)))
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job), \
                patch.object(WindowsJob, 'terminate', record_terminate):
            info = probe_cli(self.command())
        self.assertEqual(info['version'], 'test-cli 1.0')
        self.assertEqual(len(self.jobs), 2)
        self.assertTrue(all(count >= 1 for count in active_before_close))
        self.assertIsNone(unrelated.poll())
        self.assert_released()

    def test_timeout_terminates_wrapper_and_background_powershell(self):
        active_before_close = []
        original_terminate = WindowsJob.terminate
        def record_terminate(job):
            active_before_close.append(job.active_processes())
            return original_terminate(job)
        started = time.monotonic()
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job), \
                patch.object(WindowsJob, 'terminate', record_terminate):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned(self.command('timeout'), timeout=1, capture_output=True)
        self.assertTrue((self.root / 'child-pid').exists())
        self.assertGreaterEqual(active_before_close[0], 2)
        self.assertLess(time.monotonic() - started, 5)
        self.assert_released()

    def test_inherited_output_pipe_cannot_keep_finished_probe_alive(self):
        # Reproduce the previous run(timeout=...) behavior inside another owned
        # fixture job. Its wrapper exits, but its PowerShell child inherits the
        # output pipe. Windows communicate() then waits past its own timeout.
        previous = [sys.executable, '-c',
            f'import subprocess; subprocess.run({self.command()!r}, capture_output=True, timeout=1)']
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            with self.assertRaises(subprocess.TimeoutExpired):
                run_owned(previous, timeout=3, capture_output=True)
            started = time.monotonic()
            result = run_owned(self.command(), timeout=3, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--input-format', result.stdout)
        self.assertLess(time.monotonic() - started, 3)
        self.assert_released()

    def test_cancel_reaps_owned_tree_and_does_not_wait_for_helper_timeout(self):
        cancelled = threading.Event()
        def cancel_after_child():
            deadline = time.monotonic() + 5
            while not (self.root / 'child-pid').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            cancelled.set()
        cancel_thread = threading.Thread(target=cancel_after_child)
        cancel_thread.start()
        self.addCleanup(lambda: cancel_thread.join(5))
        started = time.monotonic()
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            with self.assertRaises(CancelledError):
                run_owned(self.command('timeout'), timeout=300, cancel_event=cancelled, capture_output=True)
        self.assertLess(time.monotonic() - started, 5)
        self.assert_released()

    def test_cancelled_before_launch_does_not_create_process_or_job(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch('local_app.owned_process.WindowsJob') as job, \
                patch('local_app.owned_process.subprocess.Popen') as popen:
            with self.assertRaises(CancelledError):
                run_owned(self.command(), timeout=1, cancel_event=cancelled)
        job.assert_not_called()
        popen.assert_not_called()

    def test_assignment_failure_does_not_run_the_unowned_child(self):
        marker = self.root / 'must-not-run'
        command = [sys.executable, '-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).touch()', str(marker)]
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job), \
                patch.object(WindowsJob, 'assign_and_resume', side_effect=OSError('assignment denied')):
            with self.assertRaises(OSError):
                run_owned(command, timeout=2)
        self.assertFalse(marker.exists())
        self.assert_released()

    def test_cleanup_failure_closes_job_handle_as_final_owned_tree_fallback(self):
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job), \
                patch.object(WindowsJob, 'terminate', side_effect=OSError('termination denied')):
            with self.assertRaises(OSError):
                run_owned(self.command(), timeout=2)
        self.assert_released()

    def test_capture_limit_stops_noisy_helper_without_unbounded_in_memory_output(self):
        command = [sys.executable, '-c',
            'import sys,time; sys.stdout.buffer.write(b"x"*131072); sys.stdout.flush(); time.sleep(60)']
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            with self.assertRaises(OutputLimitExceeded):
                run_owned(command, timeout=10, capture_output=True, output_limit=65536)
        self.assert_released()

    def test_check_preserves_child_exit_code_after_descendants_are_reaped(self):
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                run_owned(self.command('error'), timeout=3, check=True)
        self.assertEqual(error.exception.returncode, 23)
        self.assert_released()

    def test_text_input_and_unicode_capture_without_hidden_interactive_stdin(self):
        command = [sys.executable, '-X', 'utf8', '-c',
            'import sys; print(sys.stdin.read()); print("오류",file=sys.stderr)']
        result = run_owned(command, timeout=3, capture_output=True, input='한글 자료', encoding='utf-8')
        self.assertEqual(result.stdout.strip(), '한글 자료')
        self.assertEqual(result.stderr.strip(), '오류')

    def test_repeated_probes_do_not_accumulate_job_handles_or_reader_threads(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessHandleCount.restype = wintypes.BOOL
        def handle_count():
            count = wintypes.DWORD()
            self.assertTrue(kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count)))
            return count.value
        baseline_handles, baseline_threads = handle_count(), threading.active_count()
        with patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            for _ in range(10):
                self.assertEqual(probe_cli(self.command())['version'], 'test-cli 1.0')
        self.assertEqual(len(self.jobs), 20)
        self.assert_released()
        self.assertLessEqual(handle_count(), baseline_handles + 2)
        self.assertEqual(threading.active_count(), baseline_threads)

    def test_registered_helper_retains_json_input_protocol_and_reaps_children(self):
        client = HarnessClient(config=self.root / 'config', registrations=self.root / 'installations')
        request = {'operation': 'snapshot', 'title': '한글 자료'}
        with patch.object(client, 'locate', return_value=self.command('call')), \
                patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            self.assertEqual(client.call(self.root, request), {'request': request})
        self.assert_released()

    def test_registered_helper_retains_typed_failure_without_retry(self):
        client = HarnessClient(config=self.root / 'config', registrations=self.root / 'installations')
        with patch.object(client, 'locate', return_value=self.command('error_json')) as locate, \
                patch('local_app.owned_process.WindowsJob', side_effect=self.job):
            with self.assertRaisesRegex(ValueError, 'fixture request rejected'):
                client.call(self.root, {'operation': 'snapshot'})
        self.assertEqual(locate.call_count, 1)
        self.assert_released()


if __name__ == '__main__':
    unittest.main()
