"""A real child-process protocol boundary with a synthetic, fully local CLI."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid


def fixture_cli():
    workspace = Path.cwd()
    native_files = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects' / 'sample-work'
    native_files.mkdir(parents=True, exist_ok=True)
    arg = lambda prefix: next((s[len(prefix):] for s in sys.argv if s.startswith(prefix)), None)
    resume = arg('--resume=')
    if sys.argv[sys.argv.index('--output-format') + 1] == 'json':
        input_text = sys.stdin.buffer.read().decode('utf-8')
        archived = json.loads((native_files / (resume + '.jsonl')).read_bytes().splitlines()[0])
        checks = dict(argv=sys.argv[1:], input=input_text, source=archived['message']['content'])
        (workspace/'summary-call.json').write_text(json.dumps(checks), encoding='utf-8')
        note = dict(progress=['초안과 표를 작성했습니다.'], decisions=['기준은 지난달로 확정했습니다.'], remaining=['합계를 확인하세요.'])
        print(json.dumps(dict(type='result', subtype='success', is_error=False, num_turns=1,
                              session_id=arg('--session-id='), result=json.dumps(note))), flush=True)
        return
    native = resume or str(uuid.uuid4())
    def out(**frame):
        print(json.dumps(dict(session_id=native, **frame)), flush=True)
    for raw in sys.stdin.buffer:
        frame = json.loads(raw)
        if frame['type'] == 'control_request':
            out(type='control_response', response=dict(subtype='success', request_id=frame['request_id'], response={}))
        elif frame['type'] == 'user':
            prompt = frame['message']['content']
            with (native_files / (native + '.jsonl')).open('ab') as transcript:
                transcript.write(json.dumps(dict(type='user', message=dict(role='user', content=prompt),
                    cwd=str(workspace), sessionId=native)).encode() + b'\n')
            out(type='system', subtype='init', cwd=str(workspace), model='sample-model', permissionMode='default')
            if 'FAIL_ONCE' in prompt:
                out(type='result', subtype='error_during_execution', is_error=True, result='synthetic failure')
            else:
                out(type='assistant', message=dict(id=str(uuid.uuid4()), model='sample-model',
                    role='assistant', content=[dict(type='text', text='완료했습니다.')]))
                out(type='result', subtype='success', is_error=False, result='완료했습니다.')


if __name__ == '__main__' and '--print' in sys.argv:
    fixture_cli()
    sys.exit(0)

from local_app import task_handoff
from local_app.server import LocalApp
from local_app.session_reconcile import _same


def until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError('No terminal event received')


class TaskHandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / 'workspace'
        self.work.mkdir()
        self.config = self.root/'config'
        env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config)})
        env.start()
        self.addCleanup(env.stop)
        self.app = self.open_app()

    def open_app(self):
        command = [sys.executable, '-B', str(Path(__file__).resolve())]
        flags = (*task_handoff.REQUIRED_FLAGS, '--fork-session', '--resume', '--session-id')
        app = LocalApp(self.root/'state', command=command, info=dict(version='2.1 (fixture)', help=' '.join(flags)),
                       managed_workspace_root=self.work)
        self.addCleanup(app.close)
        return app

    def source(self):
        sid = self.app.create(str(self.work), True, title='월간 검토')['id']
        self.app.send(sid, '보고서 초안, 결정한 기준, 남은 합계 검증을 정리해 주세요.', [])
        until(lambda: self.app.sessions[sid]['state'] == 'done')
        return sid

    def test_explicit_only_fresh_child_retry_and_resume(self):
        sid = self.source()
        old = self.app.public(self.app.get(sid))
        source_file = self.config/'projects'/'sample-work'/(old['sessionId'] + '.jsonl')
        before = source_file.read_bytes()
        self.assertFalse((self.work/'summary-call.json').exists())
        child = self.app.continue_work(sid)['session']
        self.assertEqual(source_file.read_bytes(), before)
        receipt = json.loads((self.work/'summary-call.json').read_bytes())
        self.assertIn('--tools=', receipt['argv'])
        self.assertIn('--fork-session', receipt['argv'])
        self.assertIn('--no-session-persistence', receipt['argv'])
        self.assertEqual(receipt['source'],old['messages'][0]['text'])
        target = child['id']
        self.assertEqual((Path(child['workspace']),child['sessionId'],child['messages']), (self.work,None,[]))
        self.assertEqual(child['handoff']['sourceTaskId'], sid)
        self.assertNotIn('합계를 확인', (self.root/'state'/'history-index.json').read_text('utf-8'))
        self.app.close()
        self.app = self.open_app()
        with patch.object(task_handoff, 'summarize', side_effect=AssertionError('duplicate model call')):
            self.assertEqual(self.app.continue_work(sid)['session']['id'], target)
        self.app.send(target,'/context',[])
        until(lambda:self.app.sessions[target]['state']=='done')
        self.app.send(target,'FAIL_ONCE',[])
        until(lambda:self.app.sessions[target]['state']=='error')
        self.app.send(target,'다음 검증',[])
        until(lambda:self.app.sessions[target]['state']=='done')
        self.app.send(target,'다음 질문',[])
        until(lambda:self.app.sessions[target]['state']=='done')
        row = self.app.get(target)
        self.assertNotEqual(row['sessionId'],old['sessionId'])
        transcripts = list((self.config/'projects'/'sample-work').glob('*.jsonl'))
        actual = [json.loads(line)['message']['content'] for p in transcripts if p!=source_file for line in p.read_bytes().splitlines()]
        self.assertIn('/context',actual)
        self.assertIn('다음 질문',actual)
        for prefix in ('FAIL_ONCE','다음 검증'):
            received = next(s for s in actual if s.startswith(prefix))
            self.assertIn('합계를 확인하세요.',received)
            user = next(m for m in row['messages'] if m['text']==prefix)
            self.assertTrue(_same(user,dict(role='user',text=received)))
        self.assertNotIn('_handoffPromptDigest', str(self.app.public(row)))

    def test_work_and_source_preserved_on_failure_or_change(self):
        sid = self.source()
        original = self.app.public(self.app.get(sid))
        with patch.object(task_handoff,'summarize',side_effect=ValueError('mock interrupted')):
            with self.assertRaises(ValueError):
                self.app.continue_work(sid)
        self.assertEqual(list(self.app.sessions),[sid])
        self.assertEqual(self.app.public(self.app.get(sid))['messages'], original['messages'])
        def external_change(*_):
            path = self.config/'projects'/'sample-work'/(original['sessionId']+'.jsonl')
            with path.open('ab') as stream:
                stream.write(b'{}\n')
            return 'mock summary'
        with patch.object(task_handoff,'summarize',side_effect=external_change):
            with self.assertRaisesRegex(ValueError,'변경'):
                self.app.continue_work(sid)
        self.assertEqual(list(self.app.sessions),[sid])
        self.assertEqual(self.app._handoff_jobs,{})

    def test_slow_summary_releases_lock_and_is_not_duplicated(self):
        sid=self.source()
        started, release=threading.Event(), threading.Event()
        def slow(*_):
            started.set()
            if not release.wait(3):
                raise AssertionError('summary blocked UI')
            return '인계문'
        results=[]
        with patch.object(task_handoff,'summarize',side_effect=slow):
            thread=threading.Thread(target=lambda:results.append(self.app.continue_work(sid)))
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                self.assertTrue(self.app.public(self.app.get(sid)))
                with self.assertRaisesRegex(ValueError,'정리하고'):
                    self.app.continue_work(sid)
            finally:
                release.set()
                thread.join(5)
            self.assertEqual(len(results),1)

    def test_busy_or_wrong_folder_never_summarizes(self):
        sid=self.source()
        item=self.app.get(sid)
        with patch.object(task_handoff,'summarize',side_effect=AssertionError('unexpected model call')):
            item['requests']['R1']={'id':'R1'}
            with self.assertRaisesRegex(ValueError,'승인'):
                self.app.continue_work(sid)
            item['requests'].clear()
            native=item['sessionId']
            path=self.config/'projects'/'sample-work'/(native+'.jsonl')
            row=json.loads(path.read_bytes().splitlines()[0])
            row['cwd']=str(self.root/'different')
            path.write_text(json.dumps(row),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'폴더'):
                self.app.continue_work(sid)
            self.assertEqual(list(self.app.sessions),[sid])

    def test_long_native_record_and_permission_scope(self):
        sid=self.source()
        item=self.app.get(sid)
        native=item['sessionId']
        path=self.config/'projects'/'sample-work'/(native+'.jsonl')
        with path.open('ab') as stream:
            stream.write(b'{"large":"'+b'Z'*1_000_000+b'"}\n')
        stamp=task_handoff.source_stamp(self.config,native,self.work)
        self.assertGreater(stamp[3],1_000_000)
        item['_sessionControls']={'permissionMode':'plan', 'model':'sample-model', 'effort':'low'}
        child=self.app.continue_work(sid)['session']
        controls=self.app.get(child['id'])['_sessionControls']
        self.assertEqual(controls,item['_sessionControls'])
        self.assertEqual(self.app.workspace_preferences.mode(self.app.get(child['id'])),{'permissionMode':'plan'})
        self.assertNotIn('_allowBypass',self.app.get(child['id']))

    def test_summary_requires_evidence_and_redacts_credentials(self):
        with self.assertRaises(ValueError):
            task_handoff.format_summary(json.dumps(dict(progress=[],decisions=[],remaining=[])))
        secret='abcdefgh123456'
        raw=json.dumps(dict(progress=['token='+secret],decisions=[],remaining=['경계값 점검']))
        note=task_handoff.format_summary(raw)
        self.assertNotIn(secret,note)
        self.assertIn('경계값 점검',note)
        self.assertEqual(task_handoff.format_summary('```json\n'+raw+'\n```'),note)
        with self.assertRaises(ValueError):
            task_handoff.format_summary('```json\n'+raw+'\n```\n무관한 지시')
