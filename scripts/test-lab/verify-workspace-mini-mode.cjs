/* Real shipped application and browser input events; synthetic API responses only.
 * NODE_PATH=<playwright node_modules> node this-file --output <outside-repo-evidence-dir>
 * No Claude process, real connection, user file, scheduling or approval action.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const repository=path.resolve(__dirname,'../..'),web=path.join(repository,'local_app/web');
const outputIndex=process.argv.indexOf('--output');
if(outputIndex!==-1&&!process.argv[outputIndex+1])throw new Error('--output requires a directory');
const output=outputIndex===-1?null:path.resolve(process.argv[outputIndex+1]);
if(output&&(output===repository||output.startsWith(repository+path.sep)))throw new Error('Keep browser evidence outside the repository');
if(output)fs.mkdirSync(output,{recursive:true});
const checks=[],geometry=[],pageErrors=[],sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const clone=value=>JSON.parse(JSON.stringify(value));
const draftText='작성 중인 초안은 간편 화면을 바꿔도 그대로 유지합니다.';
const attachment='C:/fixture/reports/월간 실적.csv';
const normalPreferences={sidebarCollapsed:false,inspectorCollapsed:false,inspectorWidth:400,miniMode:false};
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
const hiddenInMini=['#capabilities-open','.sidebar-footer','#task-title','#task-pin','#branch-open','#workspace-summary','#connection-badge','#restart-connection','#materials-button','#workspace-button','#workflow-open','#schedule-open','#attach','#attach-path','#composer-model','#composer-effort','#composer-permission','#inspector-panel','#inspector-resizer'];
const settle=page=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
async function screenshot(page,name){if(output){await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});if(/^(?:standard-desktop-light|simple-view-(?:1440|1000|640)-(?:light|dark))$/u.test(name))await page.locator('.topbar').screenshot({path:path.join(output,name+'-header.png'),animations:'disabled'});if(name.startsWith('panel-'))await page.screenshot({path:path.join(output,name+'-header.png'),clip:{x:0,y:0,width:page.viewportSize().width,height:120},animations:'disabled'});}}
const layoutServer=(layout=null,delayMs=0)=>({layout:clone(layout),delayMs,inFlight:0,maxInFlight:0,writes:[],completed:[]});
async function waitForLayout(server,expected){for(let index=0;index<140;index++){if(server.inFlight===0&&JSON.stringify(server.layout)===JSON.stringify(expected))return;await sleep(25);}assert.deepEqual(server.layout,expected,'server must persist the final layout');assert.equal(server.inFlight,0,'layout write must finish');}
async function fixture(browser,{preferences=normalPreferences,restricted=false,server=layoutServer(preferences),origin='http://workspace.test',seedLocal=true}={}){
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await context.newPage();
 const calls=[],errors=[],eventQueues=new Map(),drafts=new Map();let selectedId='mini-work';
 const row=(id,title,kind='work',state='idle')=>({id,title,kind,state,workspace:'C:/fixture/reports',trusted:true,created:1,updated:1,seq:0,lastRunId:'run-'+id,
  messages:[{role:'user',text:'이번 달 실적의 주요 변화를 정리해 주세요.'},{role:'assistant',text:'실적 자료를 확인하고 있습니다.'}],requests:[],artifacts:[],toolActivity:[],executions:[],
  sessionId:'claude-'+id,connection:{connected:true,sessionId:'claude-'+id,model:'fixture-model',capabilities:{setModel:true,setEffort:true,setPermissionMode:true},availableModels:[{value:'fixture-model',displayName:'Fixture Model'}],availableEfforts:[{value:'medium',displayName:'보통'}],availablePermissionModes:[{value:'default',displayName:'기본'}]}});
 const rows=[row('mini-work','월간 실적 보고서'),row('mini-running','진행 중인 실적 분석','work','running'),row('mini-approval','승인이 필요한 자료 확인','work','approval'),row('mini-chat','일반 대화 기록','chat')];
 rows[2].requests=[{id:'approval-1',tool:'Read',input:{file_path:attachment},description:'월간 실적 자료 읽기'}];
 for(const item of rows){drafts.set(item.id,{id:item.id,revision:1,draft:{text:draftText,attachments:[attachment]},stash:null});eventQueues.set(item.id,[]);}
 const groups={groups:[{id:'reports',name:'실적 보고',collapsed:false}],assignments:{'mini-work':'reports','mini-running':'reports','mini-approval':'reports'},revision:0};
 await page.addInitScript(({preferences,restricted,seedLocal})=>{
  const key='workspace.layout.v1';
  globalThis.__miniInitialLayout=localStorage.getItem(key);
  if(seedLocal&&!sessionStorage.getItem('mini-fixture-seeded')){localStorage.setItem(key,JSON.stringify(preferences));sessionStorage.setItem('mini-fixture-seeded','yes');}
  if(restricted){const get=Storage.prototype.getItem,set=Storage.prototype.setItem;Storage.prototype.getItem=function(key){if(key==='workspace.layout.v1')throw new DOMException('Blocked fixture storage','SecurityError');return get.call(this,key)};Storage.prototype.setItem=function(key,value){if(key==='workspace.layout.v1')throw new DOMException('Blocked fixture storage','SecurityError');return set.call(this,key,value)};}
 },{preferences,restricted,seedLocal});
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  if(!p.startsWith('/api/')){
   const filename=path.resolve(web,p==='/'?'index.html':p.slice(1));
   if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});
   return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.woff2':'font/woff2','.svg':'image/svg+xml','.png':'image/png'})[path.extname(filename)]||'application/octet-stream'});
  }
  const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
  calls.push({path:p,method:req.method(),body});let result={ok:true};
  if(p==='/api/bootstrap')result={sessions:rows,demo:false,appearance:{theme:'light'},layout:server.layout,executionMode:'normal',sessionGroups:groups,appUpdate:{autoCheck:false},runtime:{entry:'fixture'}};
  else if(p==='/api/layout'){
   assert.equal(req.method(),'POST');server.inFlight++;server.maxInFlight=Math.max(server.maxInFlight,server.inFlight);server.writes.push(clone(body));
   if(server.delayMs)await sleep(server.delayMs);
   server.layout=clone(body);server.completed.push(clone(body));result=server.layout;server.inFlight--;
  }
  else if(p==='/api/drafts'){
   if(req.method()==='GET')result={entries:[...drafts.values()],selectedId};
   else {selectedId=body.selectedId;const revision=(body.revision||0)+1;drafts.set(body.id,{...body,revision});result={revision};}
  }else if(p==='/api/session')result=rows.find(item=>item.id===url.searchParams.get('id'));
  else if(p==='/api/attention')result={items:[{id:'approval-1',sessionId:'mini-approval',title:'승인이 필요한 자료 확인',summary:'월간 실적 자료 읽기',kind:'approval'}],total:1,native:{},desktop,sessions:rows};
  else if(p==='/api/notifications')result={desktop};
  else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
  else if(p==='/api/files')result={files:[]};
  else if(p==='/api/results')result={artifacts:[],lastRunId:null};
  else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
  else if(p==='/api/session-groups'){
   if(body.action==='collapse'){groups.groups.find(group=>group.id===body.groupId).collapsed=body.collapsed;groups.revision++;}
   result=req.method()==='GET'?groups:{sessionGroups:groups};
  }else if(p==='/api/completions')result={items:body.kind==='file'?[{id:'file-1',path:attachment,label:'월간 실적.csv',supported:true}]:[{id:'skill-1',invocation:'report',label:'report',description:'보고서 초안 작성',supported:true}]};
  else if(p==='/api/events'){await sleep(100);result={events:eventQueues.get(url.searchParams.get('id'))?.splice(0)||[]};}
  await route.fulfill({status:result?200:404,json:clone(result||{error:'fixture not found'})});
 });
 const ready=async()=>{await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0&&active?.id);await page.evaluate(()=>document.fonts.ready);await settle(page)};
 await page.goto(origin+'/#token=fixture');await ready();page.setDefaultTimeout(7000);
 return {context,page,calls,rows,eventQueues,server,ready,finish:async()=>{pageErrors.push(...errors);assert.deepEqual(errors,[]);await context.close();}};
}
async function setMini(page,value){const button=page.locator('#mini-mode-toggle');if(await button.getAttribute('aria-pressed')!==String(value))await button.click();await page.waitForFunction(value=>document.querySelector('.app').classList.contains('mini-mode')===value,value);await settle(page);assert.equal(await button.getAttribute('aria-pressed'),String(value));}
async function panelActions(page){
 const state=await page.evaluate(()=>({layout:WorkspaceLayout.snapshot(),available:!document.querySelector('.app').matches('.mini-mode,.chat-mode,.catalog-open')}));
 assert.equal(await page.locator('#materials-button').isVisible(),state.available&&!state.layout.inspectorVisible,'header open button must only appear for a closed panel');
 assert.equal(await page.locator('#close-materials').isVisible(),state.layout.inspectorVisible,'open panel must use its internal close button');
 return state;
}
async function iconControls(page){
 for(const selector of ['#appearance-toggle','#mini-mode-toggle','#appearance-toggle > .icon','#mini-mode-toggle > .icon'])assert.equal(await page.locator(selector).isVisible(),true,selector+' must remain visible');
 const state=await page.evaluate(()=>{
  const appearance=$('appearance-toggle'),simple=$('mini-mode-toggle'),rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right}};
  const style=getComputedStyle(simple),pseudo=[getComputedStyle(simple,'::before').content,getComputedStyle(simple,'::after').content];
  return {appearance:rect(appearance),simple:rect(simple),appearanceIcon:rect(appearance.querySelector('.icon')),simpleIcon:rect(simple.querySelector('.icon')),adjacent:simple.previousElementSibling===appearance,text:simple.textContent.trim(),label:simple.getAttribute('aria-label'),title:simple.title,pressed:simple.getAttribute('aria-pressed'),oldTextNodes:simple.querySelectorAll('.mini-mode-label,.mini-mode-state').length,pseudo,paint:[style.backgroundColor,style.borderColor,style.color]};
 });
 assert.equal(state.adjacent,true,'simple view must immediately follow the theme button');
 for(const button of [state.appearance,state.simple]){assert.equal(button.width,32);assert.equal(button.height,32);}
 for(const glyph of [state.appearanceIcon,state.simpleIcon]){assert.equal(glyph.width,18);assert.equal(glyph.height,18);}
 assert.ok(state.simple.x>=state.appearance.right,'simple-view button must appear to the right of theme');assert.ok(Math.abs(state.simple.y-state.appearance.y)<1,'theme and simple-view buttons must share a row');
 assert.equal(state.text,'');assert.equal(state.oldTextNodes,0);assert.equal(state.pseudo.some(value=>/\b(?:ON|OFF)\b/u.test(value)),false);assert.match(state.label,/간편 화면/u);assert.match(state.title,/간편 화면/u);
 return state;
}
async function setTheme(f,theme){
 const {page,calls}=f;if(await page.evaluate(()=>document.documentElement.dataset.theme)===theme)return;
 const before=calls.length;await page.locator('#appearance-toggle').click();await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme&&!document.getElementById('appearance-toggle').disabled,theme);await settle(page);
 const writes=calls.slice(before).filter(call=>call.path==='/api/appearance');assert.equal(writes.length,1);assert.equal(writes[0].method,'POST');assert.equal(writes[0].body.theme,theme);
}
async function bounds(page){return page.evaluate(()=>{
 const rect=id=>{const r=document.getElementById(id).getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
 return {width:innerWidth,theme:document.documentElement.dataset.theme,overflow:document.documentElement.scrollWidth>innerWidth,layout:WorkspaceLayout.snapshot(),prompt:rect('prompt'),toggle:rect('mini-mode-toggle'),composer:rect('composer'),sidebar:rect('sidebar-panel')};
});}
async function tabTo(page,locator){for(let index=0;index<100;index++){await page.keyboard.press('Tab');if(await locator.evaluate(node=>document.activeElement===node))return;}throw new Error('Tab did not reach '+locator);}
async function preserveToggle(f,value){
 const {page,calls}=f;await page.evaluate(()=>WorkspaceDraftPersistence.flush());await settle(page);
 await page.evaluate(()=>{globalThis.__miniBefore={active,connection:active.connection,poll:pollController,signal:pollController.signal,selectionGeneration,mode:currentMode,prompt:$('prompt').value,attachments:JSON.stringify(attachments),messages:[...$('conversation').children],requests:[...$('requests').children],run:active.lastRunId,state:active.state};});
 const before=calls.length;await setMini(page,value);await waitForLayout(f.server,await page.evaluate(()=>WorkspaceLayout.snapshot().preferences));
 const same=await page.evaluate(()=>{const b=__miniBefore;return {session:active===b.active,connection:active.connection===b.connection,poll:pollController===b.poll,signal:!b.signal.aborted,selection:selectionGeneration===b.selectionGeneration,mode:currentMode===b.mode,draft:$('prompt').value===b.prompt,attachments:JSON.stringify(attachments)===b.attachments,messages:b.messages.every((node,index)=>$('conversation').children[index]===node),requests:b.requests.every((node,index)=>$('requests').children[index]===node),run:active.lastRunId===b.run,state:active.state===b.state};});
 assert.ok(Object.values(same).every(Boolean),'toggle changed session state: '+JSON.stringify(same));
 const effects=calls.slice(before),layoutWrites=effects.filter(call=>call.path==='/api/layout');
 assert.equal(layoutWrites.length,1,'one settled toggle saves its layout once');assert.equal(layoutWrites[0].method,'POST');assert.deepEqual(layoutWrites[0].body,await page.evaluate(()=>WorkspaceLayout.snapshot().preferences));
 const sideEffects=effects.filter(call=>call.path!=='/api/layout'&&!['/api/events','/api/attention'].includes(call.path)&&!(call.path==='/api/dispatch'&&call.method==='GET')&&!(call.path==='/api/notifications'&&call.body.action==='view'));
 assert.deepEqual(sideEffects,[],'presentation toggle only saves layout, alongside existing background polling');
}
async function visibleMini(page){
 for(const selector of hiddenInMini)assert.equal(await page.locator(selector).isVisible(),false,selector+' must be hidden in mini mode');
 for(const selector of ['#appearance-toggle','#mini-mode-toggle','#prompt','#attachments .attachment','#conversation'])assert.equal(await page.locator(selector).first().isVisible(),true,selector+' must remain visible');
 const icons=await iconControls(page);assert.equal(icons.pressed,'true');
 await panelActions(page);
 const state=await bounds(page);assert.equal(state.overflow,false);assert.ok(state.toggle.x>=0&&state.toggle.right<=state.width+1);assert.ok(state.prompt.width>200&&state.prompt.bottom<=1001);return state;
}
async function runMain(browser){
 const f=await fixture(browser),{page,calls,eventQueues}=f;
 try{
  assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'false');
  const initialIcons=await iconControls(page);assert.equal(initialIcons.pressed,'false');
  assert.equal(await page.locator('#attach').isVisible(),true);assert.equal(await page.locator('#composer-model').isVisible(),true);
  assert.ok(Math.abs((await page.locator('#inspector-panel').boundingBox()).width-400)<1);
  for(const width of [1409,1440,1448,1498,1000,640]){
   await page.setViewportSize({width,height:1000});await settle(page);assert.equal((await bounds(page)).overflow,false);await iconControls(page);await panelActions(page);
   const clipped=await page.evaluate(()=>{const main=document.querySelector('main').getBoundingClientRect();return [...document.querySelectorAll('.top-actions > *')].filter(node=>node.getBoundingClientRect().width>0).filter(node=>{const r=node.getBoundingClientRect();return r.left<main.left-1||r.right>main.right+1;}).map(node=>node.id);});
   assert.deepEqual(clipped,[],'standard header controls must fit at '+width);await screenshot(page,'standard-'+width+'-light');
  }
  await page.setViewportSize({width:1440,height:1000});await settle(page);
  assert.equal(calls.some(call=>call.path==='/api/layout'),false,'bootstrap and responsive layout must not save preferences');
  checks.push('standard header with mini switch and live restart/attention controls fits at main widths 761/792/800/850 and window widths 1000/640');
  await screenshot(page,'standard-desktop-light');
  await page.locator('#close-materials').focus();await page.keyboard.press('Enter');await settle(page);await panelActions(page);assert.equal(await page.locator('#materials-button').evaluate(node=>document.activeElement===node),true);await page.locator('#chat-title').click();await screenshot(page,'panel-closed-work-light');
  await page.locator('#materials-button').focus();await page.keyboard.press('Space');await settle(page);await panelActions(page);assert.equal(await page.locator('#close-materials').evaluate(node=>document.activeElement===node),true);await page.locator('#chat-title').click();await screenshot(page,'panel-open-work-light');
  checks.push('selected work session shows only the internal close action while panel is open and only the header open action while closed; keyboard activation transfers focus');
  await preserveToggle(f,true);await visibleMini(page);assert.equal(await page.locator('#send').isVisible(),true);
  assert.notDeepEqual((await iconControls(page)).paint,initialIcons.paint,'active simple view needs a visible selected treatment');
  checks.push('간편 화면 is an accessible icon-only 32px button immediately right of the theme button; both retain matching 18px glyphs without label or ON/OFF text');
  for(const selector of ['#session-search','#new-chat','#task-group-create','#sessions .task-group-toggle'])assert.equal(await page.locator(selector).isVisible(),true);
  checks.push('mini hides advanced controls while preserving composer, attachments, messages, sidebar search/new task/groups');
  await page.locator('#session-search').fill('진행 중인');assert.equal(await page.locator('#sessions [data-session-id="mini-running"]').count(),1);await page.locator('#session-search').fill('');
  const group=page.locator('.task-group-header[data-group-id="reports"] .task-group-toggle');await group.click();await page.waitForFunction(()=>document.querySelector('.task-group-toggle').getAttribute('aria-expanded')==='false');await group.click();await page.waitForFunction(()=>document.querySelector('.task-group-toggle').getAttribute('aria-expanded')==='true');
  checks.push('mini sidebar still filters sessions and expands/collapses task folders');
  await preserveToggle(f,false);assert.ok(Math.abs((await page.locator('#inspector-panel').boundingBox()).width-400)<1);
  assert.equal(await page.locator('#attach').isVisible(),true);assert.equal(await page.locator('#composer-model').isVisible(),true);
  checks.push('toggle preserves active object, connection, running poll, selected mode, draft, attachments and message/request DOM; its only new API action saves layout');
  await page.locator('#prompt').focus();await tabTo(page,page.locator('#mini-mode-toggle'));await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.app').classList.contains('mini-mode'));
  assert.equal(await page.locator('#mini-mode-toggle').evaluate(node=>document.activeElement===node),true);await page.keyboard.press('Tab');await tabTo(page,page.locator('#mini-mode-toggle'));await page.keyboard.press('Space');await page.waitForFunction(()=>!document.querySelector('.app').classList.contains('mini-mode'));
  checks.push('Tab can reach mini toggle and exit; Enter/Space work with retained focus');
  await setMini(page,true);await page.locator('#prompt').fill('/rep');await page.locator('#composer-suggestion-list [role="option"]').waitFor();await page.keyboard.press('Tab');assert.equal(await page.locator('#prompt').inputValue(),'/report ');assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'true');
  await page.locator('#prompt').fill('@월간');await page.locator('#composer-suggestion-list [role="option"]').waitFor();await page.keyboard.press('Enter');assert.match(await page.locator('#prompt').inputValue(),/@"월간 실적\.csv"/);assert.equal(await page.locator('#attachments .attachment').count(),1);assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'true');
  assert.equal(calls.some(call=>['/api/connect','/api/send'].includes(call.path)),false);await page.locator('#prompt').fill(draftText);
  checks.push('real slash + Tab and @file + Enter completion remain available without connecting or sending');
  await page.locator('#prompt').focus();await page.keyboard.press('Alt+p');await page.locator('#composer-controls-panel').waitFor({state:'visible'});assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'false');assert.equal(await page.locator('#prompt').inputValue(),draftText);assert.equal(await page.locator('#composer-model').isVisible(),true);await page.keyboard.press('Escape');
  await setMini(page,true);await page.locator('#prompt').fill('/model');await page.keyboard.press('Escape');await page.keyboard.press('Enter');await page.locator('#composer-controls-panel').waitFor({state:'visible'});assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'false');assert.equal(await page.locator('#prompt').inputValue(),'/model');await page.keyboard.press('Escape');await page.locator('#prompt').fill(draftText);await setMini(page,true);
  assert.equal(calls.some(call=>['/api/connect','/api/send','/api/model'].includes(call.path)),false);
  checks.push('explicit Alt+P and /model reveal standard settings with their visible anchor while preserving input and without sending or changing a model');
  for(const width of [1440,1000,640]){
   await page.setViewportSize({width,height:1000});await settle(page);
   for(const theme of ['light','dark']){await setTheme(f,theme);const selected=await iconControls(page);await setMini(page,false);const unselected=await iconControls(page);assert.notDeepEqual(selected.paint,unselected.paint,'selected treatment must remain visible in '+theme);await setMini(page,true);geometry.push(await visibleMini(page));if(width>700)assert.equal(await page.locator('#session-search').isVisible(),true);await screenshot(page,'simple-view-'+width+'-'+theme);}
   if(width===640){await page.locator('#sidebar-toggle').click();await page.locator('#session-search').waitFor({state:'visible'});assert.equal(await page.locator('#new-chat').isVisible(),true);for(let index=0;index<35;index++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.getElementById('sidebar-panel').contains(document.activeElement)),true,'mobile overlay must contain Tab focus');}await screenshot(page,'simple-view-640-sidebar-dark');await page.keyboard.press('Escape');assert.equal((await bounds(page)).overflow,false);assert.equal(await page.locator('#sidebar-toggle').evaluate(node=>document.activeElement===node),true);}
  }
  checks.push('light/dark mini layout at 1440, 1000 and 640 pixels has no page overflow; narrow sidebar opens and dismisses');
  checks.push('the visible theme button performs real appearance API toggles in simple view at all widths; icon geometry and selected styling remain correct in both themes');
  await page.setViewportSize({width:1440,height:1000});await setTheme(f,'light');await settle(page);
  for(const shortcut of ['Shift+Tab','Alt+m']){await page.locator('#prompt').focus();await page.keyboard.press(shortcut);await page.locator('#composer-control-status').waitFor({state:'visible'});assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'false');assert.equal(await page.locator('#composer-permission').isVisible(),true);assert.equal(await page.locator('#prompt').inputValue(),draftText);await setMini(page,true);}
  assert.equal(calls.some(call=>call.path==='/api/permission-mode'),false);
  checks.push('explicit Shift+Tab/Alt+M permission selection exits mini without losing draft or changing permission when only the current option is available');
  await page.evaluate(()=>WorkspaceCapabilities.open());await page.locator('#capabilities-view').waitFor({state:'visible'});assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'false');await page.locator('#capabilities-back').click();await page.locator('#conversation').waitFor({state:'visible'});assert.equal(await page.locator('#prompt').inputValue(),draftText);await setMini(page,true);
  checks.push('explicit skills catalog opens in standard mode and returns to the same draft');
  await page.locator('#sessions [data-session-id="mini-running"] .session').click();await page.waitForFunction(()=>active?.id==='mini-running');assert.equal(await page.locator('#stop').isVisible(),true);assert.equal(await page.locator('#send').isVisible(),false);
  await preserveToggle(f,false);await preserveToggle(f,true);eventQueues.get('mini-running').push({seq:1,type:'assistant_delta',data:{id:'live-mini',text:'간편 화면에서도 실시간 응답이 계속됩니다.'}});await page.getByText('간편 화면에서도 실시간 응답이 계속됩니다.',{exact:false}).first().waitFor({state:'visible'});assert.equal(await page.locator('#stop').isVisible(),true);
  checks.push('live running session keeps stop control and receives actual polled response after toggling');
  await page.locator('#sessions [data-session-id="mini-approval"] .session').click();await page.waitForFunction(()=>active?.id==='mini-approval');const approval=page.locator('#requests [data-request-id="approval-1"]');await approval.waitFor({state:'visible'});
  await preserveToggle(f,false);await preserveToggle(f,true);assert.equal(await approval.getByRole('button',{name:'확인하고 승인'}).isVisible(),true);assert.equal(await approval.getByRole('button',{name:'거절',exact:true}).isVisible(),true);await screenshot(page,'simple-view-approval-light');
  assert.equal(calls.some(call=>['/api/respond','/api/stop','/api/connect','/api/send','/api/restart'].includes(call.path)),false);
  checks.push('approval content and both response controls survive mini switches; no approval/stop/send/reconnect API action');
  await page.evaluate(()=>WorkspaceDraftPersistence.flush());await page.reload();await f.ready();assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'true');assert.equal(await page.locator('#prompt').inputValue(),draftText);assert.equal(await page.locator('#attachments .attachment').isVisible(),true);assert.equal(await page.evaluate(()=>active.id),'mini-approval');
  await setMini(page,false);assert.ok(Math.abs((await page.locator('#inspector-panel').boundingBox()).width-400)<1);
  const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('workspace.layout.v1')));assert.deepEqual(stored,normalPreferences);
  checks.push('reload persists mini, selected approval session and draft; exit restores saved 400px inspector and panel preferences');
  await page.locator('#mode-chat').click();await page.locator('#sessions [data-session-id="mini-chat"] .session').click();await page.waitForFunction(()=>active?.id==='mini-chat');await preserveToggle(f,true);assert.equal(await page.locator('#mode-chat').getAttribute('aria-pressed'),'true');assert.equal(await page.locator('.app').evaluate(node=>node.classList.contains('chat-mode')),true);await preserveToggle(f,false);
  checks.push('general conversation semantic mode and selected session are unchanged by mini toggles');
 }finally{await f.finish();}
}
async function runPreferences(browser){
 const original={sidebarCollapsed:true,inspectorCollapsed:true,inspectorWidth:460,miniMode:false};const f=await fixture(browser,{preferences:original}),{page}=f;
 try{
  assert.equal(await page.locator('#session-search').isVisible(),false);await setMini(page,true);assert.equal(await page.locator('#session-search').isVisible(),true);await setMini(page,false);
  const state=await page.evaluate(()=>WorkspaceLayout.snapshot());assert.equal(state.sidebarCollapsed,true);assert.equal(state.inspectorCollapsed,true);assert.deepEqual(state.preferences,original);
  await page.locator('#materials-button').click();assert.ok(Math.abs((await page.locator('#inspector-panel').boundingBox()).width-460)<1);
  checks.push('mini temporarily shows desktop task list and restores previously collapsed panels plus saved inspector width');
 }finally{await f.finish();}
 const restricted=await fixture(browser,{restricted:true});try{await setMini(restricted.page,true);await visibleMini(restricted.page);await setMini(restricted.page,false);assert.equal(await restricted.page.locator('#attach').isVisible(),true);checks.push('blocked layout storage allows mini entry/exit with in-memory preferences');}finally{await restricted.finish();}
}
async function runSubmit(browser){
 for(const action of ['button','Enter']){
  const f=await fixture(browser),{page,calls}=f;
  try{
   await setMini(page,true);const before=calls.length;
   if(action==='button')await page.locator('#send').click();else{await page.locator('#prompt').focus();await page.keyboard.press('Enter');}
   await page.waitForFunction(()=>!sending&&document.getElementById('prompt').value==='');
   const sent=calls.slice(before).filter(call=>call.path==='/api/send');assert.equal(sent.length,1);assert.deepEqual(sent[0].body,{id:'mini-work',text:draftText,attachments:[attachment],trusted:true});
   assert.equal(await page.evaluate(()=>active.id),'mini-work');assert.equal(await page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'true');assert.equal(await page.locator('#attachments .attachment').count(),0);
   assert.equal(calls.slice(before).some(call=>['/api/create','/api/connect','/api/restart'].includes(call.path)),false);assert.equal(await page.locator('#conversation').getByText(draftText,{exact:true}).isVisible(),true);
  }finally{await f.finish();}
 }
 checks.push('mini Send button and Enter each submit the existing session draft and attachment exactly once without creating or reconnecting a session');
}
async function runDurableLayout(browser){
 const original={...normalPreferences,sidebarCollapsed:true,inspectorCollapsed:true,inspectorWidth:460},server=layoutServer(original,220);
 const first=await fixture(browser,{server,seedLocal:false,origin:'http://workspace.test:48101'});
 try{
  assert.equal(await first.page.evaluate(()=>__miniInitialLayout),null);assert.deepEqual(await first.page.evaluate(()=>WorkspaceLayout.snapshot().preferences),original);assert.equal(server.writes.length,0,'hydrating backend preferences must not save them again');
  await first.page.evaluate(()=>{for(let index=0;index<5;index++)document.getElementById('mini-mode-toggle').click();});
  const expected={...original,miniMode:true};await waitForLayout(server,expected);
  assert.equal(await first.page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'true');assert.equal(server.maxInFlight,1,'layout persistence must serialize requests');assert.ok(server.writes.length>=1&&server.writes.length<=2,'rapid flips must coalesce into at most the active and latest queued writes');
  assert.equal(first.calls.some(call=>['/api/create','/api/connect','/api/send','/api/permission-mode'].includes(call.path)),false);
 }finally{await first.finish();}
 checks.push('five rapid mini switches coalesce with one layout save in flight and persist the final ON state without Claude/session effects');
 const saved=clone(server.layout),writes=server.writes.length;
 const restarted=await fixture(browser,{server,seedLocal:false,origin:'http://workspace.test:49202'});
 try{
  assert.equal(await restarted.page.evaluate(()=>__miniInitialLayout),null,'restart fixture must begin without local layout storage');assert.equal(await restarted.page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'true');assert.deepEqual(await restarted.page.evaluate(()=>WorkspaceLayout.snapshot().preferences),saved);
  assert.equal(server.writes.length,writes,'fresh-origin restoration is read-only');assert.equal(await restarted.page.locator('#session-search').isVisible(),true);await visibleMini(restarted.page);await screenshot(restarted.page,'simple-view-restart-restored-light');
  await preserveToggle(restarted,false);assert.deepEqual(await restarted.page.evaluate(()=>WorkspaceLayout.snapshot().preferences),original);assert.equal(await restarted.page.locator('#session-search').isVisible(),false);assert.equal(await restarted.page.locator('#inspector-panel').isVisible(),false);
 }finally{await restarted.finish();}
 checks.push('new browser context on a different server port restores mini ON from backend with empty localStorage; exit restores collapsed panels and saved460px width');
 const empty=await fixture(browser,{server:layoutServer(null),seedLocal:false});
 try{assert.equal(await empty.page.locator('#mini-mode-toggle').getAttribute('aria-pressed'),'false');assert.equal(empty.server.writes.length,0);await preserveToggle(empty,true);}finally{await empty.finish();}
 checks.push('first launch with no backend or browser layout uses full mode and saves only an explicit user change');
}
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{await runMain(browser);await runPreferences(browser);await runSubmit(browser);await runDurableLayout(browser);const result={passed:true,checks,geometry,pageErrors};if(output)fs.writeFileSync(path.join(output,'simple-view-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));}
 catch(error){if(output)fs.writeFileSync(path.join(output,'simple-view-checks.json'),JSON.stringify({passed:false,checks,geometry,pageErrors,error:error.stack},null,2));throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
