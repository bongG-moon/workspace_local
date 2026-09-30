"""Behavioral checks for the dependency-free rich content DOM renderer."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
HARNESS = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element {
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attributes={};this.className='';this._text='';this.hidden=false;}
  get isConnected(){return this.root===true||!!this.parentNode?.isConnected;}
  set textContent(value){this.children.forEach(c=>c.parentNode=null);this.children=[];this._text=String(value);}
  get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}
  append(...nodes){for(let n of nodes){if(typeof n==='string')n={textContent:n};n.parentNode=this;this.children.push(n);}}
  replaceChildren(...nodes){this.children.forEach(n=>n.parentNode=null);this.children=[];this._text='';this.append(...nodes);}
  insertBefore(node,before){const at=this.children.indexOf(before);if(at<0)return this.append(node);node.parentNode=this;this.children.splice(at,0,node);}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.parentNode=null;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  removeAttribute(name){delete this.attributes[name];if(name==='src')delete this.src;}
}
const all=(node,test)=>{const found=[];for(const child of node.children||[]){if(test(child))found.push(child);found.push(...all(child,test));}return found;};
const tag=(node,name)=>all(node,n=>n.tagName===name.toUpperCase());
const cls=(node,name)=>all(node,n=>String(n.className||'').split(' ').includes(name));
const observers=[];
class Observer{constructor(callback){this.callback=callback;this.nodes=[];observers.push(this);}observe(node){this.nodes.push(node);}unobserve(node){this.nodes=this.nodes.filter(n=>n!==node);}disconnect(){this.nodes=[];}trigger(visible=true){this.callback(this.nodes.map(target=>({target,isIntersecting:visible})));}}
const parent=new Element();parent.root=true;
let clipboard='',opens=[],apiCalls=[],replies=[];
const context={assert,console,URL,Set,Map,encodeURIComponent,Element,all,tag,cls,parent,observers,active:{id:'A',workspace:'C:/work'},
document:{createElement:tag=>new Element(tag),createTextNode:text=>({textContent:String(text)})},
navigator:{clipboard:{writeText:async value=>{clipboard=value;}}},IntersectionObserver:Observer,
setTimeout:fn=>{return 1;},api:path=>{apiCalls.push(path);return new Promise((resolve,reject)=>replies.push({resolve,reject}));},
preview:path=>opens.push(path),readClipboard:()=>clipboard,opens,apiCalls,replies,
flush:async()=>{for(let i=0;i<8;i++)await Promise.resolve();}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
context.R=context.WorkspaceRichContent;
(async()=>{try{await vm.runInContext(process.argv[3],context);}catch(e){console.error(e.stack||e);process.exitCode=1;}})();
"""


