/* Real layout code and browser pointer capture; synthetic API data only. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const checks=[],errors=[];
let layout=null;
const row={id:'resize-work',kind:'work',title:'월간 실적 보고서',workspace:'C:/fixture/reports',state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[]};
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  await page.addInitScript(()=>{
   globalThis.__layoutWrites=[];
   const original=Storage.prototype.setItem;
   Storage.prototype.setItem=function(key,value){if(key==='workspace.layout.v1')__layoutWrites.push(JSON.parse(value));return original.call(this,key,value)};
   document.addEventListener('pointerdown',e=>{if(e.target.id==='inspector-resizer')globalThis.__resizePointerId=e.pointerId},true);
  });
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://workspace.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(!p.startsWith('/api/')){
    const filename=path.resolve(web,p==='/'?'index.html':p.slice(1));
    if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});
    return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.svg':'image/svg+xml','.png':'image/png'})[path.extname(filename)]||'application/octet-stream'});
   }
   const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
   let result={ok:true};
   if(p==='/api/bootstrap')result={sessions:[row],demo:false,appearance:{theme:'light'},layout,executionMode:'normal',sessionGroups:{groups:[],assignments:{},revision:0},runtime:{entry:'fixture'}};
   else if(p==='/api/layout')result=layout={...body};
   else if(p==='/api/drafts')result=req.method()==='GET'?{entries:[],selectedId:'home'}:{revision:1};
   else if(p==='/api/session')result=row;
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:[row]};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/events'){await new Promise(r=>setTimeout(r,100));result={events:[]};}
   await route.fulfill({json:result});
  });
  const settle=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const ready=async()=>{await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);await page.evaluate(()=>document.fonts.ready);await settle()};
  await page.goto('http://workspace.test/#token=fixture');await ready();
  page.setDefaultTimeout(7000);
  const handle=page.locator('#inspector-resizer');
  const geometry=()=>page.evaluate(()=>({panel:document.getElementById('inspector-panel').getBoundingClientRect().width,main:document.querySelector('main').getBoundingClientRect().width,viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,saved:JSON.parse(localStorage.getItem('workspace.layout.v1')),writes:__layoutWrites.length,dragging:document.querySelector('.app').classList.contains('inspector-resizing')}));
  const width=async expected=>{await page.waitForFunction(w=>Math.abs(document.getElementById('inspector-panel').getBoundingClientRect().width-w)<1,expected);assert.equal((await geometry()).overflow,false)};
  const startDrag=async delta=>{const r=await handle.boundingBox();assert.ok(r);await page.mouse.move(r.x+r.width/2,r.y+160);await page.mouse.down();await page.mouse.move(r.x+r.width/2-delta,r.y+160,{steps:12});await settle()};
  const drag=async delta=>{await startDrag(delta);await page.mouse.up();await settle()};
  const shot=async name=>{if(output){await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});if(name.startsWith('panel-'))await page.screenshot({path:path.join(output,name+'-header.png'),clip:{x:0,y:0,width:page.viewportSize().width,height:120},animations:'disabled'});}};
  const opener=page.locator('#materials-button'),closer=page.locator('#close-materials');
  const panelControls=async(open,available=true)=>{await settle();assert.equal(await page.locator('#inspector-panel').isVisible(),open);assert.equal(await opener.isVisible(),available&&!open,'header opener appears only while the panel is closed');assert.equal(await closer.isVisible(),open,'panel close button appears only while the panel is open');};
  const focused=async locator=>assert.equal(await locator.evaluate(node=>document.activeElement===node),true,'focus must land on the visible panel action');
  await width(292);assert.equal(await handle.getAttribute('role'),'separator');
  await panelControls(true);await shot('panel-open-desktop');await closer.focus();await page.keyboard.press('Enter');await panelControls(false);await focused(opener);await shot('panel-closed-desktop');
  await page.keyboard.press('Space');await panelControls(true);await focused(closer);
  checks.push('desktop has exactly one visible panel action; Enter closes from inside and Space reopens from the header with focus on the visible counterpart');
  const scrollbarHit=await page.evaluate(()=>{
   const area=document.querySelector('.work-area'),filler=document.createElement('div');filler.style.height='3000px';area.append(filler);
   const r=area.getBoundingClientRect(),main=document.querySelector('main').getBoundingClientRect(),divider=document.getElementById('inspector-resizer').getBoundingClientRect();
   const result={overlap:divider.left<main.right,intercepts:document.elementFromPoint(r.right-2,r.top+80)?.id==='inspector-resizer'};filler.remove();return result;
  });
  assert.deepEqual(scrollbarHit,{overlap:false,intercepts:false});checks.push('divider does not intercept conversation scrollbar');
  const initial=await geometry();
  await startDrag(200);await width(492);assert.equal((await geometry()).writes,initial.writes,'moves must not persist settings');
  await page.mouse.up();await settle();assert.equal((await geometry()).writes,initial.writes+1);assert.equal((await geometry()).saved.inspectorWidth,492);
  checks.push('real pointer drag; one save only at release');await shot('resized-light');
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');await shot('resized-dark');
  await drag(1000);await width(560);assert.equal(await handle.getAttribute('aria-valuenow'),'560');
  const beforeShrink=(await geometry()).writes;
  await page.setViewportSize({width:1101,height:1000});await settle();let g=await geometry();
  assert.ok(g.main>=419&&g.panel<560);assert.equal(g.saved.inspectorWidth,560);assert.equal(g.writes,beforeShrink);assert.equal(g.overflow,false);
  await page.locator('#sidebar-toggle').click();await settle();await width(560);
  await page.locator('#sidebar-toggle').click();await settle();assert.ok((await geometry()).main>=419);
  await page.setViewportSize({width:1440,height:1000});await settle();await width(560);
  checks.push('maximum and central minimum; sidebar/window resizing preserves chosen width');
  await closer.click();await panelControls(false);await focused(opener);assert.equal(await handle.isVisible(),false);
  await opener.click();await width(560);await panelControls(true);await focused(closer);
  await page.setViewportSize({width:1000,height:1000});await panelControls(false);await focused(opener);assert.equal(await handle.isVisible(),false);await shot('panel-closed-narrow');
  await page.keyboard.press('Enter');await panelControls(true);await focused(closer);assert.equal(await handle.isVisible(),false);await shot('panel-open-narrow');
  for(let index=0;index<20;index++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.getElementById('inspector-panel').contains(document.activeElement)),true,'narrow inspector traps keyboard focus');}
  await closer.focus();await page.keyboard.press('Enter');await panelControls(false);await focused(opener);
  await opener.click();await panelControls(true);await page.keyboard.press('Escape');await panelControls(false);await focused(opener);
  await page.setViewportSize({width:1440,height:1000});await settle();await width(560);await panelControls(true);await focused(closer);
  checks.push('close/reopen and narrow overlay preserve saved width');
  checks.push('desktop/narrow resize moves focus to the surviving panel button; overlay close and Escape restore header focus and keep one visible action');
  for(const mode of ['catalog-open','chat-mode']){
   await page.evaluate(mode=>{document.querySelector('.app').classList.add(mode);WorkspaceLayout.refresh()},mode);
   assert.equal(await handle.isVisible(),false);
   await page.evaluate(mode=>{document.querySelector('.app').classList.remove(mode);WorkspaceLayout.refresh()},mode);await width(560);
  }
  checks.push('catalog and general chat hide divider');
  for(const cancel of ['escape','pointercancel','lostcapture','blur']){
   const writes=(await geometry()).writes;await startDrag(-100);await width(460);
   if(cancel==='escape')await page.keyboard.press('Escape');
   else if(cancel==='pointercancel')await handle.evaluate(el=>el.dispatchEvent(new PointerEvent('pointercancel',{pointerId:__resizePointerId,bubbles:true})));
   else if(cancel==='lostcapture')await handle.evaluate(el=>el.releasePointerCapture(__resizePointerId));
   else await page.evaluate(()=>window.dispatchEvent(new Event('blur')));
   await settle();await page.mouse.up();await width(560);assert.equal((await geometry()).writes,writes);assert.equal((await geometry()).dragging,false);
  }
  checks.push('Escape, cancellation, lost pointer capture and blur restore starting width');
  const writes=(await geometry()).writes;await handle.dblclick();await width(292);assert.equal((await geometry()).writes,writes+1);
  await handle.focus();await page.keyboard.press('ArrowLeft');await width(302);
  await page.keyboard.press('Shift+ArrowRight');await width(260);
  await page.keyboard.press('End');await width(560);await page.keyboard.press('Home');await width(260);
  await page.keyboard.press('Enter');await width(292);
  assert.equal(await handle.getAttribute('aria-valuemin'),'260');assert.equal(await handle.getAttribute('aria-valuemax'),'560');
  checks.push('double click reset; keyboard arrows, Home, End, Enter and ARIA values');
  await drag(108);await width(400);await page.reload();await ready();await width(400);
  checks.push('reload restores saved width');
  await startDrag(50);await page.setViewportSize({width:1000,height:1000});await settle();await page.mouse.up();assert.equal((await geometry()).dragging,false);assert.equal((await geometry()).saved.inspectorWidth,400);
  await page.setViewportSize({width:1440,height:1000});await settle();await width(400);
  checks.push('resize breakpoint during drag cancels without saving');
  await panelControls(true);await closer.focus();await page.evaluate(()=>WorkspaceLayout.setMiniMode(true));await panelControls(false,false);await focused(page.locator('#mini-mode-toggle'));await shot('panel-simple-view');
  await page.keyboard.press('Enter');await panelControls(true);await width(400);await shot('panel-restored-desktop');
  await closer.click();await panelControls(false);await page.locator('#mini-mode-toggle').click();await panelControls(false,false);await page.locator('#mini-mode-toggle').click();await panelControls(false);await opener.click();await panelControls(true);await focused(closer);await width(400);
  checks.push('simple view hides both panel actions; returning restores the previous open/closed preference and inspector width without duplicate controls');
  assert.deepEqual(errors,[]);
  const result={passed:true,checks,final:await geometry(),pageErrors:errors};
  if(output)fs.writeFileSync(path.join(output,'inspector-resize-checks.json'),JSON.stringify(result,null,2));
  await context.close();console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
