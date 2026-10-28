import hashlib
import io
import json
from pathlib import Path
import uuid
from unittest.mock import Mock

import pytest
from local_app.server import LocalApp
from local_app.extensions import ExtensionHost, ExtensionError


def test_upload_copies_bytes_and_revokes_old_selection(tmp_path):
    host=ExtensionHost(tmp_path/'state');host._verified=Mock()
    try:
        first=host.receive_publish_file('test.store','skill.md',5,io.BytesIO(b'hello'))
        context=host.file_context('test.store',first['fileToken'],'inspectPublishSource')
        assert Path(context['selectedFile']).read_bytes()==b'hello'
        second=host.receive_publish_file('test.store','skill.md',5,io.BytesIO(b'world'))
        assert first['fileToken']!=second['fileToken']
        with pytest.raises(ExtensionError):host.file_context('test.store',first['fileToken'],'inspectPublishSource')
        for name in ('../secret.md','C:\\secret.md','CON.md','bad.exe:stream'):
            with pytest.raises(ExtensionError):host.receive_publish_file('test.store',name,1,io.BytesIO(b'x'))
        with pytest.raises(ExtensionError):host.receive_publish_file('test.store','short.zip',10,io.BytesIO(b'x'))
    finally:host.close()


def test_install_request_preserves_busy_work_draft_controls_and_is_idempotent(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        created=app.create(managed=True,trusted=True,title='기존 업무');sid=created['id']
        item=app.get(sid);item['state']='running';item['_sessionControls']={'permissionMode':'manual','model':'chosen-model'}
        before_drafts=app.drafts.snapshot()
        def download(extension,action,payload,context):
            p=Path(context['installRequestDirectory'])/'tool.zip';p.write_bytes(b'fixture')
            return {'ok':True,'result':{'file':str(p),'sha256':hashlib.sha256(b'fixture').hexdigest(),'name':'내 도구','guide':'설치 안내'}}
        app.extensions.action=Mock(side_effect=download)
        data={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True}
        first=app.extension_tasks.request('test.store',data)
        assert first['requested'] and not first['installed']
        assert app.extension_tasks.request('test.store',data)==first
        assert app.extensions.action.call_count==1
        queue=app.dispatch.snapshot(sid)['queue']
        assert len(queue)==1 and queue[0]['status']=='queued'
        assert '공통 공간' in queue[0]['text'] and '내 도구' in queue[0]['text']
        assert item['state']=='running' and item['_sessionControls']['permissionMode']=='manual'
        assert app.drafts.snapshot()==before_drafts
        with pytest.raises(ValueError):app.extension_tasks.request('test.store',{**data,'scope':'work'})
    finally:app.close()


def test_untrusted_work_never_downloads_or_grants_trust(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True)['id'];item=app.get(sid)
        item['trusted']=False;app._validate_remembered_trust=Mock()
        app.extensions.action=Mock()
        with pytest.raises(ValueError,match='접근'):
            app.extension_tasks.request('test.store',{'requestId':str(uuid.uuid4()),'scope':'work','sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True})
        app.extensions.action.assert_not_called()
        assert item['trusted'] is False
    finally:app.close()


def test_legacy_thousand_requests_migrate_and_preserve_idempotency(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True,title='업무')['id'];app.get(sid)['state']='running'
        old={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True}
        binding=hashlib.sha256(json.dumps(['test.store',old],sort_keys=True).encode()).hexdigest()
        old_result={'requested':True,'installed':False,'sessionId':sid,'notice':'원래 결과'}
        legacy={str(uuid.uuid4()):{'binding':'a'*64,'sessionId':sid,'result':old_result,'artifact':{'guide':'오래된 내용'}} for _ in range(999)}
        legacy[old['requestId']]={'binding':binding,'sessionId':sid,'result':old_result,'artifact':{'guide':'오래된 내용'}}
        original=json.dumps(legacy,ensure_ascii=False).encode()
        app.extension_tasks.path.write_bytes(original)
        def download(extension,action,payload,context):
            p=Path(context['installRequestDirectory'])/'tool.zip';p.write_bytes(b'fixture')
            return {'ok':True,'result':{'file':str(p),'sha256':hashlib.sha256(b'fixture').hexdigest(),'name':'도구'}}
        app.extensions.action=Mock(side_effect=download)
        assert app.extension_tasks.request('test.store',old)==old_result
        app.extensions.action.assert_not_called()
        new={**old,'requestId':str(uuid.uuid4())}
        result=app.extension_tasks.request('test.store',new)
        assert result['requested'] and not result['installed']
        assert app.extension_tasks.request('test.store',new)==result
        assert app.extensions.action.call_count==1
        assert app.extension_tasks.path.read_bytes()==original
        import sqlite3
        with sqlite3.connect(app.extension_tasks.database) as db:
            assert db.execute('SELECT count(*) FROM requests').fetchone()[0]==1001
            saved=json.loads(db.execute('SELECT value FROM requests WHERE id=?',(old['requestId'],)).fetchone()[0])
            assert 'artifact' not in saved and saved['result']==old_result
        with pytest.raises(ValueError,match='변경'):
            app.extension_tasks.request('test.store',{**old,'scope':'work'})
    finally:app.close()


def test_failed_download_request_can_resume_without_losing_binding(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True)['id'];app.get(sid)['state']='running'
        data={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True}
        app.extensions.action=Mock(return_value={'ok':False,'error':'다운로드 실패'})
        with pytest.raises(ValueError,match='다운로드 실패'):app.extension_tasks.request('test.store',data)
        with pytest.raises(ValueError,match='변경'):app.extension_tasks.request('test.store',{**data,'scope':'work'})
        def download(extension,action,payload,context):
            p=Path(context['installRequestDirectory'])/'tool.zip';p.write_bytes(b'fixture')
            return {'ok':True,'result':{'file':str(p),'sha256':hashlib.sha256(b'fixture').hexdigest()}}
        app.extensions.action=Mock(side_effect=download)
        result=app.extension_tasks.request('test.store',data)
        assert result['requested'] and Path(result['file']).is_file()
        assert app.extension_tasks.request('test.store',data)==result
        assert app.extensions.action.call_count==1
    finally:app.close()


def test_legacy_file_changes_after_downgrade_are_merged_without_replay(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True)['id']
        data={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True}
        # Initialize the new store first, then emulate a request saved by an older app.
        app.extension_tasks._open_records().close()
        binding=hashlib.sha256(json.dumps(['test.store',data],sort_keys=True).encode()).hexdigest()
        result={'requested':True,'installed':False,'sessionId':sid,'notice':'이전 버전 결과'}
        app.extension_tasks.path.write_text(json.dumps({data['requestId']:{'binding':binding,'sessionId':sid,'result':result}}),encoding='utf-8')
        app.extensions.action=Mock()
        assert app.extension_tasks.request('test.store',data)==result
        app.extensions.action.assert_not_called()
    finally:app.close()


def test_corrupt_legacy_record_is_preserved_and_not_silently_ignored(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True)['id']
        data={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{},'confirmed':True}
        app.extension_tasks.path.write_text('{broken',encoding='utf-8')
        with pytest.raises(ValueError):app.extension_tasks.request('test.store',data)
        assert app.extension_tasks.path.read_text(encoding='utf-8')=='{broken'
    finally:app.close()


def test_legacy_import_is_atomic_when_record_is_invalid(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True)['id']
        data={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{},'confirmed':True}
        binding=hashlib.sha256(json.dumps(['test.store',data],sort_keys=True).encode()).hexdigest()
        result={'requested':True,'installed':False,'sessionId':sid}
        entries={data['requestId']:{'binding':binding,'sessionId':sid,'result':result},'bad':None}
        app.extension_tasks.path.write_text(json.dumps(entries),encoding='utf-8')
        with pytest.raises(ValueError):app.extension_tasks.request('test.store',data)
        import sqlite3
        with sqlite3.connect(app.extension_tasks.database) as db:
            assert db.execute('SELECT count(*) FROM requests').fetchone()[0]==0
        del entries['bad']
        app.extension_tasks.path.write_text(json.dumps(entries),encoding='utf-8')
        assert app.extension_tasks.request('test.store',data)==result
    finally:app.close()


def test_interruption_after_enqueue_reuses_queue_receipt_without_redownload(tmp_path):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True)['id'];app.get(sid)['state']='running'
        data={'requestId':str(uuid.uuid4()),'scope':'user','sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True}
        def download(extension,action,payload,context):
            p=Path(context['installRequestDirectory'])/'tool.zip';p.write_bytes(b'fixture')
            return {'ok':True,'result':{'file':str(p),'sha256':hashlib.sha256(b'fixture').hexdigest()}}
        app.extensions.action=Mock(side_effect=download)
        enqueue=app.dispatch.action
        def interrupted(session,payload):
            enqueue(session,payload)
            raise RuntimeError('simulated interruption after durable enqueue')
        app.dispatch.action=interrupted
        with pytest.raises(RuntimeError,match='simulated interruption'):
            app.extension_tasks.request('test.store',data)
        assert len(app.dispatch.snapshot(sid)['queue'])==1
        app.dispatch.action=enqueue
        result=app.extension_tasks.request('test.store',data)
        assert result['requested'] and Path(result['file']).is_file()
        assert len(app.dispatch.snapshot(sid)['queue'])==1
        assert app.extensions.action.call_count==1
    finally:app.close()


@pytest.mark.parametrize('scope', ['user', 'work'])
@pytest.mark.parametrize('waiting', [False, True])
def test_idle_install_dispatches_once_but_preserves_explicit_queue_pause(tmp_path, scope, waiting):
    app=LocalApp(tmp_path/'state',demo=True)
    try:
        sid=app.create(managed=True,trusted=True,title='설치 진행 업무')['id'];item=app.get(sid)
        item['_sessionControls']={'permissionMode':'manual'}
        if waiting:
            app.dispatch.action(sid,{'action':'enqueue','text':'사용자가 보류한 요청','attachments':[]})
            app.dispatch.queue.pause(sid)
        def download(extension,action,payload,context):
            p=Path(context['installRequestDirectory'])/'tool.zip';p.write_bytes(b'fixture')
            return {'ok':True,'result':{'file':str(p),'sha256':hashlib.sha256(b'fixture').hexdigest(),'name':'내 도구'}}
        app.extensions.action=Mock(side_effect=download)
        def send(*args,**kwargs):
            item['state']='running';item['lastRunId']='fixture-run'
        app.send=Mock(side_effect=send)
        data={'requestId':str(uuid.uuid4()),'scope':scope,'sessionId':sid,'action':'prepareInstallRequest','payload':{'toolId':1},'confirmed':True}
        result=app.extension_tasks.request('test.store',data)
        assert result['paused'] is waiting
        app.dispatch.pump();app.dispatch.pump()
        assert app.send.call_count==(0 if waiting else 1)
        assert app.extension_tasks.request('test.store',data)==result
        assert app.extensions.action.call_count==1
        app.dispatch.pump()
        assert app.send.call_count==(0 if waiting else 1)
        assert item['_sessionControls']=={'permissionMode':'manual'}
        if not waiting:
            assert ('공통 공간' if scope=='user' else '이 업무 폴더') in app.send.call_args.args[1]
    finally:app.close()


def test_authenticated_binary_upload_route(tmp_path):
    import json
    import threading
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from local_app.server import Server
    from test_workspace_extensions import package
    package(tmp_path)
    app=LocalApp(tmp_path/'state',demo=True);server=Server(app)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/api/extensions/example.tools/upload'
    try:
        headers={'Authorization':'Bearer '+app.token,'Content-Type':'application/octet-stream','X-File-Name':'fixture.md'}
        with urlopen(Request(url,b'# fixture',headers,method='POST')) as response:
            result=json.load(response)
        assert result['size']==9 and len(result['fileToken'])==43
        context=app.extensions.file_context('example.tools',result['fileToken'],'inspectPublishSource')
        assert Path(context['selectedFile']).read_bytes()==b'# fixture'
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(url,b'x',{**headers,'Authorization':'Bearer invalid'},method='POST'))
        assert denied.value.code==403
    finally:server.shutdown();server.server_close();app.close()