@unittest.skipUnless(NODE, "Node.js is required for frontend behavior tests")
class WorkspaceRichContentFrontendTests(unittest.TestCase):
    def run_case(self, script):
        result = subprocess.run(
            [NODE, "-", str(ROOT / "local_app/web/rich-content.js"), script],
            input=HARNESS, text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_markdown_and_code_never_interpret_untrusted_html(self):
        self.run_case(r"""(()=>{
          const source='<script>alert(1)</script>\n<img src=x onerror=evil()>\n**Bold** and `inline`\n```js\nconst text = "<img src=x>";\n```';
          assert.equal(R.render(parent,source),true);
          assert.equal(tag(parent,'script').length,0);assert.equal(tag(parent,'img').length,0);
          assert.equal(tag(parent,'strong')[0].textContent,'Bold');
          assert.equal(cls(parent,'rich-inline-code')[0].textContent,'inline');
          assert.equal(tag(parent,'pre')[0].textContent,'const text = "<img src=x>";');
          assert.ok(cls(parent,'rich-token-keyword').length>0);
          assert.ok(cls(parent,'rich-token-string').length>0);
        })()""")

    def test_code_copy_and_fold_preserve_literal_source_and_never_execute(self):
        self.run_case(r"""(async()=>{
          const source='print("한국어")\n# exact source\n';R.code(parent,source,'python');
          const buttons=tag(parent,'button');assert.equal(buttons.length,2);
          await buttons[0].onclick();assert.equal(readClipboard(),source);
          buttons[1].onclick();assert.equal(tag(parent,'pre')[0].hidden,true);
          assert.equal(buttons[1].attributes['aria-expanded'],'false');
          buttons[1].onclick();assert.equal(tag(parent,'pre')[0].hidden,false);
          assert.equal(apiCalls.length,0);assert.ok(!parent.textContent.includes('실행 완료'));
        })()""")

    def test_code_truncation_is_explicit_and_copy_uses_visible_scope(self):
        self.run_case(r"""(async()=>{
          R.code(parent,'a'.repeat(100010),'txt',{collapsed:true});
          assert.equal(tag(parent,'pre')[0].hidden,true);
          await tag(parent,'button')[0].onclick();assert.equal(readClipboard().length,100000);
          assert.match(parent.textContent,/표시된 부분/);
        })()""")

    def test_many_tiny_fences_are_bounded_without_thousands_of_controls(self):
        self.run_case(r"""(()=>{
          const source=('```js\na\n```\n').repeat(1000);R.render(parent,source);
          assert.equal(cls(parent,'rich-code').length,1);assert.equal(tag(parent,'button').length,2);
          assert.equal(tag(parent,'pre')[0].textContent,source);assert.match(parent.textContent,/서식 없이/);
        })()""")

    def test_syntax_dom_budget_keeps_remaining_code_as_literal_text(self):
        self.run_case(r"""(()=>{
          const source='const a = 1;\n'.repeat(2500);R.code(parent,source,'javascript');
          assert.equal(tag(parent,'pre')[0].textContent,source);
          assert.ok(all(parent,()=>true).length<4100);
        })()""")

    def test_table_parses_escaped_and_inline_code_pipes(self):
        self.run_case(r"""(()=>{
          R.render(parent,'| 이름 | 값 |\n| --- | --- |\n| A\\|B | `x|y` |');
          assert.equal(tag(parent,'th').length,2);assert.equal(tag(parent,'td').length,2);
          assert.equal(tag(parent,'td')[0].textContent,'A|B');assert.equal(tag(parent,'td')[1].textContent,'x|y');
        })()""")

    def test_tables_bound_rows_columns_and_copy_only_visible_scope(self):
        self.run_case(r"""(async()=>{
          const columns=Array.from({length:35},(_,i)=>'col'+i),rows=Array.from({length:120},(_,r)=>columns.map((_,c)=>r+':'+c));
          R.table(parent,{columns,rows});assert.equal(tag(parent,'th').length,30);assert.equal(tag(parent,'td').length,3000);
          await tag(parent,'button')[0].onclick();const lines=readClipboard().split('\n');
          assert.equal(lines.length,101);assert.equal(lines[0].split('\t').length,30);assert.match(parent.textContent,/복사는 표시된 범위/);
        })()""")

    def test_table_data_is_plain_text_and_tsv_escapes_cell_separators(self):
        self.run_case(r"""(async()=>{
          R.table(parent,{columns:['name','value'],rows:[['<img src=x>','a\tb\nc"d']]});
          assert.equal(tag(parent,'img').length,0);await tag(parent,'button')[0].onclick();
          assert.equal(readClipboard(),'name\tvalue\n<img src=x>\t"a\tb\nc""d"');
        })()""")

    def test_images_wait_until_visible_and_remote_images_never_load(self):
        self.run_case(r"""(async()=>{
          R.render(parent,'![local](chart.png)\n![remote](https://example.com/a.png)\n![bad](javascript:alert(1))',{sessionId:'A',workspace:'C:/work'});
          assert.equal(apiCalls.length,0);assert.equal(tag(parent,'img').length,0);
          assert.equal(tag(parent,'a').length,1);assert.equal(tag(parent,'a')[0].rel,'noopener noreferrer');
          observers[0].trigger();assert.equal(apiCalls.length,1);assert.match(apiCalls[0],/path=C%3A%2Fwork%2Fchart.png/);
          replies[0].resolve({kind:'image',data:'data:image/png;base64,YQ=='});await flush();
          assert.equal(tag(parent,'img').length,1);assert.equal(tag(parent,'img')[0].alt,'local');
          observers[0].trigger(false);assert.equal(tag(parent,'img').length,0);
        })()""")

    def test_images_bound_requests_and_mounted_data_and_reset_releases_sources(self):
        self.run_case(r"""(async()=>{
          R.files(parent,Array.from({length:8},(_,i)=>'chart'+i+'.png'),{sessionId:'A'});
          observers[0].trigger();assert.equal(apiCalls.length,2);
          for(let i=0;i<8;i++){replies[i].resolve({kind:'image',data:'data:image/png;base64,YQ=='});await flush();}
          assert.equal(apiCalls.length,8);assert.equal(tag(parent,'img').length,4);
          const retained=tag(parent,'img');R.reset();assert.equal(tag(parent,'img').length,0);assert.ok(retained.every(img=>!img.src));
        })()""")

    def test_detached_message_keeps_inline_image_observers_when_adding_attachments(self):
        self.run_case(r"""(async()=>{
          const article=new Element('article'),body=new Element('div');
          R.render(body,'![chart](chart.png)',{sessionId:'A'});article.append(body);
          R.files(article,['source.png'],{sessionId:'A'});parent.append(article);
          observers[0].trigger();assert.equal(apiCalls.length,2);
          for(const reply of replies)reply.resolve({kind:'image',data:'data:image/png;base64,YQ=='});await flush();
          assert.equal(tag(parent,'img').length,2);
        })()""")

    def test_delayed_image_from_old_session_does_not_mount_or_open(self):
        self.run_case(r"""(async()=>{
          R.files(parent,['chart.png'],{sessionId:'A'});observers[0].trigger();
          const old=cls(parent,'rich-image')[0];R.reset();active={id:'B',workspace:'C:/other'};
          replies[0].resolve({kind:'image',data:'data:image/png;base64,YQ=='});await flush();
          assert.equal(tag(parent,'img').length,0);await old.onclick();assert.equal(opens.length,0);
        })()""")

    def test_failed_or_malformed_image_has_manual_open_and_no_unsafe_src(self):
        self.run_case(r"""(async()=>{
          R.files(parent,['missing.png','bad.png'],{sessionId:'A'});observers[0].trigger();
          replies[0].reject(new Error('not found'));replies[1].resolve({kind:'image',data:'data:image/svg+xml;base64,PHN2Zz4='});await flush();
          assert.equal(tag(parent,'img').length,0);assert.match(parent.textContent,/파일 열어 보기/);
          await cls(parent,'rich-image')[0].onclick();assert.equal(opens[0],'C:/work/missing.png');
        })()""")

    def test_local_images_reject_network_and_device_paths(self):
        self.run_case(r"""(()=>{
          R.files(parent,['\\\\server\\share\\x.png','//server/x.png','data:image/png;base64,YQ==','https://example.com/x.png','C:/work/ok.png'],{sessionId:'A'});
          observers[0].trigger();assert.equal(apiCalls.length,1);assert.match(apiCalls[0],/ok.png/);
        })()""")

    def test_oversized_decoded_image_drops_pixels_and_keeps_file_action(self):
        self.run_case(r"""(async()=>{
          R.files(parent,['huge.png'],{sessionId:'A'});observers[0].trigger();
          replies[0].resolve({kind:'image',data:'data:image/png;base64,YQ=='});await flush();
          const img=tag(parent,'img')[0];img.naturalWidth=5000;img.naturalHeight=5000;img.onload();
          assert.equal(tag(parent,'img').length,0);assert.match(parent.textContent,/파일 열어 보기/);
        })()""")


if __name__ == "__main__":
    unittest.main()
