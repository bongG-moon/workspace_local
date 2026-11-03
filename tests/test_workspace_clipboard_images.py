"""User-initiated image paste reuses bounded, session-owned attachments."""
import json
from pathlib import Path
import subprocess
import unittest

from tests.test_workspace_productivity_frontend import HARNESS as BASE_HARNESS, NODE, SETUP

ROOT = Path(__file__).resolve().parents[1]
HARNESS = BASE_HARNESS.replace(
    "const scenario=process.argv[3];",
    "context.File=require('node:buffer').File; const scenario=process.argv[3];",
)
PASTE_SETUP = r"""
const image=(type='image/png',name='image',bytes='synthetic image')=>new File([bytes],name,{type});
const eventFor=(files,extra={})=>({target:$('prompt'),defaultPrevented:false,
  preventDefault(){this.defaultPrevented=true;},
  clipboardData:{items:files.map(file=>({kind:'file',type:file.type,getAsFile:()=>file})),files,...extra}});
const paste=event=>$('prompt').listeners.paste[0](event);
const calls=[],bodies=[];
api=async(path,data)=>{calls.push({path,data});return {ok:true};};
fetch=async(path,options)=>{bodies.push({path,options});return {ok:true,json:async()=>({
  path:'C:/managed/'+decodeURIComponent(options.headers['X-File-Name']),copied:true})};};
boot.attachmentPolicy={extensions:['.png','.jpg','.webp'],maxAttachments:12,maxUploadBytes:52428800};
boot.chatAttachmentPolicy={extensions:['.png','.jpg','.webp'],maxAttachments:12,maxUploadBytes:16777216};
"""


