"""Run the shipped picker with delayed directory responses, without a browser."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ids=new Map();
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.parent=null;this.attributes={};this.dataset={};this.isConnected=true;this.disabled=false;this.value='';this.textContent='';this.className='';this.classList={toggle:(name,enabled)=>{const values=new Set(this.className.split(' '));enabled?values.add(name):values.delete(name);this.className=[...values].join(' ');},contains:name=>this.className.split(' ').includes(name)};}
 set id(value){this._id=value;ids.set(value,this);}get id(){return this._id;}
 append(...children){for(const n of children){n.parent=this;this.children.push(n);}}
 replaceChildren(...children){this.children=[];this.append(...children);}
 setAttribute(name,value){this.attributes[name]=String(value);}
 showModal(){this.open=true;}close(){this.open=false;this.onclose?.();}
 focus(){context.document.activeElement=this;}scrollIntoView(){}
}
const all=node=>[node,...node.children.flatMap(all)];
const context={assert,console,AbortController,Map,Set,setTimeout,clearTimeout,document:{body:new Element('body'),activeElement:null},
 el:(tag,text,cls)=>{const n=new Element(tag);n.textContent=text??'';n.className=cls||'';return n;},basename:path=>String(path).split(/[\\/]/).pop(),showDialog:id=>ids.get(id).showModal(),api:async()=>{},
 nodes:()=>all(context.document.body),byClass:cls=>all(context.document.body).find(n=>n.classList.contains(cls)),byLabel:label=>all(context.document.body).find(n=>n.attributes['aria-label']===label),byText:text=>all(context.document.body).find(n=>n.textContent===text),
 flush:()=>new Promise(resolve=>setImmediate(resolve)),
 result:(path,entries=[],extra={})=>({path,parent:'C:/Desktop',breadcrumbs:[{name:'Desktop',path:'C:/Desktop'},{name:path.split('/').pop(),path}],shortcuts:[{name:'바탕화면',path:'C:/Desktop'}],entries,limited:false,...extra}),
 file:(name,path='C:/Desktop')=>({name,path:path+'/'+name,kind:'file',size:12}),folder:(name,path='C:/Desktop')=>({name,path:path+'/'+name,kind:'folder',size:null}),
 finish:()=>context.byClass('send-button').onclick(),
 listing:handler=>async(route,data,signal)=>data?.action==='select'?{paths:data.paths}:handler(route,data,signal)};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
setImmediate(async()=>{try{await vm.runInContext(process.argv[3],context);}catch(error){console.error(error.stack||error);process.exitCode=1;}});
"""


