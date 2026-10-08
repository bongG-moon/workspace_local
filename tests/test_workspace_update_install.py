"""Verified persistent updates; fixtures never start the real app or Claude."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import warnings
import zipfile

from local_app import update_install as install

ROOT = Path(__file__).resolve().parents[1]
REAL_POPEN = subprocess.Popen


def package(entries=None):
    raw = io.BytesIO()
    if entries is None:
        entries = [('Company-Workspace/deploy/Start-CompanyWorkspace.ps1', b'# fixture'),
                   ('Company-Workspace/local_app/server.py', b'# fixture'),
                   ('Company-Workspace/local_app/__init__.py', b'')]
    with warnings.catch_warnings(), zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter('ignore', UserWarning)
        for name, data in entries:
            archive.writestr(name, data)
    return raw.getvalue()


class UpdateInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace update & ')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        self.raw = package()
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.popen = self.enterContext(patch.object(install.subprocess, 'Popen'))
        self.enterContext(patch.object(install, 'powershell_path', return_value='powershell.exe'))

    def stage(self, version='0.23.0', raw=None, **kwargs):
        raw = self.raw if raw is None else raw
        return install.stage_and_launch(self.state, '0.22.0', version, raw, hashlib.sha256(raw).hexdigest(), **kwargs)

    def manifest(self, name='pending'):
        return json.loads((self.state / f'updates/{name}.json').read_text())

    def confirm(self, version):
        application = self.state / 'updates/versions' / version / 'Company-Workspace'
        return install.confirm_running_update(self.state, version, application_root=application)

    def test_pending_then_verified_ready_promotes_and_preserves_launch_options(self):
        result = self.stage(no_browser=True)
        self.assertEqual('launching', result['status'])
        self.assertIs(self.popen.return_value, result['_process'])
        self.assertFalse((self.state / 'updates/current.json').exists())
        expected = self.manifest()
        self.assertEqual('versions/0.23.0/Company-Workspace', expected['root'])
        args, options = self.popen.call_args
        self.assertIn('-NoBrowser', args[0])
        self.assertIn('-StateRoot', args[0])
        self.assertEqual(str(self.state), args[0][args[0].index('-StateRoot') + 1])
        self.assertFalse(options['shell'])
        self.assertEqual('1', options['env']['PYTHONDONTWRITEBYTECODE'])
        self.assertTrue(self.confirm('0.23.0'))
        self.assertEqual(expected, self.manifest('current'))
        self.assertFalse((self.state / 'updates/pending.json').exists())

    def test_demo_uses_original_launcher_state(self):
        self.state = self.state / 'demo'
        self.stage(demo=True)
        args = self.popen.call_args.args[0]
        self.assertIn('-Demo', args)
        self.assertEqual(str(self.state.parent), args[args.index('-StateRoot') + 1])

    def test_hash_mismatch_never_writes_or_launches(self):
        with self.assertRaises(ValueError):
            install.stage_and_launch(self.state, '0.22.0', '0.23.0', self.raw, '0' * 64)
        self.assertFalse(self.state.exists())
        self.popen.assert_not_called()

    def test_bad_versions_never_write(self):
        for version in ('../0.23.0', '0.23.0-beta', '00.23.0', '0.22.0', '0.21.0', '0.23.0\n'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.stage(version)
        self.popen.assert_not_called()

    def test_traversal_and_windows_special_paths_rejected(self):
        for name in ('Company-Workspace/../outside', 'other/a', 'Company-Workspace/C:/a',
                     'Company-Workspace/deploy\\bad.ps1', 'Company-Workspace/runtime/runtime.json',
                     'Company-Workspace/NUL.txt', 'Company-Workspace/a. /x'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.stage(raw=package([(name, b'x')]))
        self.popen.assert_not_called()

    def test_duplicate_casefold_and_zip_symlink_rejected(self):
        for names in (['Company-Workspace/a', 'Company-Workspace/a'], ['Company-Workspace/a', 'Company-Workspace/A']):
            with self.assertRaises(ValueError):
                self.stage(raw=package([(name, b'x') for name in names]))
        link = zipfile.ZipInfo('Company-Workspace/link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            self.stage(raw=package([(link, b'../outside')]))

    def test_windows_compress_archive_separators_are_normalized_safely(self):
        raw = package([('Company-Workspace\\deploy\\Start-CompanyWorkspace.ps1', b'# fixture'),
                       ('Company-Workspace\\local_app\\server.py', b'# fixture')])
        self.stage(raw=raw)
        self.assertIn('deploy/Start-CompanyWorkspace.ps1', self.manifest()['files'])
        with self.assertRaises(ValueError):
            self.stage('0.24.0', raw=package([('Company-Workspace\\..\\outside', b'x')]))
        with self.assertRaises(ValueError):
            self.stage('0.24.0', raw=package([('Company-Workspace\\a', b'x'), ('Company-Workspace/a', b'x')]))

    def test_archive_and_expanded_size_are_bounded(self):
        with patch.object(install, 'MAX_FILES', 1), self.assertRaises(ValueError):
            self.stage()
        highly_compressed = package([('Company-Workspace/huge', b'0' * 10000)])
        with patch.object(install, 'MAX_PACKAGE_BYTES', 1000), self.assertRaises(ValueError):
            self.stage(raw=highly_compressed)
        self.popen.assert_not_called()

    def test_failed_startup_keeps_previous_successful_version(self):
        self.stage()
        self.assertTrue(self.confirm('0.23.0'))
        old = self.manifest('current')
        self.stage('0.24.0')
        application = self.state / 'updates' / self.manifest()['root']
        (application / 'local_app/server.py').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.confirm('0.24.0')
        self.assertEqual(old, self.manifest('current'))

    def test_confirmation_write_failure_warns_without_replacing_previous_pointer(self):
        self.assertFalse(self.confirm('0.22.0'))
        self.stage()
        self.assertTrue(self.confirm('0.23.0'))
        old = self.manifest('current')
        self.stage('0.24.0')
        with patch.object(install, '_atomic_manifest', side_effect=OSError('fixture write failure')):
            with self.assertRaisesRegex(ValueError, '기존 실행 파일은 유지됩니다'):
                self.confirm('0.24.0')
        self.assertEqual(old, self.manifest('current'))

    def test_existing_immutable_version_is_not_overwritten(self):
        self.stage()
        path = self.state / 'updates/versions/0.23.0/Company-Workspace/local_app/server.py'
        path.write_bytes(b'changed')
        self.popen.reset_mock()
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(b'changed', path.read_bytes())
        self.popen.assert_not_called()

    def test_transient_windows_promotion_lock_retries_same_stage_and_launches_once(self):
        error = PermissionError(13, 'temporary Windows scanner lock')
        error.winerror = 5
        original = os.rename
        calls = []
        def rename(source, target):
            calls.append((source, target))
            if len(calls) <= 2: raise error
            return original(source, target)
        with patch.object(install.os, 'rename', side_effect=rename), patch.object(install.time, 'sleep') as sleep:
            self.stage()
        self.assertEqual(3, len(calls))
        self.assertEqual(1, len(set(calls)))
        self.assertEqual([.01, .025], [call.args[0] for call in sleep.call_args_list])
        self.assertEqual(1, self.popen.call_count)
        self.assertEqual('0.23.0', self.manifest()['version'])
        self.assertFalse(list((self.state/'updates/versions').glob('.stage-*')))

    def test_permanent_promotion_denial_preserves_current_install_and_never_launches(self):
        self.stage()
        self.assertTrue(self.confirm('0.23.0'))
        pointer = self.state/'updates/current.json'
        old_pointer = pointer.read_bytes()
        old_body = self.state/'updates/versions/0.23.0/Company-Workspace/local_app/server.py'
        original = old_body.read_bytes()
        self.popen.reset_mock()
        error = PermissionError(13, 'denied')
        error.winerror = 32
        with patch.object(install.os, 'rename', side_effect=error) as rename, patch.object(install.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError): self.stage('0.24.0')
        self.assertEqual(4, rename.call_count)
        self.assertAlmostEqual(.085, sum(call.args[0] for call in sleep.call_args_list))
        self.assertEqual(old_pointer, pointer.read_bytes())
        self.assertEqual(original, old_body.read_bytes())
        self.assertFalse((self.state/'updates/pending.json').exists())
        self.assertFalse((self.state/'updates/versions/0.24.0').exists())
        self.assertFalse(list((self.state/'updates/versions').glob('.stage-*')))
        self.popen.assert_not_called()

    def test_competing_version_during_failed_rename_is_preserved_and_verified(self):
        error = PermissionError(13, 'another install won')
        error.winerror = 5
        def compete(source, target):
            target.mkdir()
            (target/'untouched.txt').write_bytes(b'competing install')
            raise error
        with patch.object(install.os, 'rename', side_effect=compete) as rename, patch.object(install.time, 'sleep') as sleep:
            with self.assertRaises((ValueError, OSError)): self.stage()
        self.assertEqual(1, rename.call_count)
        sleep.assert_not_called()
        self.assertEqual(b'competing install', (self.state/'updates/versions/0.23.0/untouched.txt').read_bytes())
        self.assertFalse((self.state/'updates/pending.json').exists())
        self.popen.assert_not_called()

    def test_promotion_retry_revalidates_path_and_honors_cancellation(self):
        target = self.state/'updates/versions/0.23.0'
        original_safe = install._safe
        changed = False
        error = PermissionError(13, 'temporary lock')
        error.winerror = 33
        def sleep(_):
            nonlocal changed
            changed = True
        def safe(path):
            if changed and Path(path) == target: raise ValueError('fixture changed destination')
            return original_safe(path)
        with patch.object(install.os, 'rename', side_effect=error) as rename, patch.object(install.time, 'sleep', side_effect=sleep), patch.object(install, '_safe', side_effect=safe):
            with self.assertRaisesRegex(ValueError, 'changed destination'): self.stage()
        self.assertEqual(1, rename.call_count)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob('.stage-*')))
        cancel = threading.Event()
        with patch.object(install.os, 'rename', side_effect=error) as rename, patch.object(install.time, 'sleep', side_effect=lambda _: cancel.set()):
            with self.assertRaises(ValueError): self.stage(cancel=cancel)
        self.assertEqual(1, rename.call_count)
        self.assertFalse(target.exists())
        self.popen.assert_not_called()

    def test_non_windows_promotion_failure_has_no_retry_or_delay(self):
        with patch.object(install.os, 'rename', side_effect=OSError('disk failure')) as rename, patch.object(install.time, 'sleep') as sleep:
            with self.assertRaises(OSError): self.stage()
        self.assertEqual(1, rename.call_count)
        sleep.assert_not_called()
        self.popen.assert_not_called()

    def test_confirm_wrong_version_hash_or_unlisted_file_keeps_old_pointer(self):
        self.stage()
        self.assertFalse(self.confirm('0.24.0'))
        application = self.state / 'updates' / self.manifest()['root']
        added = application / 'unexpected.py'
        added.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.confirm('0.23.0')
        added.unlink()
        (application / 'local_app/server.py').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.confirm('0.23.0')
        self.assertFalse((self.state / 'updates/current.json').exists())

    def test_failed_spawn_preserves_successful_pointer(self):
        self.stage()
        self.assertTrue(self.confirm('0.23.0'))
        old = self.manifest('current')
        self.popen.side_effect = OSError('fixture launch failure')
        with self.assertRaises(OSError):
            self.stage('0.24.0')
        self.assertEqual(old, self.manifest('current'))
        self.assertFalse((self.state / 'updates/pending.json').exists())

    def test_cancel_at_entry_and_after_pending_cleans_up(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ValueError):
            self.stage(cancel=cancel)
        cancel.clear()
        original = install._atomic_manifest
        def writing(path, value):
            original(path, value)
            cancel.set()
        with patch.object(install, '_atomic_manifest', side_effect=writing), self.assertRaises(ValueError):
            self.stage(cancel=cancel)
        self.assertFalse((self.state / 'updates/pending.json').exists())
        self.popen.assert_not_called()

    def test_manifest_traversal_unknown_keys_and_duplicate_fields_rejected(self):
        self.stage()
        value = self.manifest()
        path = self.state / 'updates/pending.json'
        for mutation in ({**value, 'root': '../outside'}, {**value, 'command': 'calc.exe'}, {**value, 'schema': True}):
            path.write_text(json.dumps(mutation))
            with self.assertRaises(ValueError):
                self.confirm('0.23.0')
        path.write_text(json.dumps(value)[:-1] + ',"version":"0.23.0"}')
        with self.assertRaises(ValueError):
            self.confirm('0.23.0')

    def test_same_version_from_another_root_does_not_promote_staged_app(self):
        self.stage()
        self.assertFalse(install.confirm_running_update(self.state, '0.23.0', application_root=ROOT))
        self.assertFalse(install.confirm_running_update(self.state, '0.23.0'))
        self.assertFalse((self.state / 'updates/current.json').exists())
        self.assertTrue((self.state / 'updates/pending.json').exists())
        self.assertTrue(self.confirm('0.23.0'))

    def test_symlink_or_junction_state_is_rejected(self):
        destination = Path(self.temp.name) / 'outside'
        destination.mkdir()
        try:
            self.state.symlink_to(destination, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                self.skipTest('Symlink unavailable')
            with patch.object(subprocess, 'Popen', REAL_POPEN):
                result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(self.state), str(destination)],
                                        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                self.skipTest('Junction unavailable')
        self.addCleanup(lambda: self.state.unlink() if self.state.is_symlink() else self.state.rmdir())
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual([], list(destination.iterdir()))


@unittest.skipUnless(os.name == 'nt', 'PowerShell launcher required')
class ManagedLauncherTests(unittest.TestCase):
    def test_actual_hidden_stage_launch_executes_script_before_successful_exit(self):
        # Exit zero alone missed a Windows PowerShell failure with conflicting
        # detached/no-console flags. Require evidence that the script ran.
        with tempfile.TemporaryDirectory(prefix='workspace staged launch & ') as temporary:
            state = Path(temporary) / 'state'
            launcher = ('param([string]$StateRoot,[string]$PythonCommand,[switch]$Demo,[switch]$NoBrowser)\n'
                        '[IO.File]::WriteAllText([IO.Path]::Combine($StateRoot,"executed.txt"),"completed")\n')
            raw = package([('Company-Workspace/deploy/Start-CompanyWorkspace.ps1', launcher.encode()),
                           ('Company-Workspace/local_app/server.py', b'# fixture')])
            result = install.stage_and_launch(state, '0.22.0', '0.23.0', raw,
                                              hashlib.sha256(raw).hexdigest(), no_browser=True)
            self.assertEqual(0, result['_process'].wait(timeout=15))
            self.assertEqual('completed', (state / 'executed.txt').read_text())

    def test_real_forwarder_reopens_verified_update_and_ignores_invalid_pointer(self):
        # Run just the actual forwarding function, never the application launcher.
        with tempfile.TemporaryDirectory(prefix='workspace reopen & ') as temporary:
            root = Path(temporary)
            state = root / 'state'
            report = root / 'opened.json'
            launcher = ('param([string]$StateRoot,[string]$PythonCommand,[switch]$Demo,[switch]$NoBrowser)\n'
                        '@{state=$StateRoot;python=$PythonCommand;demo=[bool]$Demo;headless=[bool]$NoBrowser} | '
                        'ConvertTo-Json -Compress | Set-Content -LiteralPath $env:UPDATE_FIXTURE_REPORT -Encoding UTF8\n')
            raw = package([('Company-Workspace/deploy/Start-CompanyWorkspace.ps1', launcher.encode()),
                           ('Company-Workspace/local_app/server.py', b'# fixture')])
            with patch.object(install.subprocess, 'Popen'):
                install.stage_and_launch(state, '0.22.0', '0.23.0', raw, hashlib.sha256(raw).hexdigest())
            self.assertTrue(install.confirm_running_update(state, '0.23.0',
                application_root=state / 'updates/versions/0.23.0/Company-Workspace'))
            source = (ROOT / 'deploy/Start-CompanyWorkspace.ps1').read_text(encoding='utf-8-sig')
            function = source.split('function Invoke-WorkspaceManagedUpdate {', 1)[1].split('\ntry {\n    $startupHelper', 1)[0]
            function = '\n'.join(line.replace('return $false', f'[Console]::Error.WriteLine("guard {number}: " + $_); return $false') for number, line in enumerate(function.splitlines(), 1))
            script = root / 'forward.ps1'
            script.write_text('param([string]$State,[string]$Embedded)\nfunction Invoke-WorkspaceManagedUpdate {' + function
                              + '\nInvoke-WorkspaceManagedUpdate -State $State -CurrentVersion $Embedded -Python "C:\\custom python.exe" -Headless $true\n', encoding='utf-8-sig')
            env = os.environ.copy()
            env['UPDATE_FIXTURE_REPORT'] = str(report)
            command = [install.powershell_path(), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-State', str(state)]
            def run(version):
                return subprocess.run([*command, '-Embedded', version], capture_output=True, env=env, timeout=15,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            result = run('0.22.0')
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(b'True', result.stdout, result.stderr)
            for _ in range(40):
                if report.exists():
                    break
                time.sleep(.05)
            value = json.loads(report.read_text(encoding='utf-8-sig'))
            self.assertEqual(str(state), value['state'])
            self.assertEqual('C:\\custom python.exe', value['python'])
            self.assertTrue(value['headless'])
            self.assertIn(b'False', run('0.23.0').stdout)  # No forwarding loop.
            payload = state / 'updates/versions/0.23.0/Company-Workspace/local_app/server.py'
            payload.write_text('changed')
            self.assertIn(b'False', run('0.22.0').stdout)
