"""EXE migration fixtures: never change a real desktop or invoke UAC."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from local_app import managed_launcher as launcher
from local_app import app_updates as updates
from local_app import update_install as install
from tests.test_workspace_app_updates import Transport, release, VERSION
from tests.test_workspace_update_install import package


EXE = b'MZ' + b'verified release fixture' * 10


def exe_zip(version=VERSION, data=EXE, extra=None):
    buffer = io.BytesIO()
    name = f'Company-Workspace-{version}.exe'
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(name, data)
        archive.writestr(name + '.sha256', hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
        archive.writestr('README.txt', 'fixture')
        if extra:
            archive.writestr(*extra)
    return buffer.getvalue()


def add_exe(transport, data=None):
    data = exe_zip() if data is None else data
    base = f'{updates.RELEASE_ROOT}/download/v{VERSION}/'
    name = f'Company-Workspace-{VERSION}-exe.zip'
    transport.metadata['assets'].append({'name': name, 'size': len(data), 'state': 'uploaded',
        'digest': 'sha256:' + hashlib.sha256(data).hexdigest(), 'browser_download_url': base + name})
    transport.assets[base + name] = data
    sums_url = base + 'SHA256SUMS.txt'
    transport.assets[sums_url] += (hashlib.sha256(data).hexdigest() + '  ' + name + '\n').encode()
    sums = transport.metadata['assets'][1]
    sums['size'] = len(transport.assets[sums_url])
    sums['digest'] = 'sha256:' + hashlib.sha256(transport.assets[sums_url]).hexdigest()


class ManagedLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workspace-launcher-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.desktop = self.root / 'desktop'
        self.desktop.mkdir()
        self.link = self.desktop / 'Company Workspace (최신).lnk'
        self.native = self.enterContext(patch.object(launcher, '_shortcut_bytes', return_value=(self.link, b'L' * 80)))

    def test_old_entry_missing_or_malformed_is_display_only_unknown(self):
        for value in ({}, {'COMPANY_WORKSPACE_ENTRY_VERSION': '../evil'},
                      {'COMPANY_WORKSPACE_ENTRY_VERSION': '0.23.19', 'COMPANY_WORKSPACE_ENTRY_PROTOCOL': '1'}):
            details = launcher.entry_details('0.23.21', environment=value)
            self.assertTrue(details['repairRecommended'])
        modern = launcher.entry_details('0.23.21', environment={
            'COMPANY_WORKSPACE_ENTRY_VERSION': '0.23.21', 'COMPANY_WORKSPACE_ENTRY_PROTOCOL': '1'})
        self.assertTrue(modern['modernEntry'])
        self.native.assert_not_called()

    def test_fixed_zip_checksums_no_arbitrary_files(self):
        self.assertEqual(EXE, launcher.unpack_archive(exe_zip(), VERSION))
        for data in (exe_zip(extra=('../unexpected', 'a')), exe_zip(data=b'not-exe'), b'not-zip'):
            with self.subTest(data=data[:10]), self.assertRaises(ValueError):
                launcher.unpack_archive(data, VERSION)

    def test_stage_does_not_change_current_or_create_shortcut_until_confirmed(self):
        value = launcher.stage(self.state, VERSION, EXE, pending=True)
        self.assertEqual(EXE, (self.state / 'updates' / value['path']).read_bytes())
        self.assertFalse((self.state / 'updates/launcher.json').exists())
        self.native.assert_not_called()
        self.assertFalse(launcher.confirm_pending(self.state, '0.23.21'))
        self.assertTrue(launcher.confirm_pending(self.state, VERSION))
        self.assertFalse((self.state / 'updates/pending-launcher.json').exists())
        self.native.assert_not_called()

    def test_explicit_shortcut_preserves_state_and_has_no_role_switch_arguments(self):
        value = launcher.stage(self.state, VERSION, EXE)
        result = launcher.activate(self.state, value, create_shortcut=True)
        self.assertTrue(result['shortcut'])
        self.assertEqual(b'L' * 80, self.link.read_bytes())
        self.assertEqual(['--state', str(self.state), '--python', launcher.sys.executable], self.native.call_args.args[1])
        self.assertNotIn('execution', str(self.native.call_args))
        launcher.activate(self.state, value)  # Next verified update refreshes owned link.
        self.assertEqual(2, self.native.call_count)

    def test_unknown_or_user_modified_shortcut_is_not_replaced(self):
        value = launcher.stage(self.state, VERSION, EXE)
        self.link.write_bytes(b'personal shortcut')
        with self.assertRaises(ValueError):
            launcher.activate(self.state, value, create_shortcut=True)
        self.assertEqual(b'personal shortcut', self.link.read_bytes())
        self.assertFalse((self.state / 'updates/launcher.json').exists())

    def test_failed_shortcut_creation_does_not_promote_launcher(self):
        value = launcher.stage(self.state, VERSION, EXE)
        self.native.side_effect = OSError('fixture')
        with self.assertRaises(OSError):
            launcher.activate(self.state, value, create_shortcut=True)
        self.assertFalse(self.link.exists())
        self.assertFalse((self.state / 'updates/launcher.json').exists())

    def test_failed_promotion_rolls_back_previous_shortcut_and_metadata(self):
        old = launcher.stage(self.state, '0.21.0', EXE)
        launcher.activate(self.state, old, create_shortcut=True)
        before = (self.state / 'updates/launcher-shortcut.json').read_bytes()
        current = (self.state / 'updates/launcher.json').read_bytes()
        new = launcher.stage(self.state, VERSION, EXE + b'new')
        self.native.return_value = self.link, b'N' * 80
        real = install._atomic_manifest
        def fail(path, value):
            if path.name == 'launcher.json':
                raise OSError('fixture commit failure')
            return real(path, value)
        with patch.object(install, '_atomic_manifest', side_effect=fail), self.assertRaises(OSError):
            launcher.activate(self.state, new)
        self.assertEqual(b'L' * 80, self.link.read_bytes())
        self.assertEqual(before, (self.state / 'updates/launcher-shortcut.json').read_bytes())
        self.assertEqual(current, (self.state / 'updates/launcher.json').read_bytes())

    def test_first_failed_promotion_removes_only_owned_new_shortcut(self):
        value = launcher.stage(self.state, VERSION, EXE)
        with patch.object(install, '_atomic_manifest', side_effect=OSError('fixture')), self.assertRaises(OSError):
            launcher.activate(self.state, value, create_shortcut=True)
        self.assertFalse(self.link.exists())

    def test_modified_staged_exe_and_downgrade_are_rejected(self):
        value = launcher.stage(self.state, VERSION, EXE)
        launcher.activate(self.state, value)
        old = launcher.stage(self.state, '0.21.0', EXE)
        with self.assertRaises(ValueError):
            launcher.activate(self.state, old)
        (self.state / 'updates' / value['path']).write_bytes(EXE + b'changed')
        with self.assertRaises(ValueError):
            launcher.activate(self.state, value, create_shortcut=True)

    def test_cancelled_stage_and_activation_do_not_write(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ValueError):
            launcher.stage(self.state, VERSION, EXE, cancel=cancel)
        self.assertFalse(self.state.exists())
        value = launcher.stage(self.state, VERSION, EXE)
        with self.assertRaises(ValueError):
            launcher.activate(self.state, value, create_shortcut=True, cancel=cancel)
        self.assertFalse(self.link.exists())

    def test_new_exe_stage_only_promotes_after_matching_runtime_ready(self):
        raw = package()
        with patch.object(install.subprocess, 'Popen'), patch.object(install, 'powershell_path', return_value='powershell.exe'):
            install.stage_and_launch(self.state, '0.21.0', VERSION, raw, hashlib.sha256(raw).hexdigest(), launcher_bytes=EXE)
        self.assertFalse((self.state / 'updates/launcher.json').exists())
        application = self.state / f'updates/versions/{VERSION}/Company-Workspace'
        self.assertTrue(install.confirm_running_update(self.state, VERSION, application_root=application))
        self.assertTrue((self.state / 'updates/launcher.json').exists())

    def test_transient_owned_shortcut_failure_retries_after_next_verified_startup(self):
        old = launcher.stage(self.state, '0.21.0', EXE)
        launcher.activate(self.state, old, create_shortcut=True)
        raw = package()
        with patch.object(install.subprocess, 'Popen'), patch.object(install, 'powershell_path', return_value='powershell.exe'):
            install.stage_and_launch(self.state, '0.21.0', VERSION, raw, hashlib.sha256(raw).hexdigest(), launcher_bytes=EXE)
        application = self.state / f'updates/versions/{VERSION}/Company-Workspace'
        self.native.side_effect = OSError('temporary desktop failure')
        with self.assertRaises(ValueError):
            install.confirm_running_update(self.state, VERSION, application_root=application)
        self.assertTrue((self.state / 'updates/pending.json').exists())
        self.assertTrue((self.state / 'updates/pending-launcher.json').exists())
        self.assertEqual('0.21.0', launcher._read(self.state / 'updates/launcher.json')['version'])
        self.native.side_effect = None
        self.assertTrue(install.confirm_running_update(self.state, VERSION, application_root=application))
        self.assertFalse((self.state / 'updates/pending.json').exists())
        self.assertEqual(VERSION, launcher._read(self.state / 'updates/launcher.json')['version'])

    def test_shortcut_keeps_explicit_python_and_demo_state_without_changing_authority(self):
        state = self.state / 'demo'
        value = launcher.stage(state, VERSION, EXE)
        python = 'C:/Users/test name/Python 311/python.exe'
        with patch.object(launcher.sys, 'executable', python):
            launcher.activate(state, value, create_shortcut=True, demo=True)
        self.assertEqual(['--state', str(self.state), '--python', python, '--demo'], self.native.call_args.args[1])

    def test_same_version_cached_launcher_is_bound_to_its_download_source(self):
        value = launcher.stage(self.state, VERSION, EXE)
        launcher.activate(self.state, value)
        self.assertEqual(value, launcher.current_record(self.state, VERSION))
        self.assertIsNone(launcher.current_record(self.state, VERSION, source_identity='f' * 64))

    @unittest.skipUnless(os.name == 'nt', 'Windows COM shortcut serializer')
    def test_real_native_shortcut_serializer_is_hidden_finite_and_leaves_desktop_untouched(self):
        # Call the unpatched function captured below: only its unique temporary
        # file lives in this fixture's managed directory, never on the desktop.
        value = launcher.stage(self.state, VERSION, Path(launcher.sys.executable).read_bytes())
        target = self.state / 'updates' / value['path']
        try:
            destination, data = REAL_SHORTCUT_BYTES(target, ['--state', str(self.state), '--python', launcher.sys.executable])
        except launcher.subprocess.CalledProcessError as exc:
            self.fail((exc.stderr or '')[:1500])
        self.assertEqual('AX Workspace (최신).lnk', destination.name)
        self.assertGreater(len(data), 76)
        self.assertEqual([], list(target.parent.glob('.workspace-*.lnk')))


class ManagedLauncherUpdateTests(ManagedLauncherTests):
    def manager(self, transport, current=VERSION):
        manager = updates.UpdateManager(self.state, current, transport=transport, installer=Mock())
        self.addCleanup(manager.close)
        return manager

    def finish(self, manager):
        worker = manager._thread
        if worker:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        return manager.snapshot()

    def test_old_vbs_updated_app_can_fetch_same_version_exe_and_create_modern_entry(self):
        transport = Transport()
        add_exe(transport)
        manager = self.manager(transport)
        self.assertEqual([], transport.calls)
        manager.prepare_launcher()
        result = self.finish(manager)
        self.assertEqual('ready', result['launcher']['status'])
        self.assertTrue(self.link.exists())
        manager.installer.assert_not_called()
        self.assertEqual(3, len(transport.calls))

    def test_mismatched_release_or_missing_exe_preserves_old_app(self):
        for transport in (Transport('0.23.21'), Transport()):
            manager = self.manager(transport)
            manager.prepare_launcher()
            self.assertEqual('error', self.finish(manager)['launcher']['status'])
            manager.installer.assert_not_called()
            self.assertFalse(self.link.exists())

    def test_future_update_passes_verified_exe_without_launching_it_early(self):
        transport = Transport()
        add_exe(transport)
        manager = self.manager(transport, current='0.21.0')
        manager.check(manual=True)
        self.finish(manager)
        manager.install(VERSION)
        self.finish(manager)
        self.assertEqual(EXE, manager.installer.call_args.kwargs['launcher_bytes'])
        self.native.assert_not_called()

    def test_repair_is_single_flight_and_cancellation_stops_before_shortcut(self):
        entered, release_event = threading.Event(), threading.Event()
        transport = Transport()
        add_exe(transport)
        def blocked(url, **kwargs):
            entered.set()
            release_event.wait(2)
            return transport(url, **kwargs)
        manager = self.manager(blocked)
        manager.prepare_launcher()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(ValueError):
            manager.prepare_launcher()
        manager.close()
        release_event.set()
        self.finish(manager)
        self.native.assert_not_called()

    def test_corrupt_exe_checksum_fails_before_installer_or_shortcut(self):
        transport = Transport()
        add_exe(transport)
        url = transport.metadata['assets'][-1]['browser_download_url']
        transport.assets[url] += b'changed'
        manager = self.manager(transport)
        manager.prepare_launcher()
        self.assertEqual('error', self.finish(manager)['launcher']['status'])
        manager.installer.assert_not_called()
        self.native.assert_not_called()

    def test_verified_cached_same_source_launcher_shortcut_can_be_created_offline(self):
        value = launcher.stage(self.state, VERSION, EXE)
        launcher.activate(self.state, value)
        transport = Mock(side_effect=OSError('offline'))
        manager = self.manager(transport)
        manager.prepare_launcher()
        self.assertEqual('ready', self.finish(manager)['launcher']['status'])
        transport.assert_not_called()


REAL_SHORTCUT_BYTES = launcher._shortcut_bytes
