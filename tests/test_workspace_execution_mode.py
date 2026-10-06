"""Inherited execution roles and safe cooperative app replacement."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.execution_mode import checked_mode, checked_request, EXECUTION_MODE_PROTOCOL
from local_app.server import LocalApp, Server, WORKSPACE_VERSION
from local_app import upgrade_launcher as launcher
from tests.test_workspace_upgrade_launcher import FakeClient, RID

ROOT = Path(__file__).resolve().parents[1]


class ExecutionModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = LocalApp(self.root / 'state/demo', demo=True)
        self.app._upgrade_headless = True
        self.addCleanup(self.app.close)

    def test_auto_is_a_launch_assertion_and_not_a_persisted_role(self):
        self.assertEqual('auto', checked_request('auto'))
        for value in (None, True, '', 'bypass', 'administrator;whoami'):
            with self.assertRaises(ValueError):
                checked_request(value)

    def test_stale_app_preference_is_preserved_but_cannot_enable_administrator_role(self):
        path = self.app.state / 'execution-mode.json'
        path.write_bytes(b'{"schemaVersion":1,"mode":"administrator"}')
        before = path.read_bytes()
        other = LocalApp(self.app.state, demo=True, execution_mode='normal')
        self.addCleanup(other.close)
        self.assertEqual('normal', other.execution_mode)
        self.assertEqual(before, path.read_bytes())

    def test_settings_cannot_start_a_privilege_transition(self):
        server = Server(self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),worker.join(2)))
        data = json.dumps({'mode':'administrator'}).encode()
        req = Request(server.origin+'/api/execution-mode', data=data,
                      headers={'Authorization':'Bearer '+self.app.token,'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as failure:
            urlopen(req, timeout=3)
        self.assertIn(failure.exception.code, (400, 404, 409))
        self.assertFalse(self.app.shutdown_status()['closing'])
        self.assertIsNone(self.app.upgrade.pending)

    def test_only_explicit_modes_are_accepted(self):
        for value in (None, True, '', 'auto', 'bypass', 'administrator;whoami'):
            with self.assertRaises(ValueError):
                checked_mode(value)
        self.assertEqual('administrator', checked_mode('administrator'))

    def test_same_role_upgrade_waits_for_work_and_binds_target_until_drafts_saved(self):
        self.app._upgrade_headless = False  # This case owns a visible draft capture.
        sid = self.app.create(str(self.root), True)['id']
        item = self.app.get(sid); item['state'] = 'running'
        data = {'action':'prepare','requestId':RID,'targetVersion':WORKSPACE_VERSION,'executionMode':'normal'}
        status = self.app.upgrade.action(data)
        self.assertEqual('waiting', status['upgrade']['stage'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        with self.assertRaises(ValueError):
            self.app.upgrade.action({**data,'executionMode':'administrator'})
        item['state'] = 'idle'
        status = self.app.upgrade.status()
        draft = {'sessionId':sid,'drafts':[{'id':sid,'text':'계속할 입력','attachments':['C:\\work\\data.csv']}]}
        self.app.upgrade.action({'action':'capture','requestId':RID,'revision':status['upgrade']['revision'],'snapshot':draft})
        with self.assertRaises(ValueError):
            self.app.upgrade.action({'action':'commit','requestId':RID,'executionMode':'administrator'})
        restored = json.loads(self.app.upgrade.path.read_text(encoding='utf-8'))
        self.assertEqual(draft, restored['snapshot'])
        self.app.upgrade.action({'action':'cancel','requestId':RID})
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_same_role_reuses_same_and_newer_version_without_downgrade(self):
        for current in ('0.21.5','99.0.0'):
            client=FakeClient()
            client.health.update(workspaceVersion=current,executionMode='administrator',executionModeProtocol=EXECUTION_MODE_PROTOCOL)
            with patch.object(launcher,'wait_shutdown',return_value=True):
                result=launcher.transfer(client,self.app.state/'runtime.json',demo=False,target_version='0.21.5',
                    request_id=RID,legacy_confirm=lambda:False,execution_mode='administrator',sleep=lambda _:None)
            self.assertEqual('reused',result)
            self.assertFalse(any(data and data.get('action')=='prepare' for _,data in client.calls))

    def test_other_role_never_reopens_or_attempts_cooperative_shutdown(self):
        for current, requested in (('normal','administrator'),('administrator','normal')):
            client=FakeClient()
            client.health.update(workspaceVersion='0.21.5',executionMode=current,executionModeProtocol=EXECUTION_MODE_PROTOCOL)
            with self.assertRaisesRegex(launcher.HandoffError,'완전 종료'):
                launcher.transfer(client,self.app.state/'runtime.json',demo=False,target_version='0.21.5',
                    request_id=RID,legacy_confirm=lambda:False,execution_mode=requested,sleep=lambda _:None)
            self.assertEqual([('/api/bootstrap',None)],client.calls)

    def test_handoff_cannot_prepare_a_different_windows_role(self):
        with self.assertRaisesRegex(ValueError,'Windows'):
            self.app.upgrade.action({'action':'prepare','requestId':RID,
                'targetVersion':WORKSPACE_VERSION,'executionMode':'administrator'})
        self.assertIsNone(self.app.upgrade.pending)
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_previous_same_version_without_mode_support_is_replaced_cooperatively(self):
        client=FakeClient();client.health['workspaceVersion']='0.21.5'
        with patch.object(launcher,'wait_shutdown',return_value=True):
            result=launcher.transfer(client,self.app.state/'runtime.json',demo=False,target_version='0.21.5',
                request_id=RID,legacy_confirm=lambda:False,execution_mode='normal',sleep=lambda _:None)
        self.assertEqual('closed',result)

    def test_old_patch_same_mode_is_replaced_and_future_patch_is_not_downgraded(self):
        client=FakeClient();client.health.update(workspaceVersion='0.21.5',executionMode='normal',executionModeProtocol=1)
        self.assertTrue(launcher.checked_health(client,demo=False,target_version='0.21.5',execution_mode='normal')[1])
        client.health['executionModeProtocol']=EXECUTION_MODE_PROTOCOL
        self.assertFalse(launcher.checked_health(client,demo=False,target_version='0.21.5',execution_mode='normal')[1])
        client.health['executionModeProtocol']=EXECUTION_MODE_PROTOCOL+1
        self.assertFalse(launcher.checked_health(client,demo=False,target_version='0.21.5',execution_mode='normal')[1])

    def test_medium_client_cannot_execute_high_app_even_with_connection_token(self):
        self.app.execution_mode='administrator'
        self.app._execution_peer=Mock()
        self.app._execution_peer.check=Mock(return_value=False)
        server=Server(self.app);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),worker.join(2)))
        def request(path,data=None):
            req=Request(server.origin+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+self.app.token,'Content-Type':'application/json'})
            try:response=urlopen(req,timeout=3)
            except HTTPError as error:response=error
            with response:return response.status,json.load(response)
        code,boot=request('/api/bootstrap')
        self.assertEqual(200,code);self.assertNotIn('sessions',boot)
        self.assertEqual(403,request('/api/execution-mode')[0])
        self.assertEqual(403,request('/api/quit',{})[0])
        self.assertEqual(403,request('/api/execution-mode',{'mode':'normal'})[0])
        self.assertFalse(self.app.shutdown_status()['closing'])


@unittest.skipUnless(os.name=='nt','Windows token validation')
class NativeExecutionModeTests(unittest.TestCase):
    def test_real_tcp_owner_and_current_medium_token_are_read_without_elevation(self):
        import socket
        from local_app.windows_peer import WindowsPeer
        peer=WindowsPeer()
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen()
        client=socket.create_connection(listener.getsockname());accepted,address=listener.accept()
        self.addCleanup(listener.close);self.addCleanup(client.close);self.addCleanup(accepted.close)
        self.assertEqual(os.getpid(),peer.owner(accepted.getsockname(),address))
        flags=peer.flags(os.getpid())
        self.assertTrue(flags['identity'])
        self.assertEqual(flags['elevated'] and flags['administrator'] and flags['integrity']==12288,
                         peer.check(accepted.getsockname(),address))

    def test_admin_guard_accepts_only_same_interactive_high_token_without_switching(self):
        helper = str(ROOT/'deploy/CompanyWorkspace.Startup.ps1').replace("'","''")
        source = ". '"+helper+"'; Initialize-WorkspaceNormalTokenApi; " + r"""
