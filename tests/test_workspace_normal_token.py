from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/CompanyWorkspace.NormalToken.cs'
PS = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == 'nt' and PS.is_file(), 'Windows PowerShell 5.1 native token checks')
class NormalTokenTests(unittest.TestCase):
    """Real read-only native probes; synthetic identity fixtures never launch.

    The only test child is an ordinary PowerShell process running a temporary
    argument-echo script. No elevation, linked-token launch, profile/config
    edits, credentials, or production application startup is performed.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='workspace token 한글 & ')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.echo_script = Path(cls.temp.name) / '인수 전달 & 확인.ps1'
        cls.echo_script.write_text(
            "param([switch]$NormalTokenRelaunch, [string]$PythonCommand, "
            "[switch]$Demo, [switch]$NoBrowser, [string]$StateRoot)\n"
            "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)\n"
            "@{ normalTokenRelaunch=[bool]$NormalTokenRelaunch; python=$PythonCommand; "
            "demo=[bool]$Demo; noBrowser=[bool]$NoBrowser; state=$StateRoot } | ConvertTo-Json -Compress\n",
            encoding='utf-8-sig',
        )
        cls.python_literal = 'C:\\파이썬 & 도구\\$literal; (설정)\\python.exe'
        cls.state_literal = 'C:\\작업 & 자료\\한글 상태\\'
        cls.quote_values = [
            '', 'python', '한글 & 공백', 'C:\\Program Files\\Python\\python.exe',
            'C:\\끝 경로\\', 'C:\\끝 경로\\\\', 'a"b', 'a\\"b', 'a\\\\"b',
            'literal $(Write-Output BAD); & | < > %PATH% `', 'x\ty',
        ]
        payload = dict(script=str(cls.echo_script), python=cls.python_literal,
                       state=cls.state_literal, quotes=cls.quote_values)
        harness = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
Add-Type -Path SOURCE_PATH
$data = [Console]::In.ReadToEnd() | ConvertFrom-Json
$expectedSid = 'S-1-5-21-100-200-300-1001'
$differentSid = 'S-1-5-21-100-200-300-1002'
function Make-Snapshot($sid=$expectedSid, $session=1, $kind=3, $elevated=$false,
                       $integrity=8192, $admin=$false, $primary=$true) {
    New-Object CompanyAgent.WorkspaceTokenSnapshot($sid, $session, $kind, $elevated, $integrity, $admin, $primary)
}
$fixtures = @(
    @{ name='limited_same_user'; value=(Make-Snapshot); expected=$true },
    @{ name='missing'; value=$null; expected=$false },
    @{ name='other_user'; value=(Make-Snapshot -sid $differentSid); expected=$false },
    @{ name='other_session'; value=(Make-Snapshot -session 2); expected=$false },
    @{ name='service_session'; value=(Make-Snapshot -session 0); expected=$false },
    @{ name='no_linked_uac_token'; value=(Make-Snapshot -kind 1); expected=$false },
    @{ name='full_token'; value=(Make-Snapshot -kind 2); expected=$false },
    @{ name='elevated'; value=(Make-Snapshot -elevated $true); expected=$false },
    @{ name='low_integrity'; value=(Make-Snapshot -integrity 4096); expected=$false },
    @{ name='medium_plus_integrity'; value=(Make-Snapshot -integrity 8448); expected=$false },
    @{ name='high_integrity'; value=(Make-Snapshot -integrity 12288); expected=$false },
    @{ name='system_integrity'; value=(Make-Snapshot -integrity 16384); expected=$false },
    @{ name='enabled_admin'; value=(Make-Snapshot -admin $true); expected=$false },
    @{ name='impersonation_token'; value=(Make-Snapshot -primary $false); expected=$false }
)
$candidateResults = @($fixtures | ForEach-Object {
    @{ name=$_.name; expected=$_.expected;
       actual=[CompanyAgent.WorkspaceNormalToken]::ValidateNormalTokenCandidate($_.value, $expectedSid, 1) }
})
$processFixtures = @($fixtures | ForEach-Object {
    @{ name=$_.name; value=$_.value; expected=($_.expected -or $_.name -eq 'no_linked_uac_token') }
}) + @(
    @{ name='standard_elevated_without_admin'; value=(Make-Snapshot -kind 1 -elevated $true); expected=$false },
    @{ name='standard_high_without_admin'; value=(Make-Snapshot -kind 1 -integrity 12288); expected=$false },
    @{ name='standard_enabled_admin'; value=(Make-Snapshot -kind 1 -admin $true); expected=$false },
    @{ name='unknown_elevation_type'; value=(Make-Snapshot -kind 0); expected=$false }
)
$normalProcessResults = @($processFixtures | ForEach-Object {
    @{ name=$_.name; expected=$_.expected;
       actual=[CompanyAgent.WorkspaceNormalToken]::ValidateNormalProcess($_.value, $expectedSid, 1) }
})
$source = Make-Snapshot -kind 2 -elevated $true -integrity 12288 -admin $true
$sourceResults = @{
    full=[CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken($source, $expectedSid, 1)
    differentUser=[CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken($source, $differentSid, 1)
    differentSession=[CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken($source, $expectedSid, 2)
    noLinkedToken=[CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken((Make-Snapshot -kind 1 -elevated $true -admin $true), $expectedSid, 1)
    limited=[CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken((Make-Snapshot), $expectedSid, 1)
    missing=[CompanyAgent.WorkspaceNormalToken]::ValidateSourceToken($null, $expectedSid, 1)
}
$quoted = @($data.quotes | ForEach-Object { [CompanyAgent.WorkspaceNormalToken]::QuoteWindowsArgument($_) })
$malformed = @("bad`0value", "bad`rvalue", "bad`nvalue")
$invalidArgumentResults = @($malformed | ForEach-Object {
    try { [CompanyAgent.WorkspaceNormalToken]::QuoteWindowsArgument($_) | Out-Null; $false }
    catch { $_.Exception.InnerException.ReasonCode -eq 'invalid_argument' }
})
# PowerShell normally converts $null to an empty string at a typed .NET call.
# Reflection exercises the actual C# null precondition instead.
try {
    [CompanyAgent.WorkspaceNormalToken].GetMethod('QuoteWindowsArgument').Invoke($null, [object[]]@($null)) | Out-Null
    $nullArgumentRejected = $false
} catch {
    $failure = $_.Exception
    while ($failure.InnerException) { $failure = $failure.InnerException }
    $nullArgumentRejected = $failure.ReasonCode -eq 'invalid_argument'
}
$baseline = [CompanyAgent.WorkspaceNormalToken]::BuildCommandLine($data.script, 'python', $false, $false, 'x')
$length = 1023 - $baseline.Length + 1
$boundary = [CompanyAgent.WorkspaceNormalToken]::BuildCommandLine($data.script, 'python', $false, $false, ('x' * $length))
try { [CompanyAgent.WorkspaceNormalToken]::BuildCommandLine($data.script, 'python', $false, $false, ('x' * ($length + 1))) | Out-Null; $tooLong=$false }
catch { $tooLong=$_.Exception.InnerException.ReasonCode -eq 'command_too_long' }
$native = [CompanyAgent.WorkspaceNormalToken]::InspectCurrentToken()
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
try {
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $identityMatches = $native.UserSid -eq $identity.User.Value
    $adminMatches = $native.IsAdministrator -eq $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} finally { $identity.Dispose() }
$sessionMatches = $native.SessionId -eq (Get-Process -Id $PID).SessionId
# Warm up native probes, then check that token handles do not accumulate.
1..5 | ForEach-Object { [CompanyAgent.WorkspaceNormalToken]::InspectCurrentToken() | Out-Null }
$before = (Get-Process -Id $PID).HandleCount
1..30 | ForEach-Object { [CompanyAgent.WorkspaceNormalToken]::InspectCurrentToken() | Out-Null }
$after = (Get-Process -Id $PID).HandleCount
# Deliberately mismatching the SID guarantees rejection before linked-token
# lookup/creation on both a standard test runner and an elevated test runner.
try {
    [CompanyAgent.WorkspaceNormalToken]::Relaunch($data.script, $data.python, $true, $true, $data.state, $differentSid, [Math]::Max(1, $native.SessionId)) | Out-Null
    $rejection=@{ rejected=$false }
} catch {
    $failure=$_.Exception.InnerException
    $rejection=@{ rejected=$true; reason=$failure.ReasonCode; message=$failure.Message; nativeCode=$failure.NativeErrorCode }
}
@{
    powershellMajor=$PSVersionTable.PSVersion.Major; powershellMinor=$PSVersionTable.PSVersion.Minor
    candidates=$candidateResults; normalProcesses=$normalProcessResults; sources=$sourceResults; quotes=$quoted
    invalidArguments=$invalidArgumentResults; nullArgumentRejected=$nullArgumentRejected
    boundaryLength=$boundary.Length; tooLongRejected=$tooLong
    native=@{ identityMatches=$identityMatches; adminMatches=$adminMatches; sessionMatches=$sessionMatches;
        primary=$native.IsPrimary; elevationType=$native.ElevationType; integrity=$native.IntegrityRid;
        handleGrowth=($after-$before) }
    rejection=$rejection
    command=[CompanyAgent.WorkspaceNormalToken]::BuildCommandLine($data.script, $data.python, $true, $true, $data.state)
    minimalCommand=[CompanyAgent.WorkspaceNormalToken]::BuildCommandLine($data.script, 'python', $false, $false, $null)
} | ConvertTo-Json -Depth 8 -Compress
""".replace('SOURCE_PATH', ps_literal(SOURCE))
        encoded = base64.b64encode(harness.encode('utf-16-le')).decode('ascii')
        result = subprocess.run(
            [str(PS), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
            input=json.dumps(payload, ensure_ascii=False), capture_output=True,
            encoding='utf-8', timeout=45,
        )
        if result.returncode:
            raise AssertionError(f'Windows native helper probe failed: {result.stderr}')
        cls.result = json.loads(result.stdout)

    def test_compiles_with_windows_powershell_51(self):
        self.assertEqual((5, 1), (self.result['powershellMajor'], self.result['powershellMinor']))

    def test_only_same_user_limited_primary_medium_non_admin_token_is_valid(self):
        for fixture in self.result['candidates']:
            with self.subTest(fixture=fixture['name']):
                self.assertEqual(fixture['expected'], fixture['actual'])

    def test_source_requires_same_user_session_full_split_token(self):
        self.assertTrue(self.result['sources']['full'])
        for name, valid in self.result['sources'].items():
            if name != 'full':
                with self.subTest(source=name):
                    self.assertFalse(valid)

    def test_normal_process_accepts_standard_or_limited_but_rejects_unsafe_non_admin_tokens(self):
        for fixture in self.result['normalProcesses']:
            with self.subTest(fixture=fixture['name']):
                self.assertEqual(fixture['expected'], fixture['actual'])

    def test_native_probe_matches_windows_identity_without_handle_leaks(self):
        native = self.result['native']
        for key in ('identityMatches', 'adminMatches', 'sessionMatches', 'primary'):
            self.assertTrue(native[key], key)
        self.assertIn(native['elevationType'], (1, 2, 3))
        self.assertGreaterEqual(native['integrity'], 0)
        self.assertLessEqual(native['handleGrowth'], 4)

    def test_identity_mismatch_rejects_before_any_relaunch_without_sensitive_error_text(self):
        rejection = self.result['rejection']
        self.assertTrue(rejection['rejected'])
        self.assertEqual('source_not_same_user_split_token', rejection['reason'])
        self.assertEqual(0, rejection['nativeCode'])
        for private_value in (str(ROOT), self.python_literal, self.state_literal):
            self.assertNotIn(private_value, rejection['message'])

    def test_quoted_literals_round_trip_through_windows_argument_parser(self):
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        for original, quoted in zip(self.quote_values, self.result['quotes'], strict=True):
            with self.subTest(value=original):
                count = ctypes.c_int()
                parsed = shell.CommandLineToArgvW('"probe.exe" ' + quoted, ctypes.byref(count))
                self.assertTrue(parsed)
                try:
                    self.assertEqual(2, count.value)
                    self.assertEqual(original, parsed[1])
                finally:
                    kernel.LocalFree(parsed)

    def test_argument_controls_and_native_command_length_are_bounded(self):
        self.assertEqual([True] * 3, self.result['invalidArguments'])
        self.assertTrue(self.result['nullArgumentRejected'])
        self.assertEqual(1023, self.result['boundaryLength'])
        self.assertTrue(self.result['tooLongRejected'])

    def test_powershell_file_binding_preserves_korean_spaces_ampersand_and_trailing_slash(self):
        # Production loads the user's profile. This argument-only test suppresses
        # profiles to avoid running any personal shell startup during the test.
        command = self.result['command'].replace(' -NoLogo ', ' -NoLogo -NoProfile -NonInteractive ', 1)
        completed = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(dict(normalTokenRelaunch=True, python=self.python_literal,
                              demo=True, noBrowser=True, state=self.state_literal),
                         json.loads(completed.stdout))

    def test_production_command_preserves_profile_and_optional_switch_intent(self):
        command = self.result['command']
        self.assertTrue(command.casefold().startswith(('"' + str(PS) + '"').casefold()))
        self.assertIn(' -NoLogo -WindowStyle Hidden -File ', command)
        self.assertIn(' -NormalTokenRelaunch ', command)
        for forbidden in ('-NoProfile', '-ExecutionPolicy', '-Command', '-EncodedCommand'):
            self.assertNotIn(forbidden, command)
        for optional in (' -Demo', ' -NoBrowser', ' -StateRoot'):
            self.assertIn(optional, command)
            self.assertNotIn(optional, self.result['minimalCommand'])


if __name__ == '__main__':
    unittest.main()