@unittest.skipUnless(NODE, 'Node.js is required for clipboard event regressions')
class ClipboardImageTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, '-', str(ROOT / 'local_app/web/app.js'), SETUP + PASTE_SETUP + script,
             json.dumps([str(ROOT / 'local_app/web/attachments.js')])],
            input=HARNESS, capture_output=True, text=True, encoding='utf-8', timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_only_prompt_handles_images_and_text_paste_is_native(self):
        self.run_case(r"""(async()=>{
          assert.equal($('prompt').listeners.paste.length,1);
          assert.equal(document.listeners.paste,undefined);
          assert.equal($('composer').listeners?.paste,undefined);
          for(const data of [null,{items:[],files:[]},{items:[{kind:'string',type:'text/plain'},{kind:'string',type:'text/html'}],files:[]}]){
            const event=eventFor([]);event.clipboardData=data;await paste(event);
            assert.equal(event.defaultPrevented,false);
          }
          assert.equal(calls.length,0);assert.equal(bodies.length,0);
        })()""")

    def test_image_and_html_is_uploaded_once_with_draft_and_bytes_preserved(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='작성하던 질문';attachments=['C:/keep.txt'];
          const file=image(),event=eventFor([file]);
          event.clipboardData.items.push({kind:'string',type:'text/html'});
          await paste(event);
          assert.equal(event.defaultPrevented,true);assert.equal(calls.length,1);assert.equal(bodies.length,1);
          assert.equal(calls[0].path,'/api/attachments/prepare');assert.equal(calls[0].data.id,'A');
          const body=bodies[0].options.body;
          assert.match(body.name,/^붙여넣은 이미지_\d+_\d+\.png$/);assert.equal(body.type,'image/png');
          assert.equal(await body.text(),await file.text());assert.equal(body.size,file.size);
          assert.equal($('prompt').value,'작성하던 질문');assert.equal(attachments.length,2);
          assert.equal(drafts.get('A').text,'작성하던 질문');
          assert.deepEqual([...drafts.get('A').attachments],[...attachments]);
          assert.equal(WorkspaceAttachments.isCopy(attachments[1]),true);
          assert.equal(WorkspaceAttachments.isUploading(),false);
        })()""")

    def test_file_list_fallback_and_mime_determine_extension(self):
        self.run_case(r"""(async()=>{
          active.kind='chat';currentMode='chat';
          for(const [mime,extension] of [['image/jpeg','.jpg'],['image/webp','.webp']]){
            const file=image(mime,'incorrect.txt');const event=eventFor([file],{items:[]});
            await paste(event);assert.equal(event.defaultPrevented,true);
            assert.equal(bodies.at(-1).options.body.name.endsWith(extension),true);
            assert.equal(bodies.at(-1).options.body.type,mime);
          }
          assert.equal(attachments.length,2);assert.notEqual(attachments[0],attachments[1]);
        })()""")

    def test_unreadable_or_unsupported_image_has_notice_without_upload(self):
        self.run_case(r"""(async()=>{
          await paste(eventFor([image('image/gif')]));
          assert.match($('toast').textContent,/PNG, JPG, WebP/);
          const event=eventFor([],{items:[{kind:'file',type:'image/png',getAsFile:()=>null}]});
          await paste(event);assert.equal(event.defaultPrevented,true);
          assert.match($('toast').textContent,/가져오지 못/);assert.equal(calls.length,0);
        })()""")

    def test_limits_and_server_image_validation_keep_existing_input(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='보존할 질문';attachments=Array.from({length:12},(_,i)=>'C:/keep-'+i);saveDraft();
          await paste(eventFor([image()]));assert.match($('toast').textContent,/12개까지/);assert.equal(calls.length,0);
          attachments=[];boot.attachmentPolicy.maxUploadBytes=1;
          await paste(eventFor([image()]));assert.match($('toast').textContent,/파일 크기/);assert.equal(calls.length,0);
          active.kind='chat';currentMode='chat';
          api=async()=>{throw Error('이 형식은 4 MB까지 읽을 수 있어요.');};
          await paste(eventFor([image()]));assert.match($('toast').textContent,/4 MB/);
          assert.equal(bodies.length,0);assert.equal($('prompt').value,'보존할 질문');assert.equal(WorkspaceAttachments.isUploading(),false);
        })()""")

    def test_paste_during_upload_or_send_or_shutdown_does_not_start_another(self):
        self.run_case(r"""(async()=>{
          let release;api=()=>new Promise(resolve=>release=resolve);
          const first=paste(eventFor([image()]));assert.equal(WorkspaceAttachments.isUploading(),true);
          await paste(eventFor([image()]));assert.match($('toast').textContent,/전송이 끝난 뒤/);
          release({ok:true});await first;assert.equal(bodies.length,1);
          sending=true;await paste(eventFor([image()]));sending=false;
          appClosed=true;await paste(eventFor([image()]));appClosed=false;
          const event=eventFor([image()]);event.defaultPrevented=true;await paste(event);
          assert.equal(bodies.length,1);
        })()""")

    def test_first_chat_creation_reserves_upload_before_await(self):
        self.run_case(r"""(async()=>{
          active=null;currentMode='chat';let created,creations=0;
          ensureChatSession=async()=>{creations++;return new Promise(resolve=>created=resolve);};
          const first=paste(eventFor([image()]));assert.equal(WorkspaceAttachments.isUploading(),true);
          assert.equal($('send').disabled,true);
          await paste(eventFor([image()]));assert.equal(creations,1);assert.equal(calls.length,0);
          active={id:'NEW',kind:'chat',state:'idle'};selectionGeneration++;created(active);
          await first;assert.equal(calls[0].data.id,'NEW');assert.equal(bodies.length,1);
          assert.equal(drafts.get('NEW').attachments.length,1);assert.equal(WorkspaceAttachments.isUploading(),false);
        })()""")

    def test_real_empty_chat_creation_keeps_text_and_never_sends(self):
        self.run_case(r"""(async()=>{
          active=null;currentMode='chat';$('prompt').value='이 캡처를 설명해 줘';
          poll=()=>{};refreshInspector=()=>{};
          const owner={id:'NEW',kind:'chat',title:'새 대화',workspace:'C:/owned/chat',state:'idle',messages:[],trusted:true,seq:0};
          api=async(path,data)=>{calls.push({path,data});
            if(path==='/api/create')return {...owner};
            if(path.startsWith('/api/session?'))return {...owner};
            if(path==='/api/attachments/prepare')return {ok:true};
            return {};
          };
          await paste(eventFor([image()]));
          assert.equal(active.id,'NEW');assert.equal(active.kind,'chat');
          assert.equal(calls.filter(row=>row.path==='/api/create').length,1);
          assert.equal(calls.some(row=>row.path==='/api/send'),false);
          assert.equal(bodies.length,1);assert.match(bodies[0].path,/id=NEW/);
          assert.equal(drafts.get('NEW').text,'이 캡처를 설명해 줘');
          assert.equal(drafts.get('NEW').attachments.length,1);
          assert.equal($('prompt').value,'이 캡처를 설명해 줘');
          assert.equal(WorkspaceAttachments.isUploading(),false);
        })()""")

    def test_navigation_or_creation_failure_releases_pending_upload_without_crossing(self):
        self.run_case(r"""(async()=>{
          active=null;currentMode='chat';let created;
          ensureChatSession=()=>new Promise(resolve=>created=resolve);
          const pending=paste(eventFor([image()]));active={id:'B',kind:'work',state:'idle'};currentMode='work';selectionGeneration++;
          created(null);await pending;assert.equal(calls.length,0);assert.equal(WorkspaceAttachments.isUploading(),false);
          active=null;currentMode='chat';ensureChatSession=async()=>{throw Error('대화를 만들지 못했어요.');};
          await paste(eventFor([image()]));assert.match($('toast').textContent,/대화를 만들지 못/);
          assert.equal(WorkspaceAttachments.isUploading(),false);assert.equal(calls.length,0);
        })()""")

    def test_late_upload_stays_in_original_draft_after_switch(self):
        self.run_case(r"""(async()=>{
          $('prompt').value='A 질문';attachments=['C:/A-keep.txt'];saveDraft();
          let release;fetch=()=>new Promise(resolve=>release=resolve);
          const pending=paste(eventFor([image()]));await settle();
          active={id:'B',kind:'chat',state:'idle'};currentMode='chat';selectionGeneration++;
          $('prompt').value='B 질문';attachments=['C:/B-keep.txt'];
          release({ok:true,json:async()=>({path:'C:/managed/pasted.png',copied:true})});await pending;
          assert.equal($('prompt').value,'B 질문');assert.deepEqual([...attachments],['C:/B-keep.txt']);
          assert.equal(drafts.get('A').text,'A 질문');assert.deepEqual([...drafts.get('A').attachments],['C:/A-keep.txt','C:/managed/pasted.png']);
        })()""")

    def test_deleted_origin_does_not_reappear_and_partial_success_is_kept(self):
        self.run_case(r"""(async()=>{
          let release;fetch=()=>new Promise(resolve=>release=resolve);
          const pending=paste(eventFor([image()]));await settle();hiddenSessionIds.add('A');
          release({ok:true,json:async()=>({path:'C:/managed/discard.png',copied:true})});await pending;
          assert.equal(attachments.length,0);hiddenSessionIds.clear();
          let count=0;api=async()=>({ok:true});fetch=async()=>{
            if(++count===2)throw Error('network');
            return {ok:true,json:async()=>({path:'C:/managed/first.png',copied:true})};
          };
          await paste(eventFor([image(),image()]));assert.deepEqual([...attachments],['C:/managed/first.png']);
          assert.match($('toast').textContent,/1개 파일은 추가/);assert.equal(WorkspaceAttachments.isUploading(),false);
        })()""")


if __name__ == '__main__':
    unittest.main()
