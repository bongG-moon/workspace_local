/* Shipped sidebar HTML/CSS/JS with synthetic HTTP fixtures only.
 * No Claude process, personal setting, user file or real scheduling action.
 * NODE_PATH=<playwright node_modules> node this-file --output <evidence-dir>
 * Run --baseline before changing the sidebar to retain comparison geometry.
 */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const checks=[],geometry=[],allErrors=[];
const clone=value=>JSON.parse(JSON.stringify(value));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const row=(id,title,kind='work')=>({id,kind,title,workspace:'C:/fixture/'+id,state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[],connection:null});
async function fixture(browser){
 const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
 const page=await context.newPage(),calls=[],errors=[],drafts=new Map();
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
  else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
  else if(p==='/api/events'){await sleep(150);result={events:[]};}
  await route.fulfill({status:result?200:404,json:clone(result||{error:'not found'})});
 });
 await page.goto('http://workspace.test/#token=fixture');
 await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
 await page.evaluate(()=>document.fonts.ready);
 page.setDefaultTimeout(8000);
 return {page,calls,rows,errors,getGroups:()=>groups,finish:async()=>{allErrors.push(...errors);assert.deepEqual(errors,[]);await context.close();}};
}
async function bounds(page){return page.evaluate(()=>{
 const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};
 const ids=['sidebar-panel','sessions','schedule-overview-open','settings-open','shortcuts-open','help'];
 return {viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,layout:WorkspaceLayout.snapshot(),...Object.fromEntries(ids.map(id=>[id,rect(document.getElementById(id))])),footer:rect(document.querySelector('.sidebar-footer')),font:getComputedStyle(document.querySelector('.session-title')).fontFamily};
 });}
