import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_app.execution_mode import checked_mode, save_mode, EXECUTION_MODE_PROTOCOL
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

    def test_only_explicit_modes_are_accepted(self):
        for value in (None, True, '', 'auto', 'bypass', 'administrator;whoami'):
            with self.assertRaises(ValueError):
                checked_mode(value)
        self.assertEqual('administrator', checked_mode('administrator'))

    def test_preference_is_app_owned_and_not_saved_before_success(self):
        personal = self.root / 'claude-settings.json'
        personal.write_bytes(b'personal credentials and model: unchanged')
        before = personal.read_bytes()
        with patch.object(self.app.execution, 'thread_factory') as thread:
            self.app.execution.start('administrator')
            thread.return_value.start.assert_called_once()
        self.assertFalse((self.app.state / 'execution-mode.json').exists())
        save_mode(self.app.state, 'administrator')
        self.assertEqual({'schemaVersion':1,'mode':'administrator'}, json.loads((self.app.state / 'execution-mode.json').read_text()))
        self.assertEqual(before, personal.read_bytes())

    def test_launch_is_single_and_preserves_python_state_demo_and_target(self):
        controller = self.app.execution
        controller.runner = Mock(return_value=Mock(returncode=0))
        with patch.object(self.app.execution, 'thread_factory'):
            result = controller.start('administrator')
            request = controller.pending
            with self.assertRaises(ValueError):
                controller.start('administrator')
        self.assertEqual('normal', result['current'])
        controller._launch(request)
        args = controller.runner.call_args.args[0]
        self.assertEqual('administrator', args[args.index('-ExecutionMode')+1])
        self.assertEqual(str(self.app.state.parent), args[args.index('-StateRoot')+1])
        self.assertIn('-Demo',args); self.assertIn('-NoBrowser',args)
        self.assertNotIn('token', ' '.join(args))
        self.assertEqual('waiting', controller.snapshot()['state'])

    def test_failed_launch_leaves_old_app_work_and_preference_intact(self):
        sid = self.app.create(str(self.root), True)['id']
        item = self.app.get(sid); item['state'] = 'running'
        save_mode(self.app.state, 'normal')
        preference = (self.app.state / 'execution-mode.json').read_bytes()
        controller = self.app.execution
        controller.runner = Mock(return_value=Mock(returncode=35))
        with patch.object(self.app.execution, 'thread_factory'):
            controller.start('administrator')
        controller._launch(controller.pending)
        self.assertEqual('idle', controller.snapshot()['state'])
        self.assertIsNotNone(controller.snapshot()['error'])
        self.assertEqual('running', item['state'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        self.assertEqual(preference, (self.app.state / 'execution-mode.json').read_bytes())

    def test_coordinator_failure_is_observed_while_original_launcher_is_still_requesting(self):
        sid = self.app.create(str(self.root), True)['id']
        item = self.app.get(sid); item['state'] = 'running'
        with patch.object(self.app.execution, 'thread_factory'):
            self.app.execution.start('administrator')
        request = self.app.execution.pending
        self.app.upgrade.action({'action':'prepare', 'requestId':request['requestId'],
                                'targetVersion':WORKSPACE_VERSION, 'executionMode':'administrator'})
        (self.app.state/'upgrade-last-result.json').write_text(json.dumps({'requestId':request['requestId'], 'status':'failed'}))
        result = self.app.execution.snapshot()
        self.assertEqual('idle', result['state'])
        self.assertEqual('cancelled', self.app.upgrade.pending['stage'])
        self.assertIsNotNone(result['error'])
        self.assertEqual('running', item['state'])
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_unrelated_result_cannot_cancel_current_permission_request(self):
        with patch.object(self.app.execution, 'thread_factory'):
            self.app.execution.start('administrator')
        request = self.app.execution.pending
        (self.app.state/'upgrade-last-result.json').write_text(json.dumps({'requestId':'0'*32, 'status':'failed'}))
        self.assertEqual('requesting', self.app.execution.snapshot()['state'])
        self.assertIs(request, self.app.execution.pending)
        (self.app.state/'upgrade-last-result.json').write_text('[]')
        self.assertEqual('requesting', self.app.execution.snapshot()['state'])

    def test_permission_status_distinguishes_work_wait_from_draft_capture(self):
        self.app._upgrade_headless = False
        sid = self.app.create(str(self.root), True)['id']
        item = self.app.get(sid); item['state'] = 'running'
        with patch.object(self.app.execution, 'thread_factory'):
            self.app.execution.start('administrator')
        request = self.app.execution.pending
        self.app.upgrade.action({'action':'prepare', 'requestId':request['requestId'],
                                'targetVersion':WORKSPACE_VERSION, 'executionMode':'administrator'})
        self.assertEqual('waiting_for_work', self.app.execution.snapshot()['phase'])
        item['state'] = 'idle'
        self.app.upgrade.status()
        self.assertEqual('preserving_drafts', self.app.execution.snapshot()['phase'])

    def test_mode_change_waits_for_work_and_binds_target_until_drafts_saved(self):
        self.app._upgrade_headless = False  # This case owns a visible draft capture.
        sid = self.app.create(str(self.root), True)['id']
        item = self.app.get(sid); item['state'] = 'running'
        data = {'action':'prepare','requestId':RID,'targetVersion':WORKSPACE_VERSION,'executionMode':'administrator'}
        status = self.app.upgrade.action(data)
        self.assertEqual('waiting', status['upgrade']['stage'])
        self.assertFalse(self.app.shutdown_status()['closing'])
        with self.assertRaises(ValueError):
            self.app.upgrade.action({**data,'executionMode':'normal'})
        item['state'] = 'idle'
        status = self.app.upgrade.status()
        draft = {'sessionId':sid,'drafts':[{'id':sid,'text':'계속할 입력','attachments':['C:\\work\\data.csv']}]}
        self.app.upgrade.action({'action':'capture','requestId':RID,'revision':status['upgrade']['revision'],'snapshot':draft})
        with self.assertRaises(ValueError):
            self.app.upgrade.action({'action':'commit','requestId':RID,'executionMode':'normal'})
        restored = json.loads(self.app.upgrade.path.read_text(encoding='utf-8'))
        self.assertEqual(draft, restored['snapshot'])
        self.app.upgrade.action({'action':'cancel','requestId':RID})
        self.assertFalse(self.app.shutdown_status()['closing'])

    def test_same_version_mode_change_transfers_but_never_downgrades_newer_app(self):
        for current, mode, expected in (('0.21.5','normal','closed'),('0.21.5','administrator','reused'),('99.0.0','normal','reused')):
            client=FakeClient()
            client.health.update(workspaceVersion=current,executionMode=mode,executionModeProtocol=EXECUTION_MODE_PROTOCOL)
            with patch.object(launcher,'wait_shutdown',return_value=True):
                result=launcher.transfer(client,self.app.state/'runtime.json',demo=False,target_version='0.21.5',
                    request_id=RID,legacy_confirm=lambda:False,execution_mode='administrator',sleep=lambda _:None)
            self.assertEqual(expected,result)
            if expected=='closed':
                prepare=[data for _,data in client.calls if data and data.get('action')=='prepare'][0]
                self.assertEqual('administrator',prepare['executionMode'])

    def test_administrator_to_normal_uses_same_restart_protocol(self):
        client=FakeClient(); client.health.update(workspaceVersion='0.21.5',executionMode='administrator',executionModeProtocol=EXECUTION_MODE_PROTOCOL)
        with patch.object(launcher,'wait_shutdown',return_value=True):
            result=launcher.transfer(client,self.app.state/'runtime.json',demo=False,target_version='0.21.5',
                request_id=RID,legacy_confirm=lambda:False,execution_mode='normal',sleep=lambda _:None)
        self.assertEqual('closed',result)
        commits=[data for _,data in client.calls if data and data.get('action')=='commit']
        self.assertEqual(1,len(commits)); self.assertEqual('normal',commits[0]['executionMode'])

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
        self.assertFalse(launcher.checked_health(client,demo=False,target_version='0.21.5',execution_mode='administrator')[1])

    def test_api_requires_auth_and_valid_mode_and_reading_never_restarts(self):
        server=Server(self.app); worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),worker.join(2)))
        def request(data=None, auth=True):
            headers={'Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer '+self.app.token
            req=Request(server.origin+'/api/execution-mode',data=None if data is None else json.dumps(data).encode(),headers=headers)
            try:response=urlopen(req,timeout=3)
            except HTTPError as error:response=error
            with response:return response.status,json.load(response)
        with patch.object(self.app.execution, 'thread_factory') as launch:
            self.assertEqual(403,request(auth=False)[0])
            self.assertEqual('normal',request()[1]['current'])
            self.assertEqual(400,request({'mode':'bypass'})[0])
            self.assertEqual(400,request({'mode':'administrator','extra':True})[0])
            launch.assert_not_called()
            self.assertEqual(200,request({'mode':'administrator'})[0])
            launch.return_value.start.assert_called_once()

    def test_normal_successor_keeps_coordinator_high_until_old_app_closes(self):
        self.app.execution_mode='administrator'
        controller=self.app.execution
        request={'requestId':RID,'mode':'normal','state':'requesting','acceptedAt':controller.clock()}
        controller.pending=request
        def spawn(args,**kwargs):
            self.assertEqual('administrator',args[args.index('--execution-mode')+1])
            self.assertEqual('normal',args[args.index('--target-execution-mode')+1])
            (self.app.state/('upgrade-launch-'+RID+'.json')).write_text(json.dumps({'requestId':RID,'status':'accepted'}))
            return Mock(poll=Mock(return_value=None))
        controller.coordinator_popen=Mock(side_effect=spawn)
        controller._launch(request)
        self.assertEqual('waiting',controller.pending['state'])
        controller.coordinator_popen.assert_called_once()

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
    def test_elevation_launcher_can_exit_while_its_successor_is_still_running(self):
        # Replace only the UAC boundary. The real helper waits for a disposable
        # normal launcher whose child stays alive; no administrator is started.
        with tempfile.TemporaryDirectory(prefix='workspace elevation ack ') as raw:
            root = Path(raw)
            parent = root/'parent.py'; child = root/'child.py'
            pid_path = root/'child-pid.txt'; release = root/'release.txt'
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            child.write_text("import time\nfrom pathlib import Path\ndeadline=time.monotonic()+45\nwhile not Path(" + repr(str(release)) + ").exists() and time.monotonic()<deadline: time.sleep(.1)\n", encoding='utf-8')
            powershell = str(Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
            parent.write_text("import subprocess,sys\nfrom pathlib import Path\np=subprocess.Popen([sys.executable," + repr(str(child)) + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW)\nPath(" + repr(str(pid_path)) + ").write_text(str(p.pid),encoding='ascii')\nsys.exit(17)\n", encoding='utf-8')
            source = ". " + quote(ROOT/'deploy/CompanyWorkspace.Startup.ps1') + r"""
function Start-Process {
 param($FilePath,$ArgumentList,$Verb,$WindowStyle,[switch]$PassThru,[switch]$Wait,$ErrorAction)
 if($Verb -ne 'RunAs' -or $WindowStyle -ne 'Hidden' -or -not $PassThru) { throw 'Incorrect elevation boundary' }
 $parentArguments=@(('"'+PARENT+'"'))
 if($Wait) { return Microsoft.PowerShell.Management\Start-Process -FilePath PYTHON -ArgumentList $parentArguments -WindowStyle Hidden -PassThru -Wait }
 return Microsoft.PowerShell.Management\Start-Process -FilePath PYTHON -ArgumentList $parentArguments -WindowStyle Hidden -PassThru
}
$code=Invoke-WorkspaceAdministratorRelaunch -ScriptPath PARENT -PythonCommand 'fixture.exe' -StateRoot STATE -ExecutionRequestId '1234567890abcdef1234567890abcdef' -Demo $true -NoBrowser $true
$childId=[int](Get-Content -LiteralPath CHILD_PID)
@{code=$code;childAlive=$null -ne (Get-Process -Id $childId -ErrorAction SilentlyContinue)}|ConvertTo-Json -Compress
"""
            for marker, value in [('PARENT',parent),('PYTHON',sys.executable),('STATE',root),('CHILD_PID',pid_path)]:
                source = source.replace(marker,quote(value))
            try:
                result = subprocess.run([powershell,'-NoLogo','-NoProfile','-NonInteractive','-Command',source],capture_output=True,text=True,encoding='utf-8',timeout=20)
                self.assertEqual(0,result.returncode,result.stderr)
                self.assertEqual({'code':17,'childAlive':True},json.loads(result.stdout))
            finally:
                release.touch()
                if pid_path.exists():
                    import ctypes
                    from ctypes import wintypes
                    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
                    kernel.OpenProcess.restype=wintypes.HANDLE
                    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
                    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
                    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
                    handle=kernel.OpenProcess(0x100000,False,int(pid_path.read_text().strip()))
                    if handle:
                        try:kernel.WaitForSingleObject(handle,10000)
                        finally:kernel.CloseHandle(handle)

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

    def test_admin_guard_accepts_only_same_interactive_high_token_and_policy_can_switch(self):
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
@{guards=$rows;normal=(Get-WorkspaceLaunchAction $admin $false 'normal');admin=(Get-WorkspaceLaunchAction $admin $false 'administrator');elevate=(Get-WorkspaceLaunchAction $normal $false 'administrator')}|ConvertTo-Json -Compress
"""
        result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',source],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        value=json.loads(result.stdout)
        self.assertEqual([True,True,False,False,False,False],value['guards'])
        self.assertEqual(('relaunch','run','elevate'),(value['normal'],value['admin'],value['elevate']))
