// Opt-in full shipped UI probe; all traffic is restricted to its isolated server.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.WORKSPACE_PLAYWRIGHT_PATH);
let browser;
(async()=>{
 const options=JSON.parse(fs.readFileSync(0,'utf8'));
 const marker=path.join(options.output,options.phase+'-stage.json');
 const stage=value=>fs.writeFileSync(marker,JSON.stringify({stage:value}));
 const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER_PATH});
 const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
 page.setDefaultTimeout(10000);
 const errors=[],requests=[],blocked=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  const url=new URL(request.url());
  if(url.pathname.startsWith('/api/')){
   let body=null;try{body=request.postDataJSON();}catch{}
   requests.push({path:url.pathname,method:request.method(),id:body?.id||null,time:Date.now()});
  }
 });
 await page.route('**/*',route=>{
  if(new URL(route.request().url()).origin!==options.origin){blocked.push(route.request().url());return route.abort();}
  return route.continue();
 });
 const cdp=await page.context().newCDPSession(page);await cdp.send('Performance.enable');
 async function metric(){await cdp.send('HeapProfiler.collectGarbage');const rows=(await cdp.send('Performance.getMetrics')).metrics;return Object.fromEntries(rows.map(row=>[row.name,row.value]));}
 await page.goto(options.origin+'/#token='+options.token);
 await page.waitForFunction(()=>globalThis.WorkspaceDraftPersistence?.isReady()===true);
 await page.waitForFunction(()=>document.querySelectorAll('#sessions .session').length>0);
 const evaluateApi=(route,data)=>page.evaluate(async({route,data})=>api(route,data),{route,data});
 if(options.mode==='upgrade'){
  await page.evaluate(async({work})=>{await selectSession(work);document.getElementById('prompt').value='업데이트 중 유지할 초안';saveDraft();},{work:options.work});
  const result=await page.evaluate(async()=>{
   const requestId='b'.repeat(32),targetVersion='9.0.0';
   const prepared=await api('/api/upgrade',{action:'prepare',requestId,targetVersion});
   await WorkspaceUpgrade.observe(prepared.upgrade,{items:[],total:0});
   // The normal attention loop may already own the asynchronous capture.
   // Like the updater, wait for its acknowledgement before requesting commit.
   let captured;
   const deadline=Date.now()+10000;
   do{
    captured=await api('/api/upgrade?requestId='+requestId);
    if(captured.upgrade.stage==='captured')break;
    await new Promise(resolve=>setTimeout(resolve,100));
   }while(Date.now()<deadline);
   if(captured.upgrade.stage!=='captured')throw Error('Upgrade capture was not acknowledged: '+captured.upgrade.stage);
   const committed=await api('/api/upgrade',{action:'commit',requestId,targetVersion});
   return {captureStage:captured.upgrade.stage,closed:committed.closed,stage:committed.upgrade.stage};
  });
  assert.equal(result.captureStage,'captured');assert.equal(result.closed,true);assert.equal(result.stage,'closed');
  await page.screenshot({path:path.join(options.output,options.phase+'-screen.png')});
  process.stdout.write(JSON.stringify({passed:true,mode:'upgrade',...result,errors,blockedExternalRequests:blocked.length}));return;
 }
 await pause(3500);
 const before=await metric();stage('idle-before');const idleBeforeStart=requests.length;await pause(options.idleSeconds*1000);
 const idleBeforeEnd=requests.length,idleBeforeMetric=await metric();
 stage('archive-click');
 await page.locator(`#sessions [data-session-id="${options.archived}"] .session`).click();
 await page.waitForFunction(id=>active?.id===id&&!pendingSelection,options.archived);
 await page.locator('#prompt').fill('보관 이후 저장하지 않을 새 입력');
 await page.locator(`#sessions [data-session-id="${options.archived}"]`).hover();
 await page.locator(`#sessions [data-session-id="${options.archived}"] .session-remove`).click();
 await page.locator('#action-confirm').click();
 await page.waitForFunction(id=>!document.querySelector(`#sessions [data-session-id="${id}"]`),options.archived);
 const hiddenAt=Date.now();await pause(1300);
 if(options.current)assert.equal(requests.filter(row=>row.path==='/api/drafts'&&row.method==='POST'&&row.id===options.archived&&row.time>=hiddenAt).length,0);
 await page.screenshot({path:path.join(options.output,options.phase+'-archived.png')});
 // The first archive is a real row/confirmation click. Repeat its actual
 // restore/selection/hide application paths without UI timing dominating CPU.
 stage('cycles');const durations=[];
 for(let index=0;index<options.cycles;index++){
  const began=Date.now();
  await page.evaluate(async({id})=>{
   await api('/api/sessions/restore',{id});hiddenSessionIds.delete(id);
   boot=await api('/api/bootstrap');sessions=boot.sessions;renderSessions();
   await selectSession(id);const hiding=hideSession(id,null);
   document.getElementById('action-confirm').click();
   await hiding;
  },{id:options.archived});
  durations.push(Date.now()-began);
 }
 await page.evaluate(async()=>WorkspaceDraftPersistence.flush());
 const afterCycles=await metric();stage('idle-after');const idleAfterStart=requests.length;
 await pause(options.idleSeconds*1000);const idleAfterEnd=requests.length,afterIdle=await metric();
 stage('send-work-chat');
 await page.locator(`#sessions [data-session-id="${options.work}"] .session`).click();
 await page.waitForFunction(id=>active?.id===id&&!pendingSelection,options.work);
 await page.locator('#prompt').fill('현재 업무 전송 검증');
 await page.evaluate(async()=>submit());
 await page.waitForFunction(()=>active?.messages.some(row=>row.role==='user'&&row.text==='현재 업무 전송 검증'));
 await page.locator('#mode-chat').click();
 await page.locator(`#sessions [data-session-id="${options.chat}"] .session`).click();
 await page.waitForFunction(id=>active?.id===id&&!pendingSelection,options.chat);
 await page.locator('#prompt').fill('일반 대화 전송 검증');await page.evaluate(async()=>submit());
 await page.waitForFunction(()=>active?.messages.some(row=>row.role==='user'&&row.text==='일반 대화 전송 검증'));
 await page.locator('#prompt').fill('종료 전 보존할 현재 초안');
 await page.evaluate(async()=>{saveDraft();await WorkspaceDraftPersistence.flush();});
 const saved=await evaluateApi('/api/drafts');
 assert.equal(saved.entries.find(row=>row.id===options.chat).draft.text,'종료 전 보존할 현재 초안');
 if(options.current)assert.equal(saved.entries.find(row=>row.id===options.archived).draft.text,'이미 저장된 초안');
 const label=await page.locator('label[for="registration-input"]').innerText();
 if(options.current)assert.ok(label.includes('7~8자'));
 await page.screenshot({path:path.join(options.output,options.phase+'-chat.png')});
 stage('quit');await page.evaluate(async()=>requestShutdown({confirmed:true}));
 await page.waitForFunction(()=>shutdownState==='closed');
 assert.deepEqual(errors,[]);assert.equal(blocked.length,0);
 const paths=rows=>rows.reduce((all,row)=>{const key=row.method+' '+row.path;all[key]=(all[key]||0)+1;return all;},{});
 const archivedPosts=requests.filter(row=>row.path==='/api/drafts'&&row.method==='POST'&&row.id===options.archived&&row.time>=hiddenAt);
 const report={passed:true,mode:'main',archiveClick:true,cycles:options.cycles,archivedDraftPostsAfterHide:archivedPosts.length,
  workAndChatSend:true,currentDraftPreserved:true,gracefulQuit:true,registrationLabel:label,
  idleBeforeRequests:paths(requests.slice(idleBeforeStart,idleBeforeEnd)),idleAfterRequests:paths(requests.slice(idleAfterStart,idleAfterEnd)),
  renderer:{before,idleBeforeMetric,afterCycles,afterIdle,retainedHeapDeltaBytes:afterIdle.JSHeapUsedSize-before.JSHeapUsedSize,
   idleTaskSecondsBefore:idleBeforeMetric.TaskDuration-before.TaskDuration,idleTaskSecondsAfter:afterIdle.TaskDuration-afterCycles.TaskDuration},
  cycleMilliseconds:{min:Math.min(...durations),max:Math.max(...durations),mean:durations.reduce((a,b)=>a+b,0)/durations.length},errors,blockedExternalRequests:blocked.length};
 process.stdout.write(JSON.stringify(report));
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();});
