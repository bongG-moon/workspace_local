"""Opt-in real Windows installer lifecycle, isolated from the user's AX install.

WORKSPACE_NSIS_TEST_COMPILER = approved makensis.exe
WORKSPACE_NSIS_TEST_STANDALONE = built, version-matching standalone EXE
Only the uniquely named test registration/links are created and removed.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMPILER = os.environ.get('WORKSPACE_NSIS_TEST_COMPILER')
STANDALONE = os.environ.get('WORKSPACE_NSIS_TEST_STANDALONE')
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


@unittest.skipUnless(os.name == 'nt' and COMPILER and STANDALONE, 'Opt-in NSIS compiler and EXE required')
class NsisInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import winreg
        cls.winreg = winreg
        cls.root = Path(tempfile.mkdtemp(prefix='nsis-qa-', dir=ROOT / 'build'))
        cls.identity = 'qa-' + uuid.uuid4().hex[:12]
        cls.product = 'AX.Workspace.Desktop.Test.' + cls.identity
        cls.name = 'AX Workspace Installer Test ' + cls.identity
        cls.regpath = r'Software\Microsoft\Windows\CurrentVersion\Uninstall' + chr(92) + cls.product
        cls.ps = str(Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        cls.run_checked([cls.ps, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                         str(ROOT / 'deploy/New-WorkspaceInstaller.ps1'), '-StandaloneExe', STANDALONE,
                         '-OutputDirectory', str(cls.root / 'output'), '-NsisCompiler', COMPILER,
                         '-TestIdentity', cls.identity], timeout=120)
        cls.setup = next((cls.root / 'output').glob('*.exe'))
        cls.installed = cls.root / 'installed 앱'
        cls.original_hash = hashlib.sha256(Path(STANDALONE).read_bytes()).hexdigest()
        cls.install()

    @classmethod
    def run_checked(cls, args, timeout=60):
        env = os.environ.copy()
        # Do not pass PowerShell 7's private module path to Windows PowerShell 5.
        env.pop('PSModulePath', None)
        capture = not isinstance(args, str)  # NSIS GUI child processes must not retain test pipes.
        result = subprocess.run(args, stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
                                timeout=timeout, creationflags=HIDDEN, env=env)
        if result.returncode:
            raise AssertionError(f'Exit {result.returncode}: {(result.stdout or b"")[-3000:]!r} {(result.stderr or b"")[-3000:]!r}')
        return result

    @classmethod
    def install(cls):
        cls.run_checked(cls.command(cls.setup, '/D=', cls.installed))

    @staticmethod
    def command(exe, option, directory):
        # NSIS consumes the final directory option verbatim, including spaces;
        # Python's normal list quoting would hide /D from its command parser.
        return subprocess.list2cmdline([str(exe), '/S']) + ' ' + option + str(directory)

    @classmethod
    def read_registry(cls, key):
        with cls.winreg.OpenKey(cls.winreg.HKEY_CURRENT_USER, cls.regpath, 0,
                               cls.winreg.KEY_READ | cls.winreg.KEY_WOW64_64KEY) as handle:
            return cls.winreg.QueryValueEx(handle, key)[0]

    @classmethod
    def tearDownClass(cls):
        remover = cls.installed / 'Uninstall.exe'
        if remover.exists():
            cls.uninstall()
            assert not (cls.installed / 'AX-Workspace.exe').exists()
            assert not remover.exists()
            assert not (cls.installed / 'AX-Workspace.install-id').exists()
            with cls._assert_registry_gone():
                cls.read_registry('DisplayName')

    @classmethod
    def uninstall(cls):
        # Exercise Windows Apps' actual entry point. NSIS may relaunch its
        # temporary uninstaller, so verify completion beyond the first exit.
        cls.run_checked(subprocess.list2cmdline([str(cls.installed / 'Uninstall.exe'), '/S']))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                cls.read_registry('DisplayName')
            except FileNotFoundError:
                if not (cls.installed / 'Uninstall.exe').exists():
                    return
            time.sleep(.1)
        raise AssertionError('NSIS removal did not complete')

    @classmethod
    def _assert_registry_gone(cls):
        return unittest.TestCase().assertRaises(FileNotFoundError)

    def test_install_identity_payload_and_shortcuts(self):
        self.assertEqual(self.read_registry('DisplayName'), self.name)
        self.assertEqual(Path(self.read_registry('InstallLocation')), self.installed)
        self.assertEqual(hashlib.sha256((self.installed / 'AX-Workspace.exe').read_bytes()).hexdigest(), self.original_hash)
        # Inspect actual Shell links and their AppUserModelID, not script text.
        script = self.root / 'read-links.ps1'
        script.write_text('''param($ProductName)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$wsh=New-Object -ComObject WScript.Shell
$shell=New-Object -ComObject Shell.Application
$rows=@(foreach($folder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {
 $path=Join-Path $folder ($ProductName+'.lnk')
 $link=$wsh.CreateShortcut($path)
 $item=$shell.NameSpace($folder).ParseName($ProductName+'.lnk')
 @{target=$link.TargetPath; appId=$item.ExtendedProperty('System.AppUserModel.ID')}
})
$rows | ConvertTo-Json -Compress
''', encoding='utf-8-sig')
        rows = json.loads(self.run_checked([self.ps, '-NoProfile', '-File', str(script), self.name]).stdout)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(Path(row['target']), self.installed / 'AX-Workspace.exe')
            self.assertEqual(row['appId'], self.product)

    def test_reinstall_preserves_unowned_files_and_uninstall_does_too(self):
        sentinel = self.installed / '사용자-보관자료.txt'
        sentinel.write_text('preserve this file', encoding='utf-8')
        self.install()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preserve this file')
        # Verify full uninstall and reinstall in one test, with data remaining.
        self.uninstall()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preserve this file')
        self.assertFalse((self.installed / 'AX-Workspace.exe').exists())
        with self.assertRaises(FileNotFoundError):
            self.read_registry('DisplayName')
        # Remove only the test-created sentinel, leaving an empty fresh target.
        sentinel.unlink()
        self.install()

    def test_unowned_nonempty_directory_is_rejected(self):
        target = self.root / 'unrelated'
        target.mkdir()
        sentinel = target / 'document.txt'
        sentinel.write_bytes(b'keep')
        result = subprocess.run(self.command(self.setup, '/D=', target), timeout=30, creationflags=HIDDEN)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(target.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertEqual(Path(self.read_registry('InstallLocation')), self.installed)

    def test_concurrent_install_and_uninstall_are_rejected(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, 0, 'Local\\' + self.product + '.Installer')
        self.assertTrue(handle)
        try:
            result = subprocess.run(self.command(self.setup, '/D=', self.installed), timeout=30, creationflags=HIDDEN)
            self.assertNotEqual(result.returncode, 0)
            runner = self.root / 'mutex-remover.exe'
            shutil.copy2(self.installed / 'Uninstall.exe', runner)
            result = subprocess.run(self.command(runner, '_?=', self.installed), timeout=30, creationflags=HIDDEN)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((self.installed / 'AX-Workspace.exe').exists())
        finally:
            kernel.CloseHandle(handle)

    def test_installed_entry_extracts_verified_payload(self):
        cache = self.root / 'isolated-cache'
        self.run_checked([str(self.installed / 'AX-Workspace.exe'), '--verify-only', '--cache-root', str(cache)])
        payload = next(cache.glob('*/Company-Workspace'))
        self.assertTrue((payload / 'local_app/server.py').exists())
        self.assertTrue((payload / 'docs/WORKSPACE_USER_GUIDE.html').exists())
        self.assertFalse((payload / 'runtime').exists())

    def test_locked_uninstaller_reports_failure(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(self.installed / 'Uninstall.exe'), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            result = subprocess.run(self.command(self.setup, '/D=', self.installed), timeout=30, creationflags=HIDDEN)
            self.assertNotEqual(result.returncode, 0)
        finally:
            kernel.CloseHandle(handle)
        self.install()

    def test_installed_app_starts_and_stops_in_isolated_demo(self):
        state = self.root / 'isolated-state'
        cache = self.root / 'demo-cache'
        runtime = state / 'demo/runtime.json'
        env = os.environ.copy()
        env.pop('PSModulePath', None)
        env['CLAUDE_CONFIG_DIR'] = str(self.root / 'isolated-claude-config')
        process = subprocess.Popen([str(self.installed / 'AX-Workspace.exe'), '--demo', '--no-browser',
                                    '--state', str(state), '--cache-root', str(cache), '--python', sys.executable],
                                   env=env, creationflags=HIDDEN)
        origin = token = None
        def request(route, data=None):
            headers = {'Authorization': 'Bearer ' + token}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            with urlopen(Request(origin + route, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None), timeout=10) as response:
                return json.load(response)
        try:
            deadline = time.monotonic() + 80
            while not runtime.exists() and time.monotonic() < deadline:
                if process.poll() not in (None, 0):
                    self.fail('Installed entry failed: ' + str(process.returncode))
                time.sleep(.15)
            self.assertTrue(runtime.exists(), 'Installed app runtime did not become ready')
            context = json.loads(runtime.read_text(encoding='utf-8'))
            address = urlsplit(context['url'])
            origin = address.scheme + '://' + address.netloc
            token = parse_qs(address.fragment)['token'][0]
            boot = request('/api/bootstrap')
            self.assertTrue(boot['demo'])
            self.assertEqual(boot['sessions'], [])
            self.assertEqual(boot['workspaceVersion'], self.read_registry('DisplayVersion'))
            task = request('/api/create', {'managed': True, 'trusted': True, 'title': '설치판 실행 검증'})
            self.assertTrue(Path(task['workspace']).resolve().is_relative_to(state.resolve()))
            self.assertTrue(request('/api/quit', {})['ok'])
            deadline = time.monotonic() + 20
            while runtime.exists() and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertFalse(runtime.exists())
            process.wait(timeout=20)
            self.assertEqual(process.returncode, 0)
        finally:
            if token and runtime.exists():
                request('/api/quit', {})
            if process.poll() is None:
                process.wait(timeout=100)


if __name__ == '__main__':
    unittest.main()
