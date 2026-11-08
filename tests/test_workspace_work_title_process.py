"""Exercise the separate title process through the same protocol and OS process boundary as production."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


def cli_fixture():
    work = Path.cwd()
    if sys.argv[sys.argv.index('--output-format')+1] == 'json':
        request = json.load(sys.stdin)
        log = {'argv':sys.argv[1:], 'request':request, 'pid':os.getpid(),
               'safe':os.environ.get('CLAUDE_CODE_SAFE_MODE')}
        (work/'title-process.json').write_text(json.dumps(log),encoding='utf8')
        if (work/'delay').exists():
            time.sleep(90)
        result = '설비 점검 지연 원인 분석'
        print(json.dumps({'type':'result','subtype':'success','is_error':False,'num_turns':1,'result':result}),flush=True)
        return
    import uuid
    native = str(uuid.uuid4())
    def out(**frame): print(json.dumps(dict(session_id=native,**frame)),flush=True)
    for raw in sys.stdin:
        frame = json.loads(raw)
        if frame['type']=='control_request':
            out(type='control_response',response={'subtype':'success','request_id':frame['request_id'],'response':{}})
        elif frame['type']=='user':
            out(type='system',subtype='init',cwd=str(work),model='init-alias',permissionMode='default')
            out(type='assistant',message={'id':str(uuid.uuid4()),'model':'actual-main',
                'role':'assistant','content':[{'type':'text','text':'MAIN-ANSWER-PRIVATE'}]})
            out(type='result',subtype='success',is_error=False,result='MAIN-ANSWER-PRIVATE')


if __name__=='__main__' and '--print' in sys.argv:
    cli_fixture()
    sys.exit(0)

from local_app import work_titles as titles
from local_app.server import LocalApp

COMMAND = [sys.executable,'-B',str(Path(__file__).resolve())]
INFO = {'version':'2.1.306 (fixture)','help':' '.join(titles.REQUIRED_FLAGS)+' --effort'}


def until(predicate,timeout=10):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for the test protocol peer')


class WorkTitleProcessTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.workspace=self.root/'workspace'
        self.workspace.mkdir()

    def app(self):
        app=LocalApp(self.root/'state',command=COMMAND,info=INFO,managed_workspace_root=self.root/'workspaces')
        self.addCleanup(app.close)
        return app

    def test_cli_result_names_first_work_using_actual_model_and_only_user_prose(self):
        app=self.app()
        sid=app.create(str(self.workspace),True)['id']
        (self.workspace/'reference.txt').write_text('ATTACHED-SECRET',encoding='utf8')
        prompt='설비 점검이 왜 늦어졌는지 살펴봐 줘.'
        app.send(sid,prompt,[str(self.workspace/'reference.txt')])
        until(lambda:app.sessions[sid]['title']=='설비 점검 지연 원인 분석')
        item=app.sessions[sid]
        self.assertEqual((item['state'],item['titleSource']),('done','generated'))
        log=json.loads((self.workspace/'title-process.json').read_bytes())
        self.assertEqual(log['request'],{'request':prompt})
        self.assertEqual(log['argv'][log['argv'].index('--model')+1],'actual-main')
        self.assertEqual(log['safe'],'1')
        for flag in ('--safe-mode','--tools=','--strict-mcp-config','--no-session-persistence','--disable-slash-commands','--no-chrome'):
            self.assertIn(flag,log['argv'])
        self.assertFalse(any('resume' in flag or 'bypass' in flag for flag in log['argv']))
        self.assertEqual([m['text'] for m in item['messages'] if m['role']=='user'],[prompt])
        self.assertIn('MAIN-ANSWER-PRIVATE',[m['text'] for m in item['messages'] if m['role']=='assistant'])
        before=log['pid'];app.send(sid,'두 번째 질문',[])
        until(lambda:item['state']=='done')
        self.assertEqual(json.loads((self.workspace/'title-process.json').read_bytes())['pid'],before)

    def test_explicit_name_never_launches_title_process(self):
        app=self.app();sid=app.create(str(self.workspace),True,title='수동 이름')['id']
        app.send(sid,'검토 요청',[]);until(lambda:app.sessions[sid]['state']=='done')
        self.assertEqual(app.sessions[sid]['title'],'수동 이름')
        self.assertFalse((self.workspace/'title-process.json').exists())

    def test_receipts_code_tables_and_attachments_are_not_inputs(self):
        raw=('〔표 인용 7〕 first.csv · 데이터 1행 · 2열\n{"행":[[1,["PRIVATE"]]]}\n'
             '지연 원인을 정리해 줘.\n```sql\nPRIVATE CODE\n```\n'
             '〔표 인용 2〕 old 형식\nPRIVATE | 원본\n'+'a,b,c,d\n'*90)
        self.assertEqual(titles.brief_question(raw),'지연 원인을 정리해 줘.')
        self.assertEqual(titles.brief_question('〔표 인용 1〕 file\n'+'X'*10000),'')
        self.assertEqual(titles.brief_question('ID,값\n실제고객,개인정보\n값 추이를 살펴봐 줘.'),'값 추이를 살펴봐 줘.')

    def test_unsupported_cli_and_incomplete_model_do_not_launch(self):
        with patch.object(titles,'run_owned') as runner:
            for info,model in [({},'actual-main'),(INFO,''),(INFO,'--danger')]:
                self.assertIsNone(titles.generate_title(COMMAND,info,self.workspace,model,'request',threading.Event()))
            runner.assert_not_called()

    def test_exact_softcamp_margins_allow_one_json_result_only(self):
        from subprocess import CompletedProcess
        noise=b'[0x7FFE1234] ANOMALY: meaningless REX prefix used'
        frame=json.dumps({'type':'result','subtype':'success','is_error':False,
                          'num_turns':1,'result':'설비 점검 지연 분석'}).encode()
        for raw, expected in [(noise+frame+noise,'설비 점검 지연 분석'),
                              (noise+b'\r\n'+frame+b'\n'+noise,'설비 점검 지연 분석'),
                              (frame+noise+frame,None), (b'warning '+frame,None),
                              (frame+b' unknown warning',None),(noise+frame[:-1]+noise,None)]:
            with self.subTest(raw=raw[:16],expected=expected), patch.object(titles,'run_owned',
                    return_value=CompletedProcess([],0,stdout=raw)):
                self.assertEqual(titles.generate_title(COMMAND,INFO,self.workspace,'main','질문',threading.Event()),expected)

    def test_owned_child_timeout_and_host_close(self):
        (self.workspace/'delay').touch()
        with patch.object(titles,'TITLE_TIMEOUT',.35):
            started=time.monotonic()
            self.assertIsNone(titles.generate_title(COMMAND,INFO,self.workspace,'actual-main','질문',threading.Event()))
            self.assertLess(time.monotonic()-started,6)
        app=self.app();sid=app.create(str(self.workspace),True)['id']
        (self.workspace/'title-process.json').unlink(missing_ok=True)
        app.send(sid,'질문',[])
        until(lambda:(self.workspace/'title-process.json').exists())
        self.assertEqual(app.sessions[sid]['state'],'done')
        started=time.monotonic()
        self.assertTrue(app.close())
        self.assertLess(time.monotonic()-started,9)
        self.assertEqual(app.work_titles.active,{})
        self.assertFalse(app.work_titles.workers)

    def test_disconnect_cancels_only_this_tasks_title_job(self):
        app=self.app()
        (self.workspace/'delay').touch()
        sid=app.create(str(self.workspace),True)['id']
        app.send(sid,'첫 요청',[]);until(lambda:(self.workspace/'title-process.json').exists())
        app.stop(sid,disconnect=True)
        until(lambda:not app.work_titles.active,timeout=6)
        self.assertEqual(app.sessions[sid]['title'],'새 업무')

    def test_bounded_workers_drop_cancelled_queue_and_invalid_results(self):
        entered=threading.Event()
        started=[];release=threading.Event();applied=[]
        def helper(command,info,workspace,model,question,cancel,**unused):
            started.append(question)
            if len(started)==2:entered.set()
            release.wait(3)
            return None if cancel.is_set() else '짧은 업무 제목'
        jobs=titles.WorkTitles(lambda *args:True,lambda sid,*args:applied.append(sid))
        self.addCleanup(jobs.close)
        with patch.object(titles,'generate_title',side_effect=helper):
            for n in range(4):
                jobs.offer(str(n),'first',COMMAND,INFO,self.workspace,'main',str(n))
            self.assertTrue(entered.wait(2))
            jobs.cancel('0');jobs.cancel('2');release.set()
            until(lambda:not jobs.workers)
        self.assertCountEqual(applied,['1','3'])


if __name__=='__main__': unittest.main()
