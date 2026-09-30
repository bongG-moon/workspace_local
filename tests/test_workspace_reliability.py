"""Window reuse, title uniqueness, CLI failure recovery and imported identity."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid

from local_app.app_window import AppWindow, launch
from local_app.attention import AttentionNotifier
from local_app.bridge import ClaudeSession
from local_app.server import LocalApp, Server
from test_local_workspace import FAKE, eventually
from test_workspace_productivity_routes import PreparedBridge


class WindowReuseTests(unittest.TestCase):
    def test_repeated_icon_tray_and_notification_opens_activate_without_launch(self):
        native = Mock()
        native.set_visible.return_value = True
        opener = Mock()
        window = AppWindow(native, 'http://127.0.0.1:123/#token=fixture', opener=opener)
        for _ in range(5):
            self.assertEqual('activated', window.open()['action'])
        opener.assert_not_called()

    def test_concurrent_first_open_is_coalesced_and_a_destroyed_window_can_reopen(self):
        native = Mock()
        native.set_visible.return_value = False
        clock, calls = [10.0], []
        window = AppWindow(native, 'fixture', opener=lambda url:calls.append(url), clock=lambda:clock[0])
        threads = [threading.Thread(target=window.open) for _ in range(12)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(['fixture'], calls)
        native.set_visible.return_value = True
        window.open()
        native.set_visible.return_value = False
        window.open()
        self.assertEqual(2, len(calls))  # An observed HWND existed, then closed.

    def test_failed_launch_can_retry_without_false_pending(self):
        native = Mock(set_visible=Mock(return_value=False))
        opener = Mock(side_effect=[OSError('blocked'), {'mode':'app','browser':'Chrome'}])
        window = AppWindow(native, 'fixture', opener=opener)
        with self.assertRaises(OSError): window.open()
        self.assertEqual('Chrome', window.open()['browser'])

    def test_unbound_background_window_is_found_only_on_explicit_reopen(self):
        native = Mock()
        native.bind.return_value = None
        native.find.return_value = 'verified-hwnd'
        native.visibility.return_value = True
        notifier = AttentionNotifier(native=native)
        notifier.update({'revision':'one','items':[]})
        native.find.assert_not_called()
        self.assertTrue(notifier.set_visible(True))
        native.find.assert_called_once_with(notifier.window_title)
        native.visibility.assert_called_once_with('verified-hwnd', notifier.window_title, show=True)

    @unittest.skipUnless(os.name == 'nt', 'Windows app browser launch')
    def test_edge_failure_uses_chrome_app_mode_not_default_browser_tab(self):
        with patch('local_app.app_window.browser_candidates', return_value=[('Edge',Path('edge.exe')),('Chrome',Path('chrome.exe'))]), \
             patch('local_app.app_window.subprocess.Popen', side_effect=[OSError('blocked'),Mock()]) as popen, \
             patch('local_app.app_window.webbrowser.open') as fallback:
            self.assertEqual({'mode':'app','browser':'Chrome'}, launch('http://127.0.0.1:123/'))
            self.assertIn('--app=http://127.0.0.1:123/', popen.call_args.args[0])
            self.assertNotIn('--new-window', popen.call_args.args[0])
            fallback.assert_not_called()


class ReliabilityAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root/'work'
        self.work.mkdir()
        self.app = LocalApp(self.root/'state', command=FAKE, info={'version':'fixture'})
        self.addCleanup(self.app.close)

    def test_create_rename_and_first_prompt_titles_do_not_collide(self):
        a = self.app.create(str(self.work), True, title='보고서')
        b = self.app.create(str(self.work), True, title='보고서')
        c = self.app.create(str(self.work), True, title='보고서 (2)')
        self.assertEqual(['보고서','보고서 (2)','보고서 (3)'], [a['title'],b['title'],c['title']])
        self.assertEqual('보고서 (2)', self.app.update_session(b['id'], {'title':'보고서'})['title'])
        self.assertEqual(str(self.work.resolve()), self.app.get(b['id'])['workspace'])
        with patch('local_app.server.ClaudeSession', PreparedBridge):
            first = self.app.create(str(self.work), True)
            second = self.app.create(str(self.work), True)
            self.app.send(first['id'],'같은 질문',[])
            self.app.send(second['id'],'같은 질문',[])
        self.assertEqual(['같은 질문','같은 질문 (2)'],[self.app.get(x['id'])['title'] for x in (first,second)])

    def test_title_suffix_handles_double_digits_and_length_without_hydration(self):
        for i in range(12):
            self.app.create(str(self.work), True, title='가'*100)
        titles=[row['title'] for row in self.app.sessions.values()]
        self.assertEqual(12,len(set(titles)))
        self.assertTrue(all(len(value)<=100 for value in titles))
        self.assertTrue(titles[-1].endswith(' (12)'))
        with self.app.lock:
            self.assertTrue(self.app.unique_title(titles[-1]).endswith(' (13)'))

    def test_import_adds_same_session_and_followup_keeps_original_uuid_after_reload(self):
        sid=str(uuid.uuid4()); task=str(uuid.uuid4())
        record={'id':task,'sessionId':sid,'title':'이전 업무','workspace':str(self.work.resolve()),
                'created':1.0,'updated':2.0,'messages':[{'role':'user','text':'기존 맥락'}]}
        with patch.object(self.app,'import_sessions',return_value=record):
            item=self.app.import_session(sid)['session']
            self.assertFalse(item['trusted'])
            self.assertEqual(task,self.app.import_session(sid)['session']['id'])
        self.app.close()
        self.app=LocalApp(self.root/'state',command=FAKE,info={'version':'fixture'})
        self.addCleanup(self.app.close)
        self.app.get(task)['trusted']=True
        self.app.send(task,'STREAM_PROTOCOL_TEST',[])
        eventually(lambda:self.app.get(task)['state']=='done')
        self.assertEqual(sid,self.app.get(task)['sessionId'])
        self.assertIn('--resume='+sid,self.app.get(task)['bridge'].process.args)
        self.assertEqual('기존 맥락',self.app.get(task)['messages'][0]['text'])
        self.assertEqual(str(self.work.resolve()),self.app.get(task)['workspace'])

    def test_authenticated_reopen_route_and_metadata_do_not_change_work(self):
        server=Server(self.app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))
        callback=Mock(return_value={'ok':True,'action':'activated'})
        self.app._open_window_callback=callback
        url=server.origin+'/api/window/open'
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(url,data=b'{}',headers={'Content-Type':'application/json'}))
        self.assertEqual(403,caught.exception.code)
        callback.assert_not_called()
        with urlopen(Request(url,data=b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+self.app.token})) as response:
            self.assertEqual('activated',json.load(response)['action'])
        self.assertEqual({},self.app.sessions)
        self.assertTrue(self.app.bootstrap()['window']['reopenSupported'])


class DiagnosticRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.bridge=ClaudeSession([],{},Path('.'),lambda kind,data:self.events.append((kind,data)))
        self.addCleanup(self.bridge.close)

    def test_diagnostic_is_failure_without_raw_assistant_text_or_automatic_replay(self):
        sid=str(uuid.uuid4());self.bridge.session_id=sid;self.bridge.busy=True
        self.bridge.pending={'pending':{'tool_name':'Bash'}};self.bridge.tasks={'worker'}
        self.bridge.handle({'type':'result','is_error':True,'session_id':sid,
                            'result':'[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use'})
        self.assertFalse(self.bridge.busy)
        self.assertEqual({},self.bridge.pending);self.assertEqual(set(),self.bridge.tasks)
        self.assertFalse(any(kind in {'result','assistant'} for kind,_ in self.events))
        error=next(data for kind,data in self.events if kind=='error')
        self.assertEqual('cli_turn_incomplete',error['code']);self.assertEqual(sid,error['resumeSessionId'])
        self.assertNotIn('last_content_type',error['message']);self.assertIsNone(self.bridge.process)
        self.bridge.handle({'type':'result','is_error':False,'session_id':sid,'result':'후속 답변'})
        self.assertTrue(any(kind=='result' for kind,_ in self.events))

    def test_delegated_result_never_finishes_or_replaces_parent_identity(self):
        sid=str(uuid.uuid4());self.bridge.session_id=sid;self.bridge.busy=True
        self.bridge.handle({'type':'result','parent_tool_use_id':'worker','session_id':str(uuid.uuid4()),'is_error':True,'result':'[ede_diagnostic]'})
        self.assertEqual(sid,self.bridge.session_id);self.assertTrue(self.bridge.busy);self.assertEqual([],self.events)

    def test_imported_resume_rejects_cli_silently_starting_different_session(self):
        sid=str(uuid.uuid4())
        bridge=ClaudeSession([],{},Path('.'),lambda kind,data:self.events.append((kind,data)),sid,require_resume_identity=True)
        self.addCleanup(bridge.close)
        bridge.handle({'type':'system','subtype':'init','session_id':str(uuid.uuid4())})
        self.assertTrue(bridge.closed);self.assertEqual(sid,bridge.session_id)
        self.assertEqual('resume_identity',self.events[-1][1]['code'])

    def test_mcp_control_projects_status_without_secrets_and_keeps_missing_tools_unknown(self):
        def write(frame):
            self.assertEqual('mcp_status',frame['request']['subtype'])
            self.bridge.handle({'type':'control_response','response':{'subtype':'success','request_id':frame['request_id'],
                'response':{'mcpServers':[{'name':'docs','status':'connected','config':{'headers':{'secret':'never-copy'}}}]}}})
        with patch.object(self.bridge,'_write',side_effect=write):
            self.bridge._read_runtime_inventory()
        result=self.bridge.connection_state()
        self.assertTrue(result['reported']['mcp']);self.assertFalse(result['reported']['tools'])
        self.assertEqual([{'name':'docs','status':'connected'}],result['mcp'])
        self.assertNotIn('never-copy',json.dumps(result))

    def test_unsupported_inventory_response_never_means_empty_reported_list(self):
        def write(frame):
            self.bridge.handle({'type':'control_response','response':{'subtype':'error','request_id':frame['request_id'],'error':'unsupported'}})
        with patch.object(self.bridge,'_write',side_effect=write):self.bridge._read_runtime_inventory()
        self.assertFalse(self.bridge.connection_state()['reported']['mcp'])


if __name__=='__main__':unittest.main()
