/* Shipped HTML/CSS/JS with synthetic company-model HTTP responses only.
 * Never starts Claude, changes personal settings, or reads user attachments.
 * NODE_PATH=<playwright node_modules> node this-file --output <evidence-dir>
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const models=[{value:'company-default',displayName:'회사 기본 모델',description:'기존 Claude 연결에서 확인한 기본 모델입니다.'},{value:'company-fast',displayName:'회사 빠른 모델',description:'짧은 질문을 위한 회사 제공 모델입니다.'},{value:'company-long',displayName:'회사에서 제공한 긴 이름의 분석 모델 · 사내 문서와 여러 자료의 내용을 함께 살펴보는 모델',description:'긴 이름도 선택 창과 입력창 밖으로 넘치지 않아야 합니다.'}];
const connection=(model='company-default',supported=true)=>({connected:true,model,modelOverride:null,availableModels:models,effort:'medium',availableEfforts:['low','medium','high'],permissionMode:'default',permissionModeLabel:'Manual',availablePermissionModes:[{value:'default',displayName:'Manual'}],capabilities:{setModel:supported,setEffort:true,setPermissionMode:true}});
const row=(id,kind,title,info=null)=>({id,kind,title,workspace:'C:/fixture/'+id,state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[],connection:info,modelOverride:null});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const clone=value=>JSON.parse(JSON.stringify(value));
const checks=[],geometry=[],allErrors=[];
async function fixture(browser){
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
 const page=await context.newPage(),calls=[],errors=[],drafts=new Map(),holds=new Map();
 const rows=[row('work-one','work','월간 실적 보고서',connection('work-private')),row('chat-two','chat','두 번째 대화',connection('company-second')),row('chat-disconnected','chat','아직 연결하지 않은 대화'),row('chat-unsupported','chat','모델 변경을 제공하지 않는 연결',connection('company-fixed',false))];
 const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};let count=0,failNextModel=false,failNextSession=false;
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('http://workspace.test/**',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(!p.startsWith('/api/')){
   const filename=path.resolve(web,p==='/'?'index.html':p.slice(1));
   if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});
   return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.png':'image/png','.svg':'image/svg+xml'})[path.extname(filename)]||'application/octet-stream'});
  }
  const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
  calls.push({path:p,method:req.method(),body});
  const hold=holds.get(p);if(hold){holds.delete(p);hold.seen();await hold.promise;}
  let result={ok:true},status=200;
  if(p==='/api/bootstrap')result={sessions:rows,demo:false,appearance:{theme:'light'},executionMode:'normal',chatAttachmentPolicy:{extensions:['.txt']},sessionGroups:{groups:[],assignments:{},revision:0},appUpdate:{autoCheck:false},runtime:{entry:'fixture'}};
  else if(p==='/api/drafts'){if(req.method()==='GET')result={entries:[...drafts.values()],selectedId:'home'};else {drafts.set(body.id,{...body,revision:(body.revision||0)+1});result={revision:(body.revision||0)+1};}}
  else if(p==='/api/session'){
   if(failNextSession){failNextSession=false;status=503;result={error:'새 대화 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.'};}
   else result=rows.find(item=>item.id===url.searchParams.get('id'));
  }
  else if(p==='/api/create'){result=row('chat-new-'+(++count),'chat','새 대화');rows.unshift(result);}
  else if(p==='/api/connect'){const item=rows.find(item=>item.id===body.id);item.connection=connection(item.modelOverride||'company-default');item.connection.modelOverride=item.modelOverride;result={ok:true,connection:item.connection};}
  else if(p==='/api/model'){
   if(failNextModel){failNextModel=false;status=400;result={error:'회사 연결에서 해당 모델을 사용할 수 없어요.'};}
   else{const item=rows.find(item=>item.id===body.id);item.modelOverride=body.model;item.connection={...item.connection,model:body.model||'company-default',modelOverride:body.model};result={ok:true,connection:item.connection,modelOverride:body.model};}
  }
  else if(p==='/api/send'){const item=rows.find(item=>item.id===body.id);item.messages.push({role:'user',text:body.text,files:body.attachments||[]});item.state='done';}
  else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:rows.map(item=>({id:item.id,kind:item.kind,state:item.state}))};
  else if(p==='/api/notifications')result={desktop};
  else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
  else if(p==='/api/files')result={files:[]};
  else if(p==='/api/results')result={artifacts:[],lastRunId:null};
  else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
  else if(p==='/api/events'){await sleep(150);result={events:[]};}
  await route.fulfill({status:result?status:404,json:clone(result||{error:'not found'})});
 });
 await page.goto('http://workspace.test/#token=fixture');
 await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
 page.setDefaultTimeout(8000);
 const hold=endpoint=>{let release,seen;const promise=new Promise(resolve=>release=resolve),started=new Promise(resolve=>seen=resolve);holds.set(endpoint,{promise,seen});return {release,started};};
 const select=async id=>{await page.locator(`#sessions [data-session-id="${id}"] .session`).click();await page.waitForFunction(id=>active?.id===id,id);};
 const finish=async()=>{assert.deepEqual(errors,[]);allErrors.push(...errors);await context.close();};
 return {page,rows,calls,hold,select,finish,failModel:()=>failNextModel=true,failSession:()=>failNextSession=true};
}
async function visibleControls(page){assert.equal(await page.locator('#composer-model').isVisible(),true);assert.equal(await page.locator('#composer-effort').isVisible(),false);assert.equal(await page.locator('#composer-permission').isVisible(),false);}
async function openModel(page){await page.locator('#composer-model').click();await page.locator('#runtime-panel-options .runtime-option').first().waitFor();}
async function chooseModel(page,value){await page.locator(`#runtime-panel-options [data-runtime-value="${value}"]`).click();await page.waitForFunction(value=>active?.modelOverride===value,value||null);await page.locator('#composer-controls-panel').waitFor({state:'hidden'});}
async function testCore(browser){
 const f=await fixture(browser),{page,calls,rows}=f;
 try{
  await f.select('work-one');
  for(const id of ['composer-model','composer-effort','composer-permission'])assert.equal(await page.locator('#'+id).isVisible(),true);
  assert.equal(await page.locator('#composer-model strong').textContent(),'work-private');
  await page.locator('#mode-chat').click();await visibleControls(page);
  assert.equal(calls.some(c=>['/api/connect','/api/create','/api/send'].includes(c.path)),false);
  assert.equal(await page.locator('#composer-model strong').textContent(),'Claude 기본');
  await page.locator('#prompt').fill('모델을 고르는 동안 이 초안을 유지해 주세요.');
  await openModel(page);
  const created=await page.evaluate(()=>active.id);
  assert.equal(calls.filter(c=>c.path==='/api/create').length,1);
  assert.equal(calls.filter(c=>c.path==='/api/connect').length,1);
  assert.equal(calls.some(c=>c.path==='/api/send'),false);
  assert.equal(await page.locator('#folder-dialog').isVisible(),false);
  assert.equal(await page.locator('#prompt').inputValue(),'모델을 고르는 동안 이 초안을 유지해 주세요.');
  assert.equal(await page.locator('#composer-model strong').textContent(),'회사 기본 모델');
  assert.equal(await page.locator('#runtime-panel-options [data-runtime-value="company-fast"]').count(),1);
  assert.equal(await page.locator('#runtime-panel-options [aria-pressed="true"]').count(),1);
  assert.equal(await page.locator('#runtime-panel-options [aria-pressed="true"]').getAttribute('data-runtime-value'),'');
  checks.push('model chip visible alone in chat; work controls preserved','home model click lazily creates and connects without a question or folder prompt','home draft preserved; actual connection default and company model choices shown');
  await chooseModel(page,'company-fast');
  assert.equal(calls.filter(c=>c.path==='/api/model').at(-1).body.id,created);
  assert.equal(await page.locator('#composer-model strong').textContent(),'회사 빠른 모델');
  await openModel(page);await page.locator('#inline-model-name').fill('company-custom-v2');await page.locator('#inline-model-name').press('Enter');
  await page.waitForFunction(()=>active?.modelOverride==='company-custom-v2');await page.locator('#composer-controls-panel').waitFor({state:'hidden'});
  assert.equal(await page.locator('#composer-model strong').textContent(),'company-custom-v2');
  await openModel(page);await chooseModel(page,'');
  assert.equal(calls.filter(c=>c.path==='/api/model').at(-1).body.model,null);
  assert.equal(await page.locator('#composer-model strong').textContent(),'회사 기본 모델');
  await openModel(page);assert.equal(await page.locator('#runtime-panel-options [aria-pressed="true"]').count(),1);await page.locator('#runtime-panel-close').click();
  checks.push('company list, custom model Enter, and restore original settings use the current session only');
  await openModel(page);await chooseModel(page,'company-fast');await f.select('chat-two');
  assert.equal(await page.locator('#composer-model strong').textContent(),'company-second');
  await f.select(created);assert.equal(await page.locator('#composer-model strong').textContent(),'회사 빠른 모델');
  await page.locator('#mode-work').click();await page.waitForFunction(()=>active?.id==='work-one');assert.equal(await page.locator('#composer-model strong').textContent(),'work-private');
  await page.locator('#mode-chat').click();await page.waitForFunction(id=>active?.id===id,created);
  assert.equal(await page.locator('#composer-model strong').textContent(),'회사 빠른 모델');
  checks.push('separate chats and work mode retain their own model selections');
  await page.evaluate(()=>{active.state='running';setStatus('running');});assert.equal(await page.locator('#composer-model').isDisabled(),true);
  await page.evaluate(()=>{active.state='idle';setStatus('idle');});assert.equal(await page.locator('#composer-model').isEnabled(),true);
  await f.select('chat-unsupported');await openModel(page);
  assert.equal(await page.locator('#inline-model-name').count(),0);
  assert.equal(await page.locator('#runtime-panel-options button:not([disabled])').count(),0);
  assert.match(await page.locator('#runtime-panel-note').textContent(),/제공하지/);
  await page.locator('#runtime-panel-close').click();await f.select(created);await openModel(page);f.failModel();
  await page.locator('#runtime-panel-options [data-runtime-value="company-long"]').click();await page.locator('#runtime-panel-error').waitFor();
  assert.match(await page.locator('#runtime-panel-error').textContent(),/사용할 수 없어요/);
  assert.equal(await page.evaluate(()=>active.modelOverride),'company-fast');
  checks.push('running requests disable model change','unsupported capabilities and rejected model leave the original model intact');
  await page.locator('#runtime-panel-close').click();await openModel(page);await chooseModel(page,'company-long');
  for(const width of [1440,680,420]){
   await page.setViewportSize({width,height:1000});
   for(const theme of ['light','dark']){
    if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme){await page.locator('#appearance-toggle').click();await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);}
    await page.evaluate(async()=>{await document.fonts.ready;document.getElementById('toast').hidden=true;});
    await openModel(page);await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const result=await page.evaluate(()=>{const box=id=>{const r=document.getElementById(id).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};return {viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,chip:box('composer-model'),panel:box('composer-controls-panel'),font:getComputedStyle(document.getElementById('composer-model')).fontFamily};});
    assert.equal(result.overflow,false);assert.ok(result.chip.right<=width+1);assert.ok(result.panel.left>=0&&result.panel.right<=width+1);assert.ok(result.panel.top>=0&&result.panel.bottom<=1001);assert.match(result.font,/Noto/);
    geometry.push({width,theme,...result});if(output)await page.screenshot({path:path.join(output,`chat-model-${width}-${theme}.png`)});
    await page.locator('#runtime-panel-close').click();
   }
  }
  checks.push('light/dark at 1440, 680, 420 pixels: long model label, anchored panel, no page overflow');
  await page.setViewportSize({width:1440,height:1000});await f.select('chat-two');
  const modelCalls=calls.filter(c=>c.path==='/api/model').length;
  await page.locator('#prompt').fill('/model company-fast');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>document.getElementById('prompt').value==='');
  assert.equal(calls.filter(c=>c.path==='/api/send').at(-1).body.text,'/model company-fast');
  assert.equal(calls.filter(c=>c.path==='/api/model').length,modelCalls);
  assert.equal(await page.locator('#composer-controls-panel').isVisible(),false);
  assert.equal(rows.find(item=>item.id==='work-one').connection.model,'work-private');
  checks.push('/model text stays an ordinary chat question; no settings command or personal-setting write');
 }finally{await f.finish();}
}
async function testRaces(browser){
 const f=await fixture(browser),{page,calls}=f;
 try{
  await f.select('work-one');await page.locator('#mode-chat').click();await f.select('chat-disconnected');
  const connect=f.hold('/api/connect');await page.locator('#composer-model').click();await connect.started;
  await page.locator('#mode-work').click();await page.waitForFunction(()=>active?.id==='work-one');connect.release();
  await page.waitForFunction(()=>!connectionPreparing);assert.equal(await page.locator('#composer-model strong').textContent(),'work-private');assert.equal(await page.locator('#composer-controls-panel').isVisible(),false);
  await page.locator('#mode-chat').click();await f.select('chat-two');await openModel(page);
  const model=f.hold('/api/model');await page.locator('#runtime-panel-options [data-runtime-value="company-fast"]').click();await model.started;
  await f.select('chat-disconnected');model.release();await page.waitForFunction(()=>!modelChanging);
  assert.equal(await page.evaluate(()=>active.modelOverride),null);assert.equal(await page.locator('#composer-model strong').textContent(),'회사 기본 모델');assert.equal(await page.locator('#composer-controls-panel').isVisible(),false);
  await page.locator('#home-button').click();await page.waitForFunction(()=>active===null);
  const before=calls.filter(c=>c.path==='/api/connect').length,create=f.hold('/api/create');await page.locator('#composer-model').click();await create.started;
  await page.locator('#mode-work').click();await page.waitForFunction(()=>active?.id==='work-one');create.release();await page.waitForFunction(()=>chatCreation===null);
  assert.equal(calls.filter(c=>c.path==='/api/connect').length,before);assert.equal(await page.locator('#composer-model strong').textContent(),'work-private');assert.equal(await page.locator('#composer-controls-panel').isVisible(),false);
  assert.equal(calls.some(c=>c.path==='/api/send'),false);
  checks.push('late connect response cannot overwrite work selection','late model response cannot overwrite another chat','late chat creation cannot select or connect after mode switch');
 }finally{await f.finish();}
}
async function testPreparingDraft(browser){
 const f=await fixture(browser),{page,calls}=f;
 try{
  await page.locator('#mode-chat').click();await page.locator('#prompt').fill('처음 작성한 초안');
  const create=f.hold('/api/create');await page.locator('#composer-model').click();await create.started;
  assert.equal(await page.locator('#composer-model').isDisabled(),true);
  await page.locator('#prompt').fill('연결을 준비하는 동안 고친 최신 초안');
  const lookup=f.hold('/api/session');create.release();await lookup.started;
  await page.locator('#prompt').fill('생성된 대화의 조회를 기다리며 다시 고친 최신 초안');
  lookup.release();await page.locator('#runtime-panel-options .runtime-option').first().waitFor();
  assert.equal(await page.locator('#prompt').inputValue(),'생성된 대화의 조회를 기다리며 다시 고친 최신 초안');
  assert.equal(calls.filter(c=>c.path==='/api/create').length,1);assert.equal(calls.filter(c=>c.path==='/api/connect').length,1);assert.equal(calls.some(c=>c.path==='/api/send'),false);
  await page.locator('#inline-model-name').focus();await page.keyboard.press('Escape');await page.locator('#composer-controls-panel').waitFor({state:'hidden'});
  assert.equal(await page.locator('#composer-model').evaluate(node=>node===document.activeElement),true);
  const created=await page.evaluate(()=>active.id);await f.select('chat-two');await f.select(created);
  assert.equal(await page.locator('#prompt').inputValue(),'생성된 대화의 조회를 기다리며 다시 고친 최신 초안');
  checks.push('latest draft typed during create and subsequent session lookup delays is saved and survives navigation; one creation and no send','default uses one selected checkmark; Escape closes the chooser and restores focus');
 }finally{await f.finish();}
}
async function testCreationLookupFailure(browser){
 const f=await fixture(browser),{page,calls}=f;
 try{
  await page.locator('#mode-chat').click();await page.locator('#prompt').fill('조회에 실패해도 남아 있어야 하는 초안');f.failSession();
  await page.locator('#composer-model').click();await page.locator('#composer-control-status').filter({hasText:'새 대화 정보를 불러오지 못했어요'}).waitFor();
  assert.equal(await page.locator('#composer-control-status').getAttribute('data-error'),'true');
  assert.equal(await page.locator('#composer-model').isEnabled(),true);assert.equal(await page.evaluate(()=>active),null);
  assert.equal(await page.locator('#prompt').inputValue(),'조회에 실패해도 남아 있어야 하는 초안');
  assert.equal(calls.filter(c=>c.path==='/api/create').length,1);assert.equal(calls.some(c=>['/api/connect','/api/send'].includes(c.path)),false);
  if(output)await page.screenshot({path:path.join(output,'chat-model-lookup-error.png')});
  checks.push('failed lookup after creation shows an owned error, preserves the draft, and leaves model control available without connecting or sending');
 }finally{await f.finish();}
}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{await testCore(browser);await testRaces(browser);await testPreparingDraft(browser);await testCreationLookupFailure(browser);const result={passed:true,checks,geometry,pageErrors:allErrors};if(output)fs.writeFileSync(path.join(output,'chat-model-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
