"""Exercise the real compiled bootstrap with tiny, isolated resource fixtures.

The test launcher never starts Claude or the application server. It records
arguments in its own temporary folder, without modifying any personal profile.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/CompanyWorkspace.Standalone.cs'
MANIFEST = ROOT / 'deploy/CompanyWorkspace.Standalone.manifest'
FRAMEWORK = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319'
CSC = FRAMEWORK / 'csc.exe'
PREFIX = 'Company-Workspace/'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


@unittest.skipUnless(os.name == 'nt' and CSC.is_file(), 'Windows .NET Framework compiler required')
class StandaloneBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='workspace 단일 실행 & ')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.counter = 0
        cls.script = (
            'param([string]$PythonCommand="auto", [switch]$NoBrowser, [switch]$Demo, [string]$StateRoot)\n'
            '$ErrorActionPreference="Stop"\n'
            '@{ python=$PythonCommand; pythonOverride=$PSBoundParameters.ContainsKey("PythonCommand"); '
            'noBrowser=[bool]$NoBrowser; demo=[bool]$Demo; '
            'state=$StateRoot; cwd=(Get-Location).Path; config=$env:CLAUDE_CONFIG_DIR; '
            'model=$env:ANTHROPIC_MODEL; path=$env:PATH; entry=$env:COMPANY_AGENT_CLAUDE } '
            '| ConvertTo-Json -Compress | Set-Content -LiteralPath $env:WORKSPACE_BOOTSTRAP_RESULT -Encoding UTF8\n'
            '[Console]::Out.WriteLine("private-profile-output-not-for-ui")\n'
            '[Console]::Error.WriteLine("private-profile-error-not-for-ui")\n'
            'if ($env:WORKSPACE_BOOTSTRAP_EXIT) { exit ([int]$env:WORKSPACE_BOOTSTRAP_EXIT) }\n'
            'exit 0\n'
        ).encode('utf-8-sig')
        cls.base_files = {
            PREFIX + 'deploy/Start-CompanyWorkspace.ps1': cls.script,
            PREFIX + 'local_app/server.py': b'# isolated fixture; never executed\n',
            PREFIX + 'local_app/web/한글 설명.txt': '한글 파일과 경로 확인'.encode(),
        }
        cls.good_exe, cls.good_hash, cls.good_files = cls.compile_fixture()

    @classmethod
    def compile_fixture(cls, *, entries=None, manifest_entries=None, bad_archive_hash=False, powershell_archive=False):
        cls.counter += 1
        build = cls.root / ('fixture-' + str(cls.counter))
        build.mkdir()
        entries = list(cls.base_files.items()) if entries is None else entries
        archive = build / 'payload.zip'
        if powershell_archive:
            payload = build / 'payload'
            for name, data in entries:
                file = payload / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
            compressor = build / 'compress.ps1'
            compressor.write_text(
                'param([string]$Source, [string]$Destination)\n'
                '$ErrorActionPreference="Stop"\n'
                'Compress-Archive -LiteralPath $Source -DestinationPath $Destination -CompressionLevel Optimal\n',
                encoding='utf-8-sig')
            powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            compressed = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                         str(compressor), '-Source', str(payload / 'Company-Workspace'),
                                         '-Destination', str(archive)], capture_output=True, timeout=30,
                                        creationflags=HIDDEN)
            if compressed.returncode:
                raise AssertionError(compressed.stderr.decode(errors='replace'))
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
                    for name, data in entries:
                        z.writestr(name, data)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        listed = list(cls.base_files.items()) if manifest_entries is None else manifest_entries
        (build / 'manifest.tsv').write_text(''.join(hashlib.sha256(data).hexdigest() + '\t' + name + '\n'
                                                  for name, data in listed), encoding='utf-8')
        (build / 'build.txt').write_text('0.12.5\n' + ('0' * 64 if bad_archive_hash else digest) + '\n', encoding='utf-8')
        exe = build / 'Company Workspace.exe'
        command = [str(CSC), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                   '/out:' + str(exe), '/win32manifest:' + str(MANIFEST),
                   '/reference:' + str(FRAMEWORK / 'System.IO.Compression.dll'),
                   '/reference:' + str(FRAMEWORK / 'System.IO.Compression.FileSystem.dll'),
                   '/reference:System.Windows.Forms.dll',
                   '/resource:' + str(archive) + ',WorkspacePayload.zip',
                   '/resource:' + str(build / 'manifest.tsv') + ',WorkspacePayload.manifest.tsv',
                   '/resource:' + str(build / 'build.txt') + ',WorkspaceBuild.txt', str(SOURCE)]
        compiled = subprocess.run(command, capture_output=True, timeout=40, creationflags=HIDDEN)
        if compiled.returncode:
            raise AssertionError(compiled.stdout.decode(errors='replace') + compiled.stderr.decode(errors='replace'))
        return exe, digest, dict(listed)

    def setUp(self):
        self.case = self.root / self.id().rsplit('.', 1)[-1]
        self.case.mkdir()
        self.cache = self.case / '보관 자료'

    def run_exe(self, exe=None, *args, env=None, cache=None):
        command = [str(exe or self.good_exe), '--cache-root', str(cache or self.cache), '--no-browser', *args]
        return subprocess.run(command, cwd=self.case, env=env, capture_output=True, timeout=30, creationflags=HIDDEN)

    def target(self, digest=None):
        return self.cache / ('0.12.5-' + (digest or self.good_hash)[:16])

    def test_extract_only_and_repeat_reuses_identical_cache(self):
        first = self.run_exe(None, '--verify-only')
        self.assertEqual(0, first.returncode, first.stderr)
        expected = self.target() / (PREFIX + 'local_app/web/한글 설명.txt')
        self.assertEqual(self.good_files[PREFIX + 'local_app/web/한글 설명.txt'], expected.read_bytes())
        self.assertEqual(set(self.good_files), {p.relative_to(self.target()).as_posix()
                                              for p in self.target().rglob('*') if p.is_file()})
        self.assertFalse((self.target() / (PREFIX + 'runtime')).exists())
        timestamps = {str(p): p.stat().st_mtime_ns for p in self.target().rglob('*') if p.is_file()}
        second = self.run_exe(None, '--verify-only')
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(timestamps, {str(p): p.stat().st_mtime_ns for p in self.target().rglob('*') if p.is_file()})
        self.assertEqual([self.target()], list(self.cache.iterdir()))

    def test_exe_copied_alone_to_empty_folder_runs(self):
        delivery = self.case / '별도 전달 & 공백'
        delivery.mkdir()
        exe = delivery / 'Company Workspace.exe'
        shutil.copyfile(self.good_exe, exe)
        result = self.run_exe(exe, '--verify-only')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([exe], list(delivery.iterdir()))

    def test_corrupt_cache_is_preserved_and_rejected(self):
        self.assertEqual(0, self.run_exe(None, '--verify-only').returncode)
        damaged = self.target() / (PREFIX + 'local_app/server.py')
        damaged.write_bytes(b'changed by test')
        result = self.run_exe(None, '--verify-only')
        self.assertEqual(54, result.returncode)
        self.assertEqual(b'changed by test', damaged.read_bytes())
        self.assertIn(b'EXE-54', result.stderr)

    def test_missing_cache_file_is_not_silently_replaced(self):
        self.assertEqual(0, self.run_exe(None, '--verify-only').returncode)
        damaged = self.target() / (PREFIX + 'deploy/Start-CompanyWorkspace.ps1')
        damaged.unlink()
        self.assertEqual(54, self.run_exe(None, '--verify-only').returncode)
        self.assertFalse(damaged.exists())

    def test_archive_hash_mismatch_leaves_no_completed_or_staging_cache(self):
        exe, _, _ = self.compile_fixture(bad_archive_hash=True)
        self.assertEqual(51, self.run_exe(exe, '--verify-only').returncode)
        self.assertEqual([], list(self.cache.iterdir()))

    def test_duplicate_zip_entry_and_unlisted_zip_entry_rejected(self):
        for suffix, extra in [('duplicate', next(iter(self.base_files.items()))), ('extra', (PREFIX + 'extra.py', b'extra'))]:
            exe, _, _ = self.compile_fixture(entries=list(self.base_files.items()) + [extra])
            cache = self.case / suffix
            self.assertEqual(51, self.run_exe(exe, '--verify-only', cache=cache).returncode)
            self.assertEqual([], list(cache.iterdir()))

    def test_powershell_backslash_directory_records_are_canonicalized(self):
        # Compress-Archive on Windows writes directories with backslashes while
        # the same ZIP's file records may use forward slashes.
        directories = [(name, b'') for name in [
            'Company-Workspace\\', 'Company-Workspace\\deploy\\',
            'Company-Workspace\\local_app\\',
            'Company-Workspace\\local_app\\web\\']]
        exe, digest, _ = self.compile_fixture(entries=directories + list(self.base_files.items()))
        result = self.run_exe(exe, '--verify-only')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((self.target(digest) / (PREFIX + 'local_app/web/한글 설명.txt')).is_file())

    def test_actual_powershell_compressed_payload_extracts(self):
        exe, digest, files = self.compile_fixture(powershell_archive=True)
        result = self.run_exe(exe, '--verify-only')
        self.assertEqual(0, result.returncode, result.stderr)
        for name, data in files.items():
            self.assertEqual(data, (self.target(digest) / name).read_bytes())

    def test_backslash_zip_paths_still_reject_traversal_and_alias_duplicates(self):
        for suffix, name in [('traversal', 'Company-Workspace\\..\\escaped.txt'),
                              ('duplicate', 'Company-Workspace\\local_app\\server.py')]:
            exe, _, _ = self.compile_fixture(entries=list(self.base_files.items()) + [(name, b'bad')])
            cache = self.case / suffix
            self.assertEqual(51, self.run_exe(exe, '--verify-only', cache=cache).returncode)
            self.assertEqual([], list(cache.iterdir()))

    def test_traversal_and_windows_alias_paths_rejected(self):
        for i, name in enumerate([PREFIX + '../escape.txt', PREFIX + 'nested/../../escape.txt',
                                  PREFIX + 'local_app/CON.txt', PREFIX + 'local_app/file:stream',
                                  PREFIX + 'local_app/trailing.', PREFIX + 'local_app/dir\\escape.txt']):
            with self.subTest(name=name):
                extra = (name, b'do not extract')
                exe, _, _ = self.compile_fixture(entries=list(self.base_files.items()) + [extra],
                                                  manifest_entries=list(self.base_files.items()) + [extra])
                self.assertEqual(51, self.run_exe(exe, '--verify-only', cache=self.case / str(i)).returncode)
        self.assertFalse((self.case / 'escape.txt').exists())

    def test_manifest_case_collision_and_content_mismatch_rejected(self):
        collision = list(self.base_files.items()) + [(PREFIX + 'LOCAL_APP/SERVER.PY', b'collision')]
        exe, _, _ = self.compile_fixture(manifest_entries=collision)
        self.assertEqual(51, self.run_exe(exe, '--verify-only').returncode)
        wrong = dict(self.base_files)
        wrong[PREFIX + 'local_app/server.py'] = b'wrong manifest bytes'
        exe, _, _ = self.compile_fixture(manifest_entries=list(wrong.items()))
        self.assertEqual(54, self.run_exe(exe, '--verify-only').returncode)
        self.assertEqual([], list(self.cache.iterdir()))

    def test_app_and_shared_launcher_are_required_before_cache_creation(self):
        for relative in ['deploy/Start-CompanyWorkspace.ps1', 'local_app/server.py']:
            with self.subTest(relative=relative):
                files = [(name, data) for name, data in self.base_files.items() if name != PREFIX + relative]
                exe, _, _ = self.compile_fixture(entries=files, manifest_entries=files)
                cache = self.case / Path(relative).name
                self.assertEqual(51, self.run_exe(exe, '--verify-only', cache=cache).returncode)
                self.assertFalse(cache.exists())

    def test_parallel_extractors_share_one_cache(self):
        args = [str(self.good_exe), '--cache-root', str(self.cache), '--verify-only']
        children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=HIDDEN)
                    for _ in range(3)]
        try:
            for child in children:
                stdout, stderr = child.communicate(timeout=30)
                self.assertEqual(0, child.returncode, stderr)
            self.assertEqual([self.target()], list(self.cache.iterdir()))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_launcher_receives_paths_flags_and_unchanged_environment(self):
        record = self.case / '실행 인수.json'
        state = self.case / '작업 자료 & $literal; (폴더)'
        env = os.environ.copy()
        env.update(WORKSPACE_BOOTSTRAP_RESULT=str(record), CLAUDE_CONFIG_DIR='fixture-config-unchanged',
                   ANTHROPIC_MODEL='fixture-model-unchanged', COMPANY_AGENT_CLAUDE='fixture-existing-wrapper')
        result = self.run_exe(None, '--state', str(state), '--demo', env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(record.read_text(encoding='utf-8-sig'))
        self.assertEqual('auto', value['python'])
        self.assertFalse(value['pythonOverride'])
        self.assertEqual(str(self.target() / 'Company-Workspace'), value['cwd'])
        self.assertEqual(str(state), value['state'])
        self.assertTrue(value['demo'])
        self.assertTrue(value['noBrowser'])
        for key, variable in [('config', 'CLAUDE_CONFIG_DIR'), ('model', 'ANTHROPIC_MODEL'),
                              ('entry', 'COMPANY_AGENT_CLAUDE')]:
            self.assertEqual(env[variable], value[key])
        # Profiles may legitimately change PATH; the bootstrap itself must not
        # prepend its application cache, which would alter existing wrappers.
        self.assertNotIn(str(self.target()).lower(), value['path'].lower())
        self.assertNotIn(b'private-profile-', result.stdout + result.stderr)

    def test_explicit_existing_python_path_is_one_literal_argument(self):
        directory = self.case / '기존 Python -Demo & $literal; (폴더)'
        directory.mkdir()
        selected = directory / 'python.exe'
        selected.write_bytes(b'fixture path only; never executed')
        record = self.case / '선택 경로.json'
        env = os.environ.copy()
        env.update(WORKSPACE_BOOTSTRAP_RESULT=str(record), CLAUDE_CONFIG_DIR='explicit-config-unchanged',
                   ANTHROPIC_MODEL='explicit-model-unchanged', COMPANY_AGENT_CLAUDE='explicit-wrapper-unchanged')
        result = self.run_exe(None, '--python', str(selected), env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(record.read_text(encoding='utf-8-sig'))
        self.assertEqual(str(selected), value['python'])
        self.assertTrue(value['pythonOverride'])
        self.assertTrue(value['noBrowser'])
        self.assertFalse(value['demo'])
        self.assertFalse(value['state'])
        self.assertEqual('explicit-config-unchanged', value['config'])
        self.assertEqual('explicit-model-unchanged', value['model'])
        self.assertEqual('explicit-wrapper-unchanged', value['entry'])
        self.assertNotIn(str(self.target()).lower(), value['path'].lower())
        self.assertEqual(b'fixture path only; never executed', selected.read_bytes())

    def test_invalid_python_option_never_prepares_or_launches_app(self):
        text = self.case / 'python.txt'
        text.write_text('not an executable', encoding='utf-8')
        directory = self.case / 'python.exe'
        directory.mkdir()
        existing = self.case / 'existing-python.exe'
        existing.write_bytes(b'fixture')
        invalid = [('--python',), ('--python', 'python'), ('--python', 'python.exe'),
                   ('--python', r'C:python.exe'), ('--python', r'\python.exe'),
                   ('--python', r'C:\bad|python.exe'), ('--python', str(self.case / 'missing.exe')),
                   ('--python', str(text)), ('--python', str(directory)),
                   ('--python', str(existing) + ' -Demo'),
                   ('--python', str(existing) + '" -Demo "'),
                   ('--python', str(existing), '--python', str(existing))]
        record = self.case / 'must-not-launch.json'
        env = {**os.environ, 'WORKSPACE_BOOTSTRAP_RESULT': str(record)}
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                result = self.run_exe(None, *arguments, env=env)
                self.assertEqual(50, result.returncode, result.stderr)
                self.assertIn(b'EXE-50', result.stderr)
                self.assertFalse(self.cache.exists())
                self.assertFalse(record.exists())

    def test_reported_launcher_error_is_not_reported_twice(self):
        env = os.environ.copy()
        env.update(WORKSPACE_BOOTSTRAP_RESULT=str(self.case / 'args.json'), WORKSPACE_BOOTSTRAP_EXIT='20')
        result = self.run_exe(env=env)
        self.assertEqual(20, result.returncode)
        self.assertEqual(b'', result.stdout + result.stderr)

    def test_no_browser_error_preserves_launcher_category_without_private_output(self):
        # Missing/unsupported installed Python remains the shared launcher's
        # precise prerequisite failure, rather than a generic extraction error.
        for code in [33, 37, 38]:
            with self.subTest(code=code):
                env = os.environ.copy()
                env.update(WORKSPACE_BOOTSTRAP_RESULT=str(self.case / 'args.json'), WORKSPACE_BOOTSTRAP_EXIT=str(code))
                result = self.run_exe(env=env)
                self.assertEqual(code, result.returncode)
                self.assertIn(('WS-' + str(code)).encode(), result.stderr)
                self.assertNotIn(b'EXE-58', result.stderr)
                self.assertNotIn(b'private-profile-', result.stdout + result.stderr)

    def test_invalid_options_and_nonabsolute_paths_do_not_extract(self):
        for args in [('--unknown',), ('--state', 'relative'), ('--state', r'C:relative'),
                     ('--state', r'\relative'), ('--state',), ('--demo', '--demo')]:
            self.assertEqual(50, self.run_exe(None, *args).returncode)
        self.assertFalse(self.cache.exists())

    def test_reparse_cache_ancestor_rejected_without_touching_destination(self):
        destination = self.case / 'junction-destination'
        destination.mkdir()
        junction = self.case / 'junction-cache'
        # mklink only creates an isolated test junction; it never deletes/moves.
        created = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(destination)],
                                 capture_output=True, creationflags=HIDDEN)
        if created.returncode:
            self.skipTest('This Windows environment cannot create a test junction')
        try:
            self.assertEqual(53, self.run_exe(None, '--verify-only', cache=junction).returncode)
            self.assertEqual([], list(destination.iterdir()))
        finally:
            # rmdir removes only the junction entry, not its destination tree.
            junction.rmdir()


class StandaloneManifestTests(unittest.TestCase):
    def test_manifest_requests_no_elevation_and_per_monitor_dpi(self):
        import xml.etree.ElementTree as ET
        root = ET.parse(MANIFEST).getroot()
        level = root.find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
        self.assertEqual('asInvoker', level.attrib['level'])
        self.assertEqual('false', level.attrib['uiAccess'])
        dpi = root.find('.//{http://schemas.microsoft.com/SMI/2016/WindowsSettings}dpiAwareness')
        self.assertIn('PerMonitorV2', dpi.text)


if __name__ == '__main__':
    unittest.main()
