"""Same-version rebuilds must not silently reuse an older loaded process."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from local_app.build_identity import build_identity, EXCLUDED, SUFFIXES

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'deploy/CompanyWorkspace.Startup.ps1'


class BuildIdentityTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'local_app/web').mkdir(parents=True)
        (root / 'local_app/server.py').write_bytes(b'print("server")\r\n')
        (root / 'local_app/web/app.js').write_text('const label="테스트";\n', encoding='utf-8')

    def test_content_not_path_state_or_generated_cache_defines_build(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / 'original'
            self.fixture(root)
            initial = build_identity(root)
            self.assertRegex(initial, r'^[a-f0-9]{64}$')
            copy = Path(raw) / '새 복사본'
            shutil.copytree(root, copy)
            (copy / 'local_app/__pycache__').mkdir()
            (copy / 'local_app/__pycache__/generated.pyc').write_bytes(b'generated')
            (copy / 'runtime.json').write_text('{}')
            (copy / 'local_app/companion.py').write_text('optional experiment')
            self.assertEqual(initial, build_identity(copy))
            (copy / 'local_app/web/app.js').write_text('changed')
            self.assertNotEqual(initial, build_identity(copy))
            (copy / 'local_app/web/app.js').unlink()
            self.assertNotEqual(initial, build_identity(copy))

    def test_bundle_contains_every_fingerprinted_source(self):
        source = (ROOT / 'deploy/New-WorkspaceBundle.ps1').read_text(encoding='utf-8-sig')
        included = {p.replace('\\', '/') for p in re.findall(r"'(local_app[^']+)'", source)}
        fingerprinted = {p.relative_to(ROOT).as_posix()
                         for folder in (ROOT / 'local_app', ROOT / 'local_app/web') for p in folder.iterdir()
                         if p.is_file() and p.suffix.lower() in SUFFIXES and p.name not in EXCLUDED}
        self.assertFalse(fingerprinted - included, fingerprinted - included)

    @unittest.skipUnless(os.name == 'nt', 'Windows startup parity')
    def test_launcher_rejects_old_same_version_process_before_reuse(self):
        # Invoke the real copied entrypoint. Only its platform/context and HTTP
        # responses are replaced; no live process or user profile is touched.
        for mode, expected in (('same', 0), ('changed', 48), ('legacy', 48), ('newer', 0)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                self.fixture(root)
                deploy = root / 'deploy'
                deploy.mkdir()
                launcher = deploy / 'Start-CompanyWorkspace.ps1'
                shutil.copyfile(ROOT / 'deploy/Start-CompanyWorkspace.ps1', launcher)
                fingerprint = build_identity(root) if mode == 'same' else '0' * 64 if mode == 'changed' else ''
                version = '9.0.0' if mode == 'newer' else '0.23.29'
                helper = HELPER.read_text(encoding='utf-8-sig') + """
function Get-WorkspaceVerifiedContext { [pscustomobject]@{verified=$true;sid='S-1-5-21-build-fixture';sessionId=1;isAdministrator=$false} }
function Get-WorkspaceExecutionMode { 'normal' }
function Invoke-RestMethod {
 param($Uri,$Headers,$TimeoutSec,$MaximumRedirection)
 if($Uri -notlike '*/api/bootstrap'){throw 'Unexpected mutating call'}
 [pscustomobject]@{application='company-workspace';demo=$true;workspaceVersion='VERSION';
 executionMode='normal';executionModeProtocol=3;buildIdentity='FINGERPRINT';closing=$false}
}
""".replace('VERSION', version).replace('FINGERPRINT', fingerprint)
                (deploy / HELPER.name).write_text(helper, encoding='utf-8-sig')
                state = root / 'state'
                (state / 'demo').mkdir(parents=True)
                (state / 'demo/runtime.json').write_text(json.dumps({'url': 'http://127.0.0.1:12345/#token=' + 'a' * 48}))
                result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(launcher),
                                         '-Demo', '-NoBrowser', '-StateRoot', str(state)],
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, expected, result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'Windows startup parity')
    def test_powershell_same_build_reuses_but_rebuilt_or_legacy_build_does_not(self):
        with tempfile.TemporaryDirectory(prefix='workspace-build-한글 ') as raw:
            root = Path(raw)
            self.fixture(root)
            initial = build_identity(root)
            def quote(value):
                return "'" + str(value).replace("'", "''") + "'"
            source = f"""
            [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
            . {quote(HELPER)}
            $root = {quote(root)}
            $first = Get-WorkspaceBuildIdentity $root
            $same = Test-WorkspaceRunningBuild $root {quote(initial)}
            [IO.File]::AppendAllText((Join-Path $root 'local_app/web/app.js'), 'changed')
            @{{ fingerprint=$first; same=$same;
                changed=(Test-WorkspaceRunningBuild $root {quote(initial)});
                missing=(Test-WorkspaceRunningBuild $root '');
                invalid=(Test-WorkspaceRunningBuild $root 'unverified');
                message=(Get-WorkspaceStartupMessage 48) }} | ConvertTo-Json -Compress
            """
            result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', source],
                                    capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['fingerprint'], initial)
            self.assertTrue(data['same'])
            for name in ('changed', 'missing', 'invalid'):
                self.assertFalse(data[name], name)
            self.assertIn('완전 종료', data['message'])
            self.assertIn('삭제할 필요', data['message'])


if __name__ == '__main__':
    unittest.main()