async function screenshot(page,name){await page.evaluate(()=>{document.getElementById('toast').hidden=true;});if(output)await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});}
async function theme(page,value){if(await page.evaluate(()=>document.documentElement.dataset.theme)!==value){await page.locator('#appearance-toggle').click();await page.waitForFunction(t=>document.documentElement.dataset.theme===t,value);}}
async function openSidebar(page){if(await page.evaluate(()=>WorkspaceLayout.snapshot().sidebarCollapsed))await page.locator('#sidebar-toggle').click();}
async function closeDialog(page,id){await page.locator('#'+id).evaluate(node=>node.close());await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function run(browser){
 const f=await fixture(browser),{page,calls,errors}=f;
 try{
  if(process.argv.includes('--baseline')){const result=await bounds(page);if(output)fs.writeFileSync(path.join(output,'sidebar-baseline.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({baseline:result}));return;}
  assert.equal(await page.locator('#palette-open').isVisible(),false);
  assert.equal(await page.locator('.primary-nav #home-button').count(),0);
  assert.equal(await page.locator('.sidebar-head #home-button.brand').isVisible(),true);
  assert.equal(await page.locator('#capabilities-open').isVisible(),true);
  assert.equal(await page.locator('#session-search').isVisible(),true);
  for(const id of ['schedule-overview-open','settings-open','shortcuts-open','help'])assert.equal(await page.locator('#'+id).evaluate(node=>!!node.closest('.sidebar-footer')),true);
  const desktop=await bounds(page),footerIds=['schedule-overview-open','settings-open','shortcuts-open','help'],rects=footerIds.map(id=>desktop[id]),xs=[...new Set(rects.map(r=>Math.round(r.x)))],ys=[...new Set(rects.map(r=>Math.round(r.y)))];
  assert.equal(xs.length,2);assert.equal(ys.length,2);assert.ok(rects.every(r=>Math.abs(r.width-rects[0].width)<1));
  if(output&&fs.existsSync(path.join(output,'sidebar-baseline.json'))){const previous=JSON.parse(fs.readFileSync(path.join(output,'sidebar-baseline.json')));assert.ok(desktop.sessions.height>previous.sessions.height+60);desktop.previousListHeight=previous.sessions.height;desktop.listHeightGain=desktop.sessions.height-previous.sessions.height;}
  checks.push('quick launcher and redundant home entry are hidden; skills/search remain','schedule/settings/shortcuts/help form an equal-width 2 by 2 footer','conversation list gains at least 60 pixels against the saved pre-change layout');
  const group='.task-group-header[data-group-id="large"]',toggle=page.locator(group+' .task-group-toggle'),children=id=>page.locator('#sessions > .session-row[data-group-id="'+id+'"]'),more=()=>page.locator('#sessions .task-group-overflow[data-group-id="large"] .task-group-more');
  assert.equal(await children('large').count(),5);assert.equal(await children('five').count(),5);
  assert.equal(await page.locator('#sessions .task-group-overflow[data-group-id="five"] .task-group-more').count(),0);
  await screenshot(page,'sidebar-preview-five-light');await theme(page,'dark');await screenshot(page,'sidebar-preview-five-dark');await theme(page,'light');
  const icon=()=>page.locator(group+' .task-folder-icon').evaluate(node=>getComputedStyle(node).maskImage);
  const openIcon=await icon();
  await more().click();assert.equal(await children('large').count(),8);
  await more().click();assert.equal(await children('large').count(),5);
  await toggle.click();await page.waitForFunction(()=>document.querySelector('.task-group-header[data-group-id="large"] .task-group-toggle').getAttribute('aria-expanded')==='false');
  const closedIcon=await icon();assert.notEqual(openIcon,closedIcon);assert.equal(await children('large').count(),0);
  await toggle.click();await page.waitForFunction(()=>document.querySelector('.task-group-header[data-group-id="large"] .task-group-toggle').getAttribute('aria-expanded')==='true');
  assert.equal(await icon(),openIcon);
  await page.locator('#session-search').fill('숨겨진 일곱');assert.equal(await page.locator('#sessions [data-session-id="group-7"]').count(),1);
  await page.locator('#sessions [data-session-id="group-7"] .session').click();await page.waitForFunction(()=>active?.id==='group-7');
  await page.locator('#session-search').fill('');assert.equal(await page.locator('#sessions [data-session-id="group-7"] .session.active').isVisible(),true);
  checks.push('open/closed folder icons follow expansion state','five entries need no more button; eight entries expand and collapse locally','search finds a hidden entry and the selected entry remains visible after clearing search');
  const selected='group-7';await page.locator('#prompt').fill('브랜드 홈을 눌러도 이 초안을 유지해 주세요.');
  const before=calls.filter(c=>['/api/connect','/api/create','/api/send'].includes(c.path)).length;
  await page.locator('.sidebar-head .brand').click();await page.waitForFunction(()=>active===null);assert.equal(new URL(page.url()).hostname,'workspace.test');
  await page.locator('#session-search').fill('숨겨진 일곱');await page.locator('#sessions [data-session-id="'+selected+'"] .session').click();await page.waitForFunction(id=>active?.id===id,selected);
  assert.equal(await page.locator('#prompt').inputValue(),'브랜드 홈을 눌러도 이 초안을 유지해 주세요.');assert.equal(calls.filter(c=>['/api/connect','/api/create','/api/send'].includes(c.path)).length,before);
  await page.locator('#session-search').fill('');
  await page.keyboard.press('Control+Shift+P');await page.locator('#palette-dialog').waitFor({state:'visible'});await page.locator('#palette-close').click();
  for(const [button,dialog] of [['schedule-overview-open','schedule-overview-dialog'],['settings-open','settings-dialog'],['shortcuts-open','shortcuts-dialog'],['help','help-dialog']]){await page.locator('#'+button).click();await page.locator('#'+dialog).waitFor({state:'visible'});await closeDialog(page,dialog);}
  checks.push('brand returns to current-mode home without page reload or new connection; session draft survives','Ctrl+Shift+P still opens quick launch','all four footer entries open their original dialogs');
  await page.locator('#home-button').click();await page.waitForFunction(()=>active===null);
  for(const id of ['large','five']){const button=page.locator('.task-group-header[data-group-id="'+id+'"] .task-group-toggle');if(await button.getAttribute('aria-expanded')==='true'){await button.click();await page.waitForFunction(id=>document.querySelector('.task-group-header[data-group-id="'+id+'"] .task-group-toggle').getAttribute('aria-expanded')==='false',id);}}
  const source=await page.locator('#sessions [data-session-id="outside"] .session').boundingBox(),target=await page.locator('#sessions .task-group-empty[data-group-id="empty"]').boundingBox();
  await page.mouse.move(source.x+35,source.y+source.height/2);await page.mouse.down();await page.mouse.move(target.x+Math.min(45,target.width/2),target.y+target.height/2,{steps:12});await page.mouse.up();
  await page.waitForFunction(()=>boot.sessionGroups.assignments.outside==='empty');assert.equal(await page.locator('#sessions .session-row[data-group-id="empty"][data-session-id="outside"]').count(),1);assert.equal(await page.evaluate(()=>active),null);
  await toggle.click();await page.waitForFunction(()=>document.querySelector('.task-group-header[data-group-id="large"] .task-group-toggle').getAttribute('aria-expanded')==='true');
  checks.push('dragging a task by its title into an expanded empty folder still moves it without opening a conversation');
  for(const width of [1440,680,420]){
   await page.setViewportSize({width,height:1000});
   for(const value of ['light','dark']){
    if(await page.evaluate(()=>WorkspaceLayout.snapshot().sidebarOverlay))await page.keyboard.press('Escape');
    await theme(page,value);await openSidebar(page);await page.locator('#sessions').evaluate(node=>node.scrollTop=0);
    const state=await bounds(page);assert.equal(state.overflow,false);assert.ok(state.sessions.height>200);assert.match(state.font,/Noto/);assert.ok(state.footer.bottom<=1001);
    for(const id of footerIds){assert.ok(state[id].x>=state['sidebar-panel'].x);assert.ok(state[id].right<=state['sidebar-panel'].right+1);}
    geometry.push({width,theme:value,state:'expanded',...state});await screenshot(page,`sidebar-${width}-${value}`);
    await page.locator('#sidebar-toggle').click();const collapsed=await bounds(page);assert.equal(collapsed.layout.sidebarCollapsed,true);assert.equal(collapsed.overflow,false);
    for(const id of ['new-chat','capabilities-open',...footerIds]){assert.equal(await page.locator('#'+id).isVisible(),true);const button=await page.locator('#'+id).boundingBox(),icon=await page.locator('#'+id+' .icon').boundingBox();assert.ok(icon.width>=14&&icon.width<=24);assert.ok(Math.abs(button.x+button.width/2-icon.x-icon.width/2)<1);assert.ok(Math.abs(button.y+button.height/2-icon.y-icon.height/2)<1);}
    assert.equal(await page.locator('.primary-nav #home-button').count(),0);assert.equal(await page.locator('.sidebar-head #home-button').isVisible(),true);assert.equal(await page.locator('#palette-open').isVisible(),false);
    geometry.push({width,theme:value,state:'collapsed',...collapsed});await screenshot(page,`sidebar-rail-${width}-${value}`);
   }
  }
  checks.push('light/dark at 1440, 680 and 420: footer and list fit without horizontal overflow','collapsed rail uses the new action list with centered sized icons');
  await page.setViewportSize({width:1280,height:720});await openSidebar(page);const short=await bounds(page);assert.ok(short.sessions.height>=300);assert.ok(short.footer.bottom<=720);geometry.push({width:1280,height:720,theme:'dark',state:'expanded',...short});await screenshot(page,'sidebar-1280x720-dark');
  await page.evaluate(()=>WorkspaceAppUpdates.observe({status:'available',currentVersion:'0.23.26',autoCheck:false,canInstall:true,source:{provider:'gitlab',label:'테스트 배포'},release:{version:'0.23.27',title:'사이드바 검증',notes:'화면 배치 검증용 가상 업데이트입니다.'}}));
  assert.equal(await page.locator('#app-update-sidebar-badge').isVisible(),true);
  for(const collapsed of [false,true]){
   if(collapsed)await page.locator('#sidebar-toggle').click();
   const badge=await page.locator('#app-update-sidebar-badge').boundingBox(),settings=await page.locator('#settings-open').boundingBox(),measure=await bounds(page);
   assert.ok(badge.y+badge.height<settings.y);assert.ok(measure.footer.bottom<=720);if(!collapsed)assert.ok(measure.sessions.height>=250);
   await screenshot(page,'sidebar-update-'+(collapsed?'rail':'expanded')+'-1280x720-dark');
   await page.locator('#app-update-sidebar-badge').click();await page.locator('#app-update-dialog').waitFor({state:'visible'});await closeDialog(page,'app-update-dialog');
  }
  await page.evaluate(()=>WorkspaceAppUpdates.observe({status:'current',currentVersion:'0.23.26',autoCheck:false,release:null}));assert.equal(await page.locator('#app-update-sidebar-badge').isVisible(),false);
  checks.push('720-pixel height keeps usable list space; available update badge fits above footer in expanded and collapsed layouts and opens notes without installing');
  await page.setViewportSize({width:1440,height:1000});await openSidebar(page);await page.locator('#mode-chat').click();
  assert.equal(await page.locator('#capabilities-open').isVisible(),false);assert.equal(await page.locator('#schedule-overview-open').isVisible(),false);
  assert.equal(await page.locator('#sessions [data-session-id="chat-1"]').count(),1);assert.equal(await page.locator('#sessions .task-group-header').count(),0);
  const chatBounds=await bounds(page);assert.ok(chatBounds.help.width>=chatBounds['settings-open'].width);await screenshot(page,'sidebar-chat-dark');
  await page.locator('#sessions [data-session-id="chat-1"] .session').click();await page.waitForFunction(()=>active?.id==='chat-1');await page.locator('.sidebar-head .brand').click();await page.waitForFunction(()=>active===null&&currentMode==='chat');
  checks.push('chat mode retains separate chat list and hides work-only skills/schedules; brand opens chat home');
  assert.equal(calls.some(c=>['/api/connect','/api/create','/api/send'].includes(c.path)),false);
  const result={passed:true,checks,geometry,desktop,requests:calls.filter(c=>c.path==='/api/session-groups'),pageErrors:errors};
  if(output)fs.writeFileSync(path.join(output,'sidebar-layout-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await f.finish();}
}
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});try{await run(browser);}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
