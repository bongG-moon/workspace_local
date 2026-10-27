/* Real DOM/pointer regression checks; fixture sessions and APIs only. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=path.resolve(process.argv[2]||'outputs/list-scope-ui');
const origin='http://127.0.0.1:17347';
const row=(id,title)=>({id,title,kind:'work',workspace:'C:/fixture/'+id,state:'idle',trusted:true,
  created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[]});
const rows=[row('A','월간 보고서'),row('B','실적 분석'),row('C','회의 자료')];
let order={manual:true,ids:['A','B','C']};
const calls=[],errors=[],measurements=[];
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
const title=id=>`#sessions [data-session-id="${id}"] > .session`;
const grip=id=>`#sessions [data-session-id="${id}"] > .session-drag`;
async function center(page,selector){await page.locator(selector).waitFor({state:'visible'});const r=await page.locator(selector).boundingBox();assert.ok(r,selector);return {x:r.x+r.width/2,y:r.y+r.height/2};}
async function settle(page){await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'));await page.evaluate(()=>document.fonts.ready);}

(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route(origin+'/**',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(!p.startsWith('/api/')){
    const file=path.join(web,p==='/'?'index.html':p.slice(1));
    if(!file.startsWith(web+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
    return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.png':'image/png','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'});
   }
   const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
   calls.push({path:p,body,query:url.search});let result={ok:true};
   if(p==='/api/bootstrap')result={sessions:rows,sessionOrder:order,demo:false,appearance:{theme:'light'},executionMode:'normal',sessionGroups:{groups:[],assignments:{},revision:0},runtime:{entry:'fixture'}};
   else if(p==='/api/drafts')result=req.method()==='GET'?{entries:[],selectedId:'home'}:{revision:1};
   else if(p==='/api/session')result=rows.find(r=>r.id===url.searchParams.get('id'));
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:rows};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/llm-usage')result={ok:false,reason:'not_configured'};
   else if(p==='/api/events'){await new Promise(r=>setTimeout(r,80));result={events:[]};}
   else if(p==='/api/session/reorder'){
    const ids=order.ids.filter(id=>id!==body.id);ids.splice(ids.indexOf(body.targetId)+(body.position==='after'?1:0),0,body.id);
    order={manual:true,ids};result={sessionOrder:order};
   }else if(p==='/api/customizations')result={scope:url.searchParams.get('scope')||'local',settingsPath:'C:/fixture/.claude/settings.json',revision:'fixture',skills:[{name:'report',description:'보고서 작성',source:'user',visibility:'on',override:null,locked:false}],plugins:[{name:'format@test',enabled:true,locked:false}]};
   await route.fulfill({json:result});
  });
  await page.goto(origin+'/#token=fixture');
  await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
  await page.locator(title('A')).waitFor();await settle(page);
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  let clickCount=0;
  // Exceeds the former 6px drag threshold, while remaining inside the title.
  // Force a status/list render between pointerdown and pointerup as in streaming.
  for(let round=0;round<2;round++)for(const dx of [0,6,12,24])for(const id of ['A','B','C']){
   const pos=await center(page,title(id));await page.mouse.move(pos.x,pos.y);await page.mouse.down();
   await page.evaluate(()=>{sessions[1].state='running';renderSessions();});
   await page.mouse.move(pos.x+dx,pos.y+2);await page.mouse.up();
   await page.waitForFunction(id=>active?.id===id,id);clickCount++;
   assert.equal(await page.evaluate(()=>sessionPointerDrag),null);
   assert.equal(calls.filter(c=>c.path==='/api/session/reorder').length,0);
  }
  // Releasing over a different row must neither open it nor reorder anything.
  const selected=await page.evaluate(()=>active.id),from=await center(page,title('A')),to=await center(page,title('B'));
  await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(to.x,to.y);await page.mouse.up();
  assert.equal(await page.evaluate(()=>active.id),selected);assert.deepEqual(order.ids,['A','B','C']);
  // Intentional grip drag still reorders; keyboard reorder and Escape still work.
  const handle=await center(page,grip('C')),target=await page.locator('#sessions [data-session-id="A"]').boundingBox();
  await page.mouse.move(handle.x,handle.y);await page.mouse.down();await page.mouse.move(target.x+40,target.y+3,{steps:8});await page.mouse.up();
  await page.waitForFunction(()=>!sessionOrderSaving&&boot.sessionOrder.ids[0]==='C');assert.deepEqual(order.ids,['C','A','B']);
  await page.locator(grip('C')).focus();await page.keyboard.press('Alt+ArrowDown');
  await page.waitForFunction(()=>!sessionOrderSaving&&boot.sessionOrder.ids[0]==='A');assert.deepEqual(order.ids,['A','C','B']);
  const cancel=await center(page,grip('A'));await page.mouse.move(cancel.x,cancel.y);await page.mouse.down();await page.mouse.move(cancel.x,cancel.y+50);
  await page.keyboard.press('Escape');await page.mouse.up();assert.deepEqual(order.ids,['A','C','B']);
  await page.locator(title('A')).click();await page.waitForFunction(()=>active?.id==='A');
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});

  for(const theme of ['light','dark'])for(const command of ['/skills','/plugin']){
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.dataset.appearance=theme;},theme);
   await page.locator('#prompt').fill(command);await page.locator('#prompt').press('Enter');
   await page.locator('#customizations-dialog').waitFor({state:'visible'});await settle(page);
   for(const zoom of [1,1.25])for(const value of ['local','user']){
    await page.evaluate(zoom=>{document.documentElement.style.zoom=zoom;},zoom);
    await page.locator('#customizations-scope').selectOption(value);await settle(page);
    const measurement=await page.locator('#customizations-scope').evaluate(select=>{
     const content=select.querySelector('selectedcontent'),r=select.getBoundingClientRect(),t=content.getBoundingClientRect();
     const range=document.createRange();range.selectNodeContents(content);const text=range.getBoundingClientRect();
     return {text:content.textContent,height:r.height,lineOffset:Math.abs((r.top+r.bottom-t.top-t.bottom)/2),textOffset:Math.abs((r.top+r.bottom-text.top-text.bottom)/2),buttonPadding:getComputedStyle(content.parentElement).padding};
    });
    assert.ok(measurement.lineOffset<=1,JSON.stringify(measurement));assert.ok(measurement.textOffset<=2,JSON.stringify(measurement));
    assert.equal(measurement.buttonPadding,'0px');measurements.push({theme,command,zoom,value,...measurement});
   }
   await page.evaluate(()=>{document.documentElement.style.zoom='1';});
   await page.locator('#customizations-dialog').screenshot({path:path.join(output,`scope-${theme}-${command.slice(1)}.png`)});
   // Open the actual native styled picker, then change with the keyboard.
   await page.locator('#customizations-scope').click();await page.keyboard.press('Home');await page.keyboard.press('Enter');
   assert.equal(await page.locator('#customizations-scope').inputValue(),'local');
   await page.locator('#customizations-close').click();await settle(page);
  }
  assert.deepEqual(errors,[]);
  const report={clickCount,cpuSlowdown:4,reorderRequests:calls.filter(c=>c.path==='/api/session/reorder'),measurements,errors};
  fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({clickCount,reorderRequests:report.reorderRequests.length,alignmentCases:measurements.length,errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