$sid='S-1-5-21-100-200-300-1001'
$rows=@()
foreach($params in @(@($sid,1,2,$true,12288,$true,$true),@($sid,1,1,$true,12288,$true,$true),@($sid,1,3,$false,8192,$false,$true),@('S-1-5-21-200',1,2,$true,12288,$true,$true),@($sid,0,2,$true,12288,$true,$true),@($sid,1,2,$true,8192,$true,$true))) {
 $token=New-Object CompanyAgent.WorkspaceTokenSnapshot -ArgumentList $params
 $rows += [CompanyAgent.WorkspaceNormalToken]::ValidateAdministratorProcess($token,$sid,1)
}
$normal=[pscustomobject]@{verified=$true;sid=$sid;sessionId=1;isAdministrator=$false}
$admin=[pscustomobject]@{verified=$true;sid=$sid;sessionId=1;isAdministrator=$true}
$mismatch=@(); foreach($pair in @(@($admin,'normal'),@($normal,'administrator'))) {try {Get-WorkspaceLaunchAction $pair[0] $false $pair[1]|Out-Null}catch{$mismatch+=$_.Exception.Message}}
@{guards=$rows;normal=(Get-WorkspaceLaunchAction $normal $false);admin=(Get-WorkspaceLaunchAction $admin $false);mismatch=$mismatch}|ConvertTo-Json -Compress
"""
        result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',source],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        value=json.loads(result.stdout)
        self.assertEqual([True,True,False,False,False,False],value['guards'])
        self.assertEqual(('run','run'),(value['normal'],value['admin']))
        self.assertEqual(['WORKSPACE_STARTUP:33']*2,value['mismatch'])

    def test_real_direct_start_inherits_verified_current_windows_role(self):
        from local_app.startup import verify_process
        from local_app.windows_peer import WindowsPeer
        flags = WindowsPeer().flags(os.getpid())
        expected = 'administrator' if flags['administrator'] and flags['elevated'] and flags['integrity'] == 0x3000 else 'normal'
        self.assertEqual(expected, verify_process())
