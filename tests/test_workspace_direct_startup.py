from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from local_app.startup import verify_process


class DirectStartupTests(unittest.TestCase):
    @patch('local_app.startup.os.name', 'nt')
    def test_direct_start_uses_the_same_guard_without_environment_overrides(self):
        runner = Mock(return_value=Mock(returncode=0))
        verify_process(runner=runner)
        args, kwargs = runner.call_args
        script = args[0][-1]
        self.assertTrue(Path(args[0][0]).is_absolute())
        self.assertEqual(Path(args[0][0]).name.lower(), 'powershell.exe')
        self.assertIn('Get-WorkspaceVerifiedContext', script)
        self.assertIn('Assert-WorkspaceNormalProcess', script)
        self.assertNotIn('env', kwargs)
        self.assertEqual('Bypass', args[0][args[0].index('-ExecutionPolicy') + 1])
        self.assertNotIn('Set-ExecutionPolicy', script)
        self.assertNotIn('RunAs', script)

    @patch('local_app.startup.os.name', 'nt')
    def test_failed_guard_never_exposes_child_output_or_starts_without_verification(self):
        runner = Mock(return_value=Mock(returncode=33, stdout=b'private', stderr=b'secret'))
        with self.assertRaisesRegex(RuntimeError, 'WS-33') as error:
            verify_process(runner=runner)
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('private', str(error.exception))

    @patch('local_app.startup.os.name', 'nt')
    def test_timeout_has_no_unguarded_retry(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired('powershell', 30))
        with self.assertRaisesRegex(RuntimeError, '실행 환경'):
            verify_process(runner=runner)
        self.assertEqual(1, runner.call_count)

    def test_main_calls_guard_before_local_app(self):
        source = (Path(__file__).resolve().parents[1] / 'local_app/server.py').read_text(encoding='utf-8')
        main = source.split('def main():', 1)[1]
        self.assertLess(main.index('verify_process()'), main.index('LocalApp('))
