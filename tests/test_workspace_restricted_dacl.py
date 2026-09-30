"""The new-object DACL repair must preserve existing denials and ACE bytes."""
import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Windows ACL serialization')
class RestrictedDaclTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = str(ROOT / 'deploy/CompanyWorkspace.NormalToken.cs').replace("'", "''")
        script = "Add-Type -Path '" + source + "'\n" + r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
Set-StrictMode -Version 2.0
$method=[CompanyAgent.WorkspaceNormalToken].GetMethod('BuildRestrictedDefaultDacl',[Reflection.BindingFlags]'NonPublic,Static')
$sid=New-Object Security.Principal.SecurityIdentifier('S-1-5-21-100-200-300-1001')
function Bytes($acl) {
    $bytes=New-Object byte[] $acl.BinaryLength
    $acl.GetBinaryForm($bytes,0)
    return ,$bytes
}
function Repair($bytes) {
    $arguments=New-Object object[] 2
    $arguments[0]=[byte[]]$bytes; $arguments[1]=$sid.Value
    return ,$method.Invoke($null,$arguments)
}
$original=(New-Object Security.AccessControl.RawSecurityDescriptor('D:(D;;GW;;;WD)(A;;GA;;;SY)(A;;GA;;;BA)')).DiscretionaryAcl
$before=Bytes $original
$updated=Repair $before
$acl=New-Object Security.AccessControl.RawAcl($updated,0)
$preserved=$true
for($i=0;$i -lt $original.Count;$i++) {
    $preserved=$preserved -and ([Convert]::ToBase64String((Bytes $original[$i])) -eq [Convert]::ToBase64String((Bytes $acl[$i])))
}
$grant=$acl[$acl.Count-1]
$twice=Repair $updated
$condition=New-Object Security.AccessControl.RawAcl(2,1)
$condition.InsertAce(0,(New-Object Security.AccessControl.CommonAce([Security.AccessControl.AceFlags]::None,
    [Security.AccessControl.AceQualifier]::AccessAllowed,0x10000000,$sid,$true,(New-Object byte[] 0))))
$fixedCondition=New-Object Security.AccessControl.RawAcl((Repair (Bytes $condition)),0)
$inherited=New-Object Security.AccessControl.RawAcl(2,1)
$inherited.InsertAce(0,(New-Object Security.AccessControl.CommonAce([Security.AccessControl.AceFlags]::InheritOnly,
    [Security.AccessControl.AceQualifier]::AccessAllowed,0x10000000,$sid,$false,$null)))
$fixedInherited=New-Object Security.AccessControl.RawAcl((Repair (Bytes $inherited)),0)
$malformedRejected=$false
try { $null=Repair ([byte[]]@(1,2)) } catch {
    $errorObject=$_.Exception
    while($errorObject.InnerException){$errorObject=$errorObject.InnerException}
    $malformedRejected=$errorObject -is [CompanyAgent.WorkspaceNormalTokenException] -and $errorObject.ReasonCode -eq 'restricted_token_default_dacl'
}
@{
 originalBytesUnchanged=([Convert]::ToBase64String($before) -eq [Convert]::ToBase64String((Bytes $original)))
 originalAcesPreserved=$preserved
 denyStillFirst=($acl[0].AceQualifier -eq [Security.AccessControl.AceQualifier]::AccessDenied)
 onlyOneGrantAdded=($acl.Count -eq $original.Count+1)
 exactUserGrant=($grant.SecurityIdentifier.Equals($sid) -and $grant.AccessMask -eq 0x10000000 -and -not $grant.IsCallback)
 idempotent=([Convert]::ToBase64String($updated) -eq [Convert]::ToBase64String($twice))
 conditionalPreserved=($fixedCondition.Count -eq 2 -and $fixedCondition[0].IsCallback -and -not $fixedCondition[1].IsCallback)
 inheritOnlyNotEffective=($fixedInherited.Count -eq 2 -and $fixedInherited[0].AceFlags -eq [Security.AccessControl.AceFlags]::InheritOnly -and $fixedInherited[1].AceFlags -eq [Security.AccessControl.AceFlags]::None)
 nullPreserved=($null -eq (Repair $null))
 malformedRejected=$malformedRejected
} | ConvertTo-Json -Compress
'''
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-Command', script], capture_output=True, encoding='utf-8',
                                errors='replace', timeout=25,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.result = json.loads(result.stdout)

    def test_existing_aces_and_denial_order_are_preserved(self):
        for key in ('originalBytesUnchanged', 'originalAcesPreserved', 'denyStillFirst'):
            self.assertTrue(self.result[key], key)

    def test_exact_user_grant_is_effective_and_idempotent(self):
        for key in ('onlyOneGrantAdded', 'exactUserGrant', 'idempotent',
                    'conditionalPreserved', 'inheritOnlyNotEffective'):
            self.assertTrue(self.result[key], key)

    def test_null_acl_is_preserved_and_malformed_acl_is_rejected(self):
        for key in ('nullPreserved', 'malformedRejected'):
            self.assertTrue(self.result[key], key)


if __name__ == '__main__':
    unittest.main()