@unittest.skipUnless(NODE, 'Node.js required for picker interaction tests')
class PathPickerFrontendTests(unittest.TestCase):
    def case(self, script):
        result=subprocess.run([NODE,'-',str(ROOT/'local_app/web/path-picker.js'),script],input=HARNESS,
                              text=True,encoding='utf-8',capture_output=True,timeout=10)
        self.assertEqual(0,result.returncode,result.stderr or result.stdout)

    def test_cancel_and_escape_leave_draft_unmodified_and_return_empty(self):
        self.case(r"""(async()=>{
          let resolve,signal;api=(route,data,s)=>{assert.equal(route,'/api/browse-paths');signal=s;return new Promise(r=>resolve=r);};
          const draft={text:'keep prompt',files:['old.csv']},opened=WorkspacePathPicker.open({kind:'files'});
          assert.equal(byClass('send-button').disabled,true);let prevented=false;
          byClass('path-picker').oncancel({preventDefault(){prevented=true;}});
          assert.equal((await opened).paths.length,0);assert.equal(signal.aborted,true);assert.equal(prevented,true);
          resolve(result('C:/late',[file('late.csv')]));await flush();
          assert.equal(byClass('path-picker').open,false);assert.equal(draft.text,'keep prompt');assert.equal(draft.files[0],'old.csv');
        })()""")

    def test_folder_apply_only_after_successful_listing_no_trust_or_creation_call(self):
        self.case(r"""(async()=>{
          let calls=[];api=async(route,data)=>{calls.push({route,data});return data.action==='select'?{paths:data.paths}:result('C:/Desktop/folder');};
          const opened=WorkspacePathPicker.open({kind:'folder',initialDirectory:'C:/Desktop/folder/not-created'});await flush();
          assert.equal(byClass('send-button').textContent,'이 폴더 선택');assert.equal(byClass('send-button').disabled,false);
          finish();assert.equal((await opened).paths[0],'C:/Desktop/folder');
          assert.equal(calls.length,2);assert.equal(calls[1].data.action,'select');assert.equal(calls[0].route,'/api/browse-paths');assert.equal(calls[0].data.initial,true);
        })()""")

    def test_files_selection_across_folders_deduplicated_and_explicit_apply(self):
        self.case(r"""(async()=>{
          api=listing(async(route,data)=>result(data.path||'C:/Desktop',data.path==='C:/Desktop/sub'?[file('b.csv','C:/Desktop/sub')]:[file('a.csv'),folder('sub')]));
          const opened=WorkspacePathPicker.open({kind:'files'});await flush();
          byText('a.csv').parent.onclick();assert.equal(byClass('path-picker').open,true);
          byText('sub').parent.onclick();await flush();byText('b.csv').parent.onclick();
          assert.equal(byClass('send-button').textContent,'2개 자료 추가');finish();const value=await opened;
          assert.equal(value.paths.join('|'),'C:/Desktop/a.csv|C:/Desktop/sub/b.csv');
        })()""")

    def test_existing_attachments_count_toward_twelve_and_cannot_be_added_again(self):
        self.case(r"""(async()=>{
          api=listing(async()=>result('C:/Desktop',[file('old0.csv'),file('one.csv'),file('two.csv')]));
          const opened=WorkspacePathPicker.open({kind:'files',existingPaths:Array.from({length:11},(_,i)=>'c:\\desktop\\old'+i+'.csv')});await flush();
          assert.equal(byText('old0.csv').parent.disabled,true);byText('one.csv').parent.onclick();byText('two.csv').parent.onclick();
          assert.match(byClass('path-picker-message').textContent,/12개/);assert.equal(byClass('send-button').textContent,'1개 자료 추가');
          finish();assert.equal((await opened).paths.join('|'),'C:/Desktop/one.csv');
        })()""")

    def test_stale_navigation_response_cannot_replace_newer_directory(self):
        self.case(r"""(async()=>{
          let first,second;api=listing((route,data)=>new Promise(resolve=>{if(!first)first=resolve;else second=resolve;}));
          const opened=WorkspacePathPicker.open({kind:'folder',initialDirectory:'C:/first'});
          byLabel('폴더 전체 경로').value='D:/second';byClass('path-picker-location').onsubmit({preventDefault(){}});
          second(result('D:/second'));await flush();first(result('C:/first'));await flush();
          assert.equal(byLabel('폴더 전체 경로').value,'D:/second');finish();assert.equal((await opened).paths[0],'D:/second');
        })()""")

    def test_error_does_not_offer_previous_folder_as_new_selection(self):
        self.case(r"""(async()=>{
          let calls=0;api=async()=>{if(calls++)throw Error('권한이 없어요');return result('C:/good');};
          const opened=WorkspacePathPicker.open({kind:'folder'});await flush();
          byLabel('폴더 전체 경로').value='C:/denied';byClass('path-picker-location').onsubmit({preventDefault(){}});await flush();
          assert.equal(byClass('send-button').disabled,true);assert.equal(byClass('path-picker-message').textContent,'권한이 없어요');
          byText('취소').onclick();assert.equal((await opened).paths.length,0);
        })()""")

    def test_native_picker_only_on_explicit_action_cancellation_keeps_styled_dialog(self):
        self.case(r"""(async()=>{
          let calls=[];api=async(route)=>{calls.push(route);return route==='/api/pick'?{paths:[]}:result('C:/Desktop');};
          const opened=WorkspacePathPicker.open({kind:'files'});await flush();assert.equal(calls.join('|'),'/api/browse-paths');
          await byText('Windows 탐색기로 선택').onclick();assert.equal(byClass('path-picker').open,true);assert.match(byClass('path-picker-message').textContent,/취소/);
          assert.equal(calls.join('|'),'/api/browse-paths|/api/pick');byText('취소').onclick();await opened;
        })()""")

    def test_native_result_is_ignored_after_cancel_and_reopen(self):
        self.case(r"""(async()=>{
          let nativeReply;api=async(route)=>route==='/api/pick'?new Promise(resolve=>nativeReply=resolve):result('C:/Desktop');
          const old=WorkspacePathPicker.open({kind:'files'});await flush();const pending=byText('Windows 탐색기로 선택').onclick();
          byText('취소').onclick();assert.equal((await old).paths.length,0);
          const fresh=WorkspacePathPicker.open({kind:'folder'});await flush();nativeReply({paths:['C:/old.csv']});await pending;
          assert.equal(byClass('path-picker').open,true);assert.equal(byClass('send-button').textContent,'이 폴더 선택');byText('취소').onclick();await fresh;
        })()""")

    def test_keyboard_navigation_and_select_remove_are_available_without_mouse(self):
        self.case(r"""(async()=>{
          api=async()=>result('C:/Desktop',[file('first.csv'),file('second.csv')]);
          const opened=WorkspacePathPicker.open({kind:'files'});await flush();const list=byClass('path-picker-list');
          list.children[0].focus();list.onkeydown({key:'ArrowDown',preventDefault(){}});assert.equal(document.activeElement.dataset.path,'C:/Desktop/second.csv');
          document.activeElement.onclick();assert.equal(byClass('send-button').disabled,false);byLabel('second.csv 선택 취소').onclick();assert.equal(byClass('send-button').disabled,true);
          list.onkeydown({key:'Home',preventDefault(){}});assert.equal(document.activeElement.dataset.path,'C:/Desktop/first.csv');byText('취소').onclick();await opened;
        })()""")

    def test_large_empty_and_duplicate_dialog_states_are_explained(self):
        self.case(r"""(async()=>{
          api=async()=>result('C:/Desktop',[],{limited:true});const opened=WorkspacePathPicker.open({kind:'files'});await flush();
          assert.match(byClass('path-picker-message').textContent,/일부 항목/);assert.equal(byClass('send-button').disabled,true);
          let message='';try{await WorkspacePathPicker.open({kind:'folder'});}catch(e){message=e.message;}assert.match(message,/이미 열린/);
          byText('취소').onclick();await opened;
        })()""")

    def test_executable_and_archive_selection_remains_passive_until_apply(self):
        self.case(r"""(async()=>{
          const calls=[];api=async(route,data)=>{calls.push({route,data});return data.action==='select'?{paths:data.paths}:result('C:/Desktop',[file('driver.exe'),file('sources.7z')]);};
          const opened=WorkspacePathPicker.open({kind:'files'});await flush();assert.match(byClass('path-picker-message').textContent,/압축·EXE/);
          byText('driver.exe').parent.onclick();byText('sources.7z').parent.onclick();assert.equal(calls.length,1);
          await finish();const value=await opened;assert.equal(value.paths.join('|'),'C:/Desktop/driver.exe|C:/Desktop/sources.7z');
          assert.equal(calls.length,2);assert.equal(calls[1].route,'/api/browse-paths');assert.equal(calls[1].data.action,'select');
        })()""")

    def test_apply_error_keeps_selection_and_dialog_for_correction(self):
        self.case(r"""(async()=>{
          api=async(route,data)=>{if(data.action==='select')throw Error('선택한 파일이 삭제됐어요');return result('C:/Desktop',[file('report.csv')]);};
          const opened=WorkspacePathPicker.open({kind:'files'});await flush();byText('report.csv').parent.onclick();
          await finish();assert.equal(byClass('path-picker').open,true);assert.match(byClass('path-picker-message').textContent,/삭제/);
          assert.equal(byClass('send-button').textContent,'1개 자료 추가');byLabel('report.csv 선택 취소').onclick();
          assert.equal(byClass('send-button').disabled,true);byText('취소').onclick();assert.equal((await opened).paths.length,0);
        })()""")

    def test_apply_late_reply_cannot_close_reopened_picker(self):
        self.case(r"""(async()=>{
          let applied;api=async(route,data)=>data.action==='select'?new Promise(resolve=>applied=()=>resolve({paths:data.paths})):result('C:/Desktop');
          const old=WorkspacePathPicker.open({kind:'folder'});await flush();const saving=finish();assert.equal(byClass('send-button').disabled,true);
          byText('취소').onclick();assert.equal((await old).paths.length,0);const fresh=WorkspacePathPicker.open({kind:'files'});await flush();
          applied();await saving;assert.equal(byClass('path-picker').open,true);assert.equal(byClass('send-button').textContent,'자료 추가');
          byText('취소').onclick();await fresh;
        })()""")


if __name__ == '__main__':
    unittest.main()
