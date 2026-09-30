from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/CompanyWorkspace.NormalToken.cs'
PS = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == 'nt' and PS.is_file(), 'Windows PowerShell 5.1 native token checks')
class NormalTokenTests(unittest.TestCase):
    """Native probes with explicitly opted-in disposable restricted descendants.

    Synthetic identity snapshots never launch. The DACL regression modifies
    only a test-created restricted host's copied token, never the real user's
    original token. No elevation, linked-token launch, profile/config edits,
    credentials, or production application startup is performed.
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
$unsplit = Make-Snapshot -kind 1 -elevated $true -integrity 12288 -admin $true
$restrictedSources = @{
    uacDisabled=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($unsplit, $expectedSid, 1, $true)
    uacEnabled=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($unsplit, $expectedSid, 1, $false)
    otherUser=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($unsplit, $differentSid, 1, $true)
    otherSession=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($unsplit, $expectedSid, 2, $true)
    splitToken=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($source, $expectedSid, 1, $true)
    normal=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot), $expectedSid, 1, $true)
    service=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot -kind 1 -elevated $true -integrity 12288 -admin $true -session 0), $expectedSid, 0, $true)
    systemIntegrity=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot -kind 1 -elevated $true -integrity 16384 -admin $true), $expectedSid, 1, $true)
    impersonation=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot -kind 1 -elevated $true -integrity 12288 -admin $true -primary $false), $expectedSid, 1, $true)
    nonAdmin=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot -kind 1 -elevated $true -integrity 12288), $expectedSid, 1, $true)
    nonElevated=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot -kind 1 -integrity 12288 -admin $true), $expectedSid, 1, $true)
    mediumIntegrity=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource((Make-Snapshot -kind 1 -elevated $true -admin $true), $expectedSid, 1, $true)
    missing=[CompanyAgent.WorkspaceNormalToken]::ValidateRestrictedSource($null, $expectedSid, 1, $true)
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
    candidates=$candidateResults; normalProcesses=$normalProcessResults; sources=$sourceResults; restrictedSources=$restrictedSources; quotes=$quoted
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

    def test_restricted_source_is_only_same_user_unsplit_high_admin_with_uac_disabled(self):
        for name, accepted in self.result['restrictedSources'].items():
            with self.subTest(source=name):
                self.assertEqual(name == 'uacDisabled', accepted)

    @unittest.skipUnless(os.environ.get('COMPANY_WORKSPACE_TEST_RESTRICTED_TOKEN') == '1',
                         'Opt in outside the sandbox: create and dispose only a restricted token copy')
    def test_native_restricted_copy_is_verified_without_mutating_original_or_launching(self):
        script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -Path SOURCE_PATH
$native=[CompanyAgent.WorkspaceNormalToken]
$flags=[Reflection.BindingFlags]'NonPublic,Static'
$source=$null; $restricted=$null
try {
    $process=$native.GetMethod('GetCurrentProcess',$flags).Invoke($null,@())
    $tokenArgs=[object[]]@($process,[uint32]139,$null)
    if (-not $native.GetMethod('OpenProcessToken',$flags).Invoke($null,$tokenArgs)) {throw 'Cannot open own token'}
    $source=$tokenArgs[2]
    $before=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
    $restricted=$native.GetMethod('CreateRestrictedNormalToken',$flags).Invoke($null,@($source))
    $filtered=$native.GetMethod('ReadToken',$flags).Invoke($null,@($restricted))
    $native.GetMethod('ValidateRestrictedCandidate',$flags).Invoke($null,@($restricted,$before.UserSid,$before.SessionId))
    $after=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
    $wrongIdentityRejected=$false
    try {$native.GetMethod('ValidateRestrictedCandidate',$flags).Invoke($null,@($restricted,'S-1-5-21-100-200-300-1001',$before.SessionId))}
    catch {$wrongIdentityRejected=$true}
    @{sameUser=($before.UserSid -eq $filtered.UserSid);sameSession=($before.SessionId -eq $filtered.SessionId);
      sourceUnchanged=(($before | ConvertTo-Json -Compress) -eq ($after | ConvertTo-Json -Compress));
      normal=[CompanyAgent.WorkspaceNormalToken]::ValidateNormalProcess($filtered,$before.UserSid,$before.SessionId);
      wrongIdentityRejected=$wrongIdentityRejected} | ConvertTo-Json -Compress
} finally {if($restricted){$restricted.Dispose()};if($source){$source.Dispose()}}
""".replace('SOURCE_PATH', ps_literal(SOURCE))
        result = subprocess.run([str(PS), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(dict(sameUser=True, sameSession=True, sourceUnchanged=True, normal=True,
                              wrongIdentityRejected=True), json.loads(result.stdout))

    @unittest.skipUnless(os.environ.get('COMPANY_WORKSPACE_TEST_RESTRICTED_TOKEN') == '1',
                         'Opt in outside the sandbox: one hidden disposable restricted child')
    def test_native_restricted_child_is_validated_before_resuming(self):
        probe = Path(self.temp.name) / 'restricted-child.ps1'
        probe.write_text('param([switch]$NormalTokenRelaunch,[string]$PythonCommand)\nexit 73\n', encoding='utf-8-sig')
        script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -Path SOURCE_PATH
$native=[CompanyAgent.WorkspaceNormalToken]
$flags=[Reflection.BindingFlags]'NonPublic,Static'
$source=$null; $restricted=$null
try {
    $process=$native.GetMethod('GetCurrentProcess',$flags).Invoke($null,@())
    $tokenArgs=[object[]]@($process,[uint32]139,$null)
    if (-not $native.GetMethod('OpenProcessToken',$flags).Invoke($null,$tokenArgs)) {throw 'Cannot open own token'}
    $source=$tokenArgs[2]
    $identity=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
    $restricted=$native.GetMethod('CreateRestrictedNormalToken',$flags).Invoke($null,@($source))
    $native.GetMethod('ValidateRestrictedCandidate',$flags).Invoke($null,@($restricted,$identity.UserSid,$identity.SessionId))
    $command=$native::BuildCommandLine(PROBE_PATH,'python',$false,$false,$null).Replace(' -NoLogo ',' -NoLogo -NoProfile -NonInteractive ')
    $code=$native.GetMethod('StartAndWait',$flags).Invoke($null,@($restricted,PS_PATH,$command,$identity.UserSid,$identity.SessionId,$true))
    @{exitCode=$code} | ConvertTo-Json -Compress
} finally {if($restricted){$restricted.Dispose()};if($source){$source.Dispose()}}
""".replace('SOURCE_PATH', ps_literal(SOURCE)).replace('PROBE_PATH', ps_literal(probe)).replace('PS_PATH', ps_literal(PS))
        result = subprocess.run([str(PS), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, encoding='utf-8', timeout=75)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'exitCode': 73}, json.loads(result.stdout))

    @unittest.skipUnless(os.environ.get('COMPANY_WORKSPACE_TEST_RESTRICTED_TOKEN') == '1',
                         'Opt in outside the sandbox: disposable copied-token DACL and Python descendants')
    def test_admin_only_default_dacl_copy_can_run_restricted_python_and_grandchild(self):
        fixture = ROOT / 'tests/fixtures/workspace_restricted_dacl.cs'
        native_dll = Path(self.temp.name) / 'restricted-native.dll'
        fixture_dll = Path(self.temp.name) / 'restricted-fixture.dll'
        child = Path(self.temp.name) / '제한 자식 & 검사.ps1'
        child_report = Path(self.temp.name) / '제한 자식 결과.json'
        host = Path(self.temp.name) / '복사 토큰 부모 & 검사.ps1'
        host_report = Path(self.temp.name) / '복사 토큰 부모 결과.json'
        python_probe = Path(self.temp.name) / '기본 모듈 & 검사.py'
        grandchild = Path(self.temp.name) / '한글 공백 & grandchild.py'
        marker = '한글 공백 경로 확인'
        grandchild.write_text(f'print({marker!r})\n', encoding='utf-8')
        python_probe.write_text(
            'import http.server, ssl, ctypes, subprocess, pathlib, threading, zipfile, urllib.request\n'
            'import json, sys\n'
            f'child = subprocess.run([sys.executable, "-B", "-X", "utf8", {str(grandchild)!r}], '
            'capture_output=True, text=True, encoding="utf-8", timeout=6)\n'
            'print(json.dumps({"version":list(sys.version_info[:3]), "modules":True, '
            '"grandchildExit":child.returncode, "grandchildOutput":child.stdout.strip()}, ensure_ascii=True))\n',
            encoding='utf-8',
        )
        child.write_text(r"""
param([switch]$NormalTokenRelaunch,[string]$PythonCommand,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
Add-Type -Path NATIVE_DLL
Add-Type -Path FIXTURE_DLL
$native=[CompanyAgent.WorkspaceNormalToken]
$token=$native::InspectCurrentToken()
$pipe=[WorkspaceRestrictedDaclFixture]::Pipe()
$self=[WorkspaceRestrictedDaclFixture]::SelfDuplicateAccess()
$probe=[WorkspaceRestrictedDaclFixture]::RedirectedPython($PythonCommand, PYTHON_PROBE)
@{normal=$native::ValidateNormalProcess($token,$token.UserSid,$token.SessionId);
  administrator=$token.IsAdministrator; elevated=$token.IsElevated; integrity=$token.IntegrityRid;
  pipeCode=$pipe; selfDuplicateCode=$self; python=$probe} |
  ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath REPORT_PATH -Encoding UTF8
exit 73
""".replace('NATIVE_DLL', ps_literal(native_dll)).replace('FIXTURE_DLL', ps_literal(fixture_dll))
            .replace('PYTHON_PROBE', ps_literal(python_probe)).replace('REPORT_PATH', ps_literal(child_report)),
            encoding='utf-8-sig')
        host_script = r"""
param([switch]$NormalTokenRelaunch,[string]$PythonCommand,[switch]$NoBrowser,[string]$StateRoot)
$ErrorActionPreference='Stop'
Add-Type -Path NATIVE_DLL
Add-Type -Path FIXTURE_DLL
$native=[CompanyAgent.WorkspaceNormalToken]
$flags=[Reflection.BindingFlags]'NonPublic,Static'
$source=$null; $restricted=$null; $result=@{}
try {
    $process=$native.GetMethod('GetCurrentProcess',$flags).Invoke($null,@())
    $tokenArgs=[object[]]@($process,[uint32]139,$null)
    if (-not $native.GetMethod('OpenProcessToken',$flags).Invoke($null,$tokenArgs)) {throw 'Cannot query fixture host token'}
    $source=$tokenArgs[2]
    # This is the test-created restricted host, not the original user's token.
    # The parent passes its TokenId only to reject accidental original mutation.
    [WorkspaceRestrictedDaclFixture]::AdminOnlyCopy($StateRoot,$source.DangerousGetHandle())
    $before=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
    $beforeDacl=[WorkspaceRestrictedDaclFixture]::DaclHash($source.DangerousGetHandle())
    $result.sourceHasUser=[WorkspaceRestrictedDaclFixture]::DaclHasUser($source.DangerousGetHandle())
    $result.sourcePipeCode=[WorkspaceRestrictedDaclFixture]::Pipe()
    $restricted=$native.GetMethod('CreateRestrictedNormalToken',$flags).Invoke($null,@($source))
    $native.GetMethod('ValidateRestrictedCandidate',$flags).Invoke($null,@($restricted,$before.UserSid,$before.SessionId))
    $result.restrictedHasUser=[WorkspaceRestrictedDaclFixture]::DaclHasUser($restricted.DangerousGetHandle())
    $command=$native::BuildCommandLine(CHILD_PATH,$PythonCommand,$false,$true,$null).Replace(' -NoLogo ',' -NoLogo -NoProfile -NonInteractive ')
    $result.childExit=$native.GetMethod('StartAndWait',$flags).Invoke($null,@($restricted,PS_PATH,$command,$before.UserSid,$before.SessionId,$true))
} catch {
    $failure=$_.Exception
    while($failure.InnerException){$failure=$failure.InnerException}
    $result.errorType=$failure.GetType().Name
    if($failure -is [ComponentModel.Win32Exception]){$result.nativeCode=$failure.NativeErrorCode}
    if($failure.PSObject.Properties['ReasonCode']){$result.reason=$failure.ReasonCode;$result.nativeCode=$failure.NativeErrorCode}
} finally {
    if($source) {
        $after=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
        $result.sourceUnchanged=(($before | ConvertTo-Json -Compress) -eq ($after | ConvertTo-Json -Compress))
        $result.sourceDaclUnchanged=$beforeDacl -eq [WorkspaceRestrictedDaclFixture]::DaclHash($source.DangerousGetHandle())
    }
    if($restricted){$restricted.Dispose()}; if($source){$source.Dispose()}
}
$result | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath HOST_REPORT -Encoding UTF8
exit 74
"""
        for name, path in {'NATIVE_DLL': native_dll, 'FIXTURE_DLL': fixture_dll, 'CHILD_PATH': child,
                           'PS_PATH': PS, 'HOST_REPORT': host_report}.items():
            host_script = host_script.replace(name, ps_literal(path))
        host.write_text(host_script, encoding='utf-8-sig')
        script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Add-Type -Path SOURCE_PATH -OutputAssembly NATIVE_DLL
Add-Type -Path NATIVE_DLL
Add-Type -Path FIXTURE_PATH -OutputAssembly FIXTURE_DLL
Add-Type -Path FIXTURE_DLL
$native=[CompanyAgent.WorkspaceNormalToken]
$flags=[Reflection.BindingFlags]'NonPublic,Static'
$source=$null; $synthetic=$null; $result=@{}
try {
    $process=$native.GetMethod('GetCurrentProcess',$flags).Invoke($null,@())
    # CreateRestrictedToken preserves handle access. ADJUST_DEFAULT is needed
    # only for the returned copy; no setter ever receives the source handle.
    $tokenArgs=[object[]]@($process,[uint32]139,$null)
    if (-not $native.GetMethod('OpenProcessToken',$flags).Invoke($null,$tokenArgs)) {throw 'Cannot query own token'}
    $source=$tokenArgs[2]
    $before=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
    $beforeDacl=[WorkspaceRestrictedDaclFixture]::DaclHash($source.DangerousGetHandle())
    $synthetic=$native.GetMethod('CreateRestrictedNormalToken',$flags).Invoke($null,@($source))
    $native.GetMethod('ValidateRestrictedCandidate',$flags).Invoke($null,@($synthetic,$before.UserSid,$before.SessionId))
    $originalId=[WorkspaceRestrictedDaclFixture]::TokenId($source.DangerousGetHandle())
    $result.originalSetterRejected=$false
    try {[WorkspaceRestrictedDaclFixture]::AdminOnlyCopy($originalId,$source.DangerousGetHandle())}
    catch {$result.originalSetterRejected=$true}
    $command=$native::BuildCommandLine(HOST_PATH,PYTHON_PATH,$false,$true,$originalId).Replace(' -NoLogo ',' -NoLogo -NoProfile -NonInteractive ')
    $result.hostExit=$native.GetMethod('StartAndWait',$flags).Invoke($null,@($synthetic,PS_PATH,$command,$before.UserSid,$before.SessionId,$true))
} catch {
    $failure=$_.Exception
    while($failure.InnerException){$failure=$failure.InnerException}
    $result.errorType=$failure.GetType().Name
    if($failure -is [ComponentModel.Win32Exception]){$result.nativeCode=$failure.NativeErrorCode}
    if($failure.PSObject.Properties['ReasonCode']){$result.reason=$failure.ReasonCode;$result.nativeCode=$failure.NativeErrorCode}
} finally {
    if($source) {
        $after=$native.GetMethod('ReadToken',$flags).Invoke($null,@($source))
        $result.sourceUnchanged=(($before | ConvertTo-Json -Compress) -eq ($after | ConvertTo-Json -Compress))
        $result.sourceDaclUnchanged=$beforeDacl -eq [WorkspaceRestrictedDaclFixture]::DaclHash($source.DangerousGetHandle())
    }
    if($synthetic){$synthetic.Dispose()}; if($source){$source.Dispose()}
}
$result | ConvertTo-Json -Depth 5 -Compress
"""
        for name, path in {'SOURCE_PATH': SOURCE, 'NATIVE_DLL': native_dll, 'FIXTURE_PATH': fixture,
                           'FIXTURE_DLL': fixture_dll, 'HOST_PATH': host, 'PYTHON_PATH': sys.executable,
                           'PS_PATH': PS}.items():
            script = script.replace(name, ps_literal(path))
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        result = subprocess.run([str(PS), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                capture_output=True, encoding='utf-8', timeout=80)
        self.assertEqual(0, result.returncode, result.stderr)
        evidence = json.loads(result.stdout)
        self.assertTrue(evidence['sourceUnchanged'], evidence)
        self.assertTrue(evidence['sourceDaclUnchanged'], evidence)
        self.assertNotIn('errorType', evidence, evidence)
        self.assertTrue(evidence['originalSetterRejected'], evidence)
        self.assertEqual(74, evidence.get('hostExit'), evidence)
        host_evidence = json.loads(host_report.read_text(encoding='utf-8-sig'))
        self.assertFalse(host_evidence['sourceHasUser'], host_evidence)
        self.assertEqual(5, host_evidence['sourcePipeCode'], host_evidence)
        self.assertTrue(host_evidence['sourceUnchanged'], host_evidence)
        self.assertTrue(host_evidence['sourceDaclUnchanged'], host_evidence)
        self.assertTrue(host_evidence['restrictedHasUser'], host_evidence)
        self.assertEqual(73, host_evidence.get('childExit'), host_evidence)
        observed = json.loads(child_report.read_text(encoding='utf-8-sig'))
        self.assertTrue(observed['normal'], observed)
        self.assertFalse(observed['administrator'], observed)
        self.assertFalse(observed['elevated'], observed)
        self.assertEqual(8192, observed['integrity'], observed)
        self.assertEqual(0, observed['pipeCode'], observed)
        self.assertEqual(0, observed['selfDuplicateCode'], observed)
        self.assertEqual('accepted', observed['python']['Status'], observed)
        python = json.loads(observed['python']['Output'])
        self.assertGreaterEqual(tuple(python['version']), (3, 11))
        self.assertTrue(python['modules'])
        self.assertEqual(0, python['grandchildExit'])
        self.assertEqual(marker, python['grandchildOutput'])

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
        self.assertIn(' -NoLogo -ExecutionPolicy Bypass -WindowStyle Hidden -File ', command)
        self.assertIn(' -NormalTokenRelaunch ', command)
        for forbidden in ('-NoProfile', '-Command', '-EncodedCommand', 'Set-ExecutionPolicy'):
            self.assertNotIn(forbidden, command)
        for optional in (' -Demo', ' -NoBrowser', ' -StateRoot'):
            self.assertIn(optional, command)
            self.assertNotIn(optional, self.result['minimalCommand'])


if __name__ == '__main__':
    unittest.main()
