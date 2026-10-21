/* Run from the repository root with Playwright on NODE_PATH.
 * Synthetic HTTP and assistant stream events; never starts Claude or edits personal data.
 * Verifies real mouse jitter, handle-only native dragging, and streaming updates.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve('local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const allErrors=[];
const clone=value=>JSON.parse(JSON.stringify(value));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const row=(id,title,kind='work')=>({id,kind,title,workspace:'C:/fixture/'+id,state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[],connection:null});
async function fixture(browser){
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
 const page=await context.newPage(),calls=[],errors=[],drafts=new Map();
 let dispatch={revision:1,queue:[{id:'q0',text:'첫 줄\n두 번째 줄',state:'queued',editRevision:0},{id:'q1',text:'다음 요청',state:'queued',editRevision:0},{id:'q2',text:'마지막 요청',state:'queued',editRevision:0}],schedules:[],steer:{supported:true}};
 const rows=[...Array.from({length:8},(_,i)=>row('group-'+(i+1),['보고서 초안 정리','분기 실적 확인','발표 자료 요약','회의 후속 요청','현장 개선 아이디어','장기 검토 항목','숨겨진 일곱 번째 업무','폴더의 마지막 긴 업무 이름이 바깥으로 나오지 않도록 확인합니다'][i])),row('five-1','정확히 다섯 개 폴더 1'),row('five-2','정확히 다섯 개 폴더 2'),row('five-3','정확히 다섯 개 폴더 3'),row('five-4','정확히 다섯 개 폴더 4'),row('five-5','정확히 다섯 개 폴더 5'),row('outside','폴더 밖에서 옮길 업무'),row('chat-1','처음 시작한 일반 대화','chat')];
 let groups={groups:[{id:'large',name:'운영 보고서',collapsed:false},{id:'five',name:'다섯 개 업무',collapsed:false},{id:'empty',name:'새 아이디어',collapsed:false}],assignments:Object.fromEntries(rows.filter(r=>r.id.startsWith('group-')||r.id.startsWith('five-')).map(r=>[r.id,r.id.startsWith('group-')?'large':'five'])),revision:0};
 const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://workspace.test/**',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(!p.startsWith('/api/')){
   const filename=path.resolve(web,p==='/'?'index.html':p.slice(1));
   if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});
   return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.png':'image/png','.svg':'image/svg+xml'})[path.extname(filename)]||'application/octet-stream'});
  }
  const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
  calls.push({path:p,method:req.method(),body});let result={ok:true};
  if(p==='/api/bootstrap')result={sessions:rows,demo:false,appearance:{theme:'light'},executionMode:'normal',chatAttachmentPolicy:{extensions:['.txt']},sessionGroups:groups,appUpdate:{autoCheck:false},runtime:{entry:'fixture'}};
  else if(p==='/api/drafts'){if(req.method()==='GET')result={entries:[...drafts.values()],selectedId:'home'};else {drafts.set(body.id,{...body,revision:(body.revision||0)+1});result={revision:(body.revision||0)+1};}}
  else if(p==='/api/session')result=rows.find(item=>item.id===url.searchParams.get('id'));
  else if(p==='/api/session-groups'){
   if(req.method()!=='GET'){
    if(body.action==='collapse')groups.groups.find(g=>g.id===body.groupId).collapsed=body.collapsed;
    if(body.action==='move'){if(body.groupId)groups.assignments[body.id]=body.groupId;else delete groups.assignments[body.id];}
    groups.revision++;
   }
   result=req.method()==='GET'?groups:{sessionGroups:groups};
  }
  else if(p==='/api/schedules')result={schedules:[],counts:{total:0,active:0,paused:0,attention:0,completed:0}};
  else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:rows};
  else if(p==='/api/notifications')result={desktop};
  else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
  else if(p==='/api/files')result={files:[]};
  else if(p==='/api/results')result={artifacts:[],lastRunId:null};
  else if(p==='/api/dispatch'){
   if(body.action==='cancel')dispatch.queue=dispatch.queue.filter(r=>r.id!==body.requestId);
   if(body.action==='reorder')dispatch.queue=body.order.map(id=>dispatch.queue.find(r=>r.id===id));
   if(body.action==='apply_now')dispatch.queue=dispatch.queue.filter(r=>r.id!==body.requestId);
   if(['enqueue','steer'].includes(body.action))dispatch.queue.push({id:'new'+dispatch.revision,text:body.text,state:'queued',editRevision:0});
   if(body.action)dispatch.revision++;
   result=dispatch;
  }
  else if(p==='/api/events'){await sleep(150);result={events:[]};}
  await route.fulfill({status:result?200:404,json:clone(result||{error:'not found'})});
 });
 await page.goto('http://workspace.test/#token=fixture');
 await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
 await page.evaluate(()=>document.fonts.ready);
 page.setDefaultTimeout(8000);
 return {page,calls,rows,errors,resetQueue:()=>{dispatch.queue=Array.from({length:3},(_,i)=>({id:'q'+i,text:'request '+i,state:'queued',editRevision:0}));dispatch.revision++;},getGroups:()=>groups,finish:async()=>{allErrors.push(...errors);assert.deepEqual(errors,[]);await page.evaluate(()=>{appClosed=true;pollController?.abort();});await page.unrouteAll({behavior:'ignoreErrors'});await context.close();}};
}


(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{const f=await fixture(browser),p=f.page;
await p.locator('#sessions [data-session-id="group-1"] .session').click();await p.waitForFunction(()=>active?.id==='group-1');
await p.evaluate(async()=>{active.state='running';active.trusted=true;setStatus('running');await WorkspaceWorkflow.refresh();});await p.waitForTimeout(450);
await p.locator('#prompt').fill('preserve my draft');
const results=[];
for(const streaming of [false,true]){
 if(streaming)await p.evaluate(()=>{window.testTicks=0;window.testStream=setInterval(()=>{handleEvent({type:'assistant_delta',data:{messageId:'load',runId:'load',text:'Streaming response text. '.repeat(20)}});setStatus('running');testTicks++;},20);});
 for(const [selector,action] of [['.inline-queue-delete','cancel'],['.inline-queue-apply','apply_now']])for(const jitter of [0,4,7,10]){
  f.resetQueue();await p.evaluate(()=>WorkspaceWorkflow.refresh());const before=f.calls.filter(c=>c.body.action===action).length;
  const b=await p.locator(selector).first().boundingBox();await p.mouse.move(b.x+b.width/2,b.y+b.height/2);await p.mouse.down();await p.mouse.move(b.x+b.width/2+jitter,b.y+b.height/2,{steps:4});await p.mouse.up();
  await p.waitForFunction(()=>document.querySelectorAll('.inline-queue-row').length===2);
  assert.equal(f.calls.filter(c=>c.body.action===action).length,before+1);assert.equal(await p.locator('#prompt').inputValue(),'preserve my draft');results.push({streaming,action,jitter,passed:true});
 }
 f.resetQueue();await p.evaluate(()=>WorkspaceWorkflow.refresh());const before=f.calls.filter(c=>c.body.action==='reorder').length;
 await p.locator('.inline-queue-handle').nth(2).dragTo(p.locator('.inline-queue-row').first());
 await p.waitForFunction(()=>document.querySelector('.inline-queue-row').dataset.requestId==='q2');assert.equal(f.calls.filter(c=>c.body.action==='reorder').length,before+1);
 results.push({streaming,action:'handle reorder',passed:true});
 if(streaming){const ticks=await p.evaluate(()=>{clearInterval(testStream);return testTicks;});assert.ok(ticks>0);console.log(JSON.stringify({streamTicks:ticks}));}
}
assert.deepEqual(f.errors,[]);console.log(JSON.stringify({checks:results,errors:f.errors}));await f.finish();
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});