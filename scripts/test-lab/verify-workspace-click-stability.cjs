// Chromium's native pointerup/click targeting with the shipped app and local synthetic API.
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const {fixture}=require('./verify-workspace-sidebar-layout.cjs');

async function heldClick(page,locator,update){
  await locator.scrollIntoViewIfNeeded();await locator.hover();
  const point=await locator.evaluate(node=>{
    const rect=node.getBoundingClientRect(),x=rect.x+rect.width/2,y=rect.y+rect.height/2;
    if(!node.contains(document.elementFromPoint(x,y)))throw Error('Control is covered');
    return {x,y};
  });
  await page.mouse.move(point.x,point.y);await page.mouse.down();
  try{await update();}finally{await page.mouse.up();}
}
async function run(browser,measure){
  const f=await fixture(browser),{page,rows}=f,posted=[],opened=[];
  try{
    const task=rows[0],selector='#sessions [data-session-id="'+task.id+'"] ';
    const background=async()=>{
      rows[1].state=rows[1].state==='running'?'done':'running';
      await page.evaluate(()=>WorkspaceAttention.refresh());
      assert.equal(await page.evaluate(id=>sessions.find(item=>item.id===id).state,rows[1].id),rows[1].state);
    };
    await page.route('**/api/session/update',async route=>{
      const body=route.request().postDataJSON();posted.push(body);
      await route.fulfill({json:{session:{...task,pinned:body.pinned}}});
    });
    await page.locator(selector+'.session').hover();
    await heldClick(page,page.locator(selector+'.session-pin'),background);
    await page.waitForFunction(()=>!sessionOrderSaving);
    assert.deepEqual(posted,[{id:task.id,pinned:true}]);
    await heldClick(page,page.locator(selector+'.session-remove'),background);
    await page.locator('#action-dialog[open]').waitFor();
    const cancel=page.locator('#action-cancel');await cancel.hover();
    await heldClick(page,cancel,async()=>{
      const before=await cancel.boundingBox();await page.evaluate(()=>toast('백그라운드 요청 알림'));
      const after=await cancel.boundingBox();assert.ok(Math.abs(after.y-before.y)<1);
    });
    await page.waitForFunction(()=>!document.querySelector('#action-dialog').open);
    const other=rows[2],otherSelector='#sessions [data-session-id="'+other.id+'"] ';
    await page.locator(otherSelector+'.session').hover();
    await heldClick(page,page.locator(otherSelector+'.session-remove'),background);
    const confirm=page.locator('#action-confirm');
    await heldClick(page,confirm,async()=>{
      const before=await confirm.boundingBox();await page.evaluate(()=>toast('다른 업무의 완료 알림'));
      assert.ok(Math.abs((await confirm.boundingBox()).y-before.y)<1);
    });
    await page.waitForFunction(id=>hiddenSessionIds.has(id),other.id);
    assert.deepEqual(f.calls.filter(item=>item.path==='/api/session/hide').map(item=>item.body.id),[other.id]);

    const queue={revision:1,queue:[{id:'q',text:'저장된 요청',state:'queued',editRevision:2},{id:'q2',text:'다른 요청',state:'queued'}],schedules:[{id:'s',text:'재개 대상',kind:'daily',time:'09:00',enabled:false,nextRunAt:2000000000},{id:'s2',text:'다른 예약',kind:'daily',time:'10:00',enabled:true,nextRunAt:2000000000}],steer:{supported:true}};
    await page.route('**/api/dispatch*',async route=>{
      if(route.request().method()==='POST')posted.push(route.request().postDataJSON());
      await route.fulfill({json:queue});
    });
    await page.locator(selector+'.session').click();await page.waitForFunction(id=>active?.id===id,task.id);
    await page.locator('#inline-queue-manage').click();await page.locator('#queue-list .workflow-row').first().waitFor();
    const queueUpdate=async()=>{queue.revision++;queue.queue[1].text+='.';queue.schedules[1].text+='.';await page.evaluate(()=>WorkspaceWorkflow.refresh());};
    const controls=page.locator('#queue-list [data-request-id="q"] .workflow-row-actions button');
    await heldClick(page,controls.nth(3),queueUpdate);
    await page.locator('#request-editor-dialog[open]').waitFor();
    assert.equal(await page.locator('#request-editor-text').inputValue(),'저장된 요청');
    await page.keyboard.press('Escape');
    await heldClick(page,controls.nth(0),queueUpdate);
    await page.waitForFunction(()=>!WorkspaceWorkflow.isSubmitting());
    await heldClick(page,controls.nth(4),queueUpdate);
    await page.waitForFunction(()=>!WorkspaceWorkflow.isSubmitting());
    const resume=page.locator('#schedule-list [data-schedule-id="s"] button').nth(1);
    await heldClick(page,resume,queueUpdate);
    await page.waitForFunction(()=>!WorkspaceWorkflow.isSubmitting());
    assert.deepEqual(posted.filter(item=>item.action).map(item=>[item.action,item.requestId]),[['apply_now','q'],['cancel','q'],['schedule_resume','s']]);
    await page.locator('#workflow-close').click();
    await heldClick(page,page.locator('#inline-queue-list [data-request-id="q"] .inline-queue-apply'),queueUpdate);
    await page.waitForFunction(()=>!WorkspaceWorkflow.isSubmitting());
    await heldClick(page,page.locator('#inline-queue-list [data-request-id="q"] .inline-queue-delete'),queueUpdate);
    await page.waitForFunction(()=>!WorkspaceWorkflow.isSubmitting());
    assert.deepEqual(posted.filter(item=>item.action).slice(3).map(item=>[item.action,item.requestId]),[['apply_now','q'],['cancel','q']]);

    const entry=name=>({name,path:'C:/fixture/'+name,kind:'folder'});
    let searchName='Alpha',searchCount=0,releaseSearch,searchGate;
    const holdSearch=()=>{searchGate=new Promise(resolve=>{releaseSearch=resolve;});};
    await page.route('**/api/browse-paths',async route=>{
      const body=route.request().postDataJSON(),path=body.path||'C:/fixture';
      if(body.query){searchCount++;await searchGate;}else opened.push(path);
      await route.fulfill({json:{path,parent:null,entries:body.query?[entry(searchName)]:[entry('Alpha'),entry('Beta')],breadcrumbs:[],shortcuts:[]}});
    });
    const picker=async()=>{
      await page.evaluate(()=>{void WorkspacePathPicker.open({kind:'folder',initialDirectory:'C:/fixture'});});
      await page.locator('.path-picker-row').first().waitFor();
    };
    await picker();
    const search=page.locator('.path-picker-main input[type=search]'),alpha=page.locator('.path-picker-row').first();
    holdSearch();await search.fill('Al');await page.locator('.path-picker-list[aria-busy="true"]').waitFor();
    await heldClick(page,alpha,()=>{releaseSearch();return page.waitForFunction(()=>document.querySelectorAll('.path-picker-row').length===1);});
    await page.waitForFunction(()=>document.querySelector('.path-picker-location input').value==='C:/fixture/Alpha');
    await page.keyboard.press('Escape');await picker();searchName='Beta';holdSearch();await search.fill('Be');await page.locator('.path-picker-list[aria-busy="true"]').waitFor();
    await heldClick(page,alpha,()=>{releaseSearch();return page.waitForFunction(()=>document.querySelector('.path-picker-row')?.dataset.path==='C:/fixture/Beta');});
    assert.equal(searchCount,2);assert.deepEqual(opened,['C:/fixture','C:/fixture/Alpha','C:/fixture']);
    await page.keyboard.press('Escape');
    const sidebar=measure?await page.evaluate(()=>{
      showHome();boot.sessionGroups={groups:[],assignments:{}};
      return [4,20,50].map(count=>{
        sessions=Array.from({length:count},(_,i)=>({id:'sample'+i,title:'업무 '+i,workspace:'C:/sample/'+i,state:'idle',created:i+1}));
        renderSessions();
        const list=document.getElementById('sessions'),observer=new MutationObserver(()=>{}),times=[];
        observer.observe(list,{childList:true});let detached=0;
        for(let i=0;i<35;i++){
          const start=performance.now();sessions[1].state=i%2?'running':'done';renderSessions();void list.scrollHeight;
          if(i>=5)times.push(performance.now()-start);
          for(const record of observer.takeRecords())detached+=record.removedNodes.length;
        }
        observer.disconnect();times.sort((a,b)=>a-b);
        return {count,detached,samples:times.length,renderAndLayoutP50ms:times[14],renderAndLayoutP95ms:times[28]};
      });
    }):[];
    assert.ok(sidebar.every(row=>row.detached===0));
    return {sidebarPin:1,deleteConfirmation:1,confirmedHide:1,dialogFooterShiftUnderOnePixel:true,detailQueue:['edit','apply_now','cancel','schedule_resume'],inlineQueue:['apply_now','cancel'],retainedPathOpens:1,removedPathOpens:0,sidebar,pageErrors:f.errors};
  }finally{await f.finish();}
}
async function selectionTiming(browser){
  const f=await fixture(browser),{page,rows}=f,results=[];
  let size=8,delay=150,withApprovals=false;
  const source=rows[0].id,target=rows[1].id;
  try{
    await page.route('**/api/session?*',async route=>{
      const id=new URL(route.request().url()).searchParams.get('id'),row=rows.find(item=>item.id===id);
      const messages=Array.from({length:size},(_,i)=>i%2?{role:'assistant',text:'| 항목 | 값 | 상태 |\n|---|---|---|\n'+Array.from({length:10},(_,n)=>'|행 '+n+'|'+n+'|완료|').join('\n')}:{role:'user',text:'업무 요청 '+i});
      if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
      await route.fulfill({json:{...row,messages,requests:withApprovals?[{id:'approval-'+id,tool:'Read',input:{file_path:id+'.txt'}}]:[]}});
    });
    await page.locator('#sessions [data-session-id="'+source+'"] .session').click();
    await page.waitForFunction(id=>active?.id===id,source);
    for(const backgroundCount of [0,4])for(delay of [0,150])for(size of [8,150]){
      for(let n=2;n<6;n++)rows[n].state=n-2<backgroundCount?'running':'idle';
      await page.evaluate(()=>WorkspaceAttention.refresh());
      const samples=[];
      for(let i=0;i<22;i++){
        const id=await page.evaluate(({source,target})=>active.id===source?target:source,{source,target});
        await page.evaluate(id=>{
          const button=[...document.querySelectorAll('#sessions [data-session-id]')].find(row=>row.dataset.sessionId===id).querySelector('.session');
          const result=window.selectionSample={id,from:active.id},now=()=>performance.now()-result.start;
          button.addEventListener('click',()=>{result.start=performance.now();},{capture:true,once:true});
          button.addEventListener('click',()=>{
            result.feedbackMs=now();result.busy=button.getAttribute('aria-busy')==='true'&&!document.querySelector('#selection-status').hidden&&active.id===result.from;
            requestAnimationFrame(()=>requestAnimationFrame(()=>{result.feedbackFrameMs=now();result.busyAtFrame=!!pendingSelection&&active.id===result.from;}));
          },{once:true});
          const observer=new MutationObserver(()=>{
            if(active.id!==id||pendingSelection||!result.start)return;
            observer.disconnect();result.loadedMs=now();result.messages=active.messages.length;result.controlsReady=!document.querySelector('.composer-wrap').inert&&!document.querySelector('#task-view').inert;
            requestAnimationFrame(()=>requestAnimationFrame(()=>{result.loadedFrameMs=now();}));
          });
          observer.observe(document.querySelector('#conversation'),{childList:true});
        },id);
        await page.locator('#sessions [data-session-id="'+id+'"] .session').click();
        await page.waitForFunction(()=>Number.isFinite(window.selectionSample?.loadedFrameMs));
        const result=await page.evaluate(()=>window.selectionSample);
        assert.equal(result.busy,true);assert.equal(result.messages,size);assert.equal(result.controlsReady,true);
        if(i>=2)samples.push(result);
      }
      const summary=name=>{const values=samples.map(row=>row[name]).sort((a,b)=>a-b);return {p50:(values[9]+values[10])/2,p95:values[18]};};
      results.push({messages:size,otherRunningBadges:backgroundCount,delayMs:delay,warmups:2,samples,feedbackMs:summary('feedbackMs'),feedbackTwoRafMs:summary('feedbackFrameMs'),loadedMs:summary('loadedMs'),loadedTwoRafMs:summary('loadedFrameMs')});
    }
    withApprovals=true;const replies=[];
    await page.route('**/api/respond',async route=>{replies.push(route.request().postDataJSON());await route.fulfill({json:{ok:true}});});
    await page.route('**/api/files?*',async route=>{
      const id=new URL(route.request().url()).searchParams.get('id');
      await route.fulfill({json:{files:[{path:id+'.txt',name:id+'.txt',kind:'file'}]}});
    });
    await page.evaluate(()=>selectSession(active.id));
    const rapid=await page.evaluate(async({source,target})=>{
      const original=active.id,next=original===source?target:source;
      document.querySelector('#prompt').value='초안 '+original;
      const approve=document.querySelector('#requests button.send-button')||document.querySelector('#requests button');
      const first=selectSession(next),firstSignal=pendingSelection.controller.signal;
      await new Promise(resolve=>setTimeout(resolve,8));
      const latest=selectSession(original),pendingId=pendingSelection.id,priorReadAborted=firstSignal.aborted;
      approve.click();
      const selected=await Promise.all([first,latest]);approve.click();
      return {original,pendingId,priorReadAborted,selected,finalId:active.id,finalDraft:document.querySelector('#prompt').value,approval:document.querySelector('#requests .request').dataset.requestId};
    },{source,target});
    assert.equal(rapid.priorReadAborted,true);assert.deepEqual(rapid.selected,[false,true]);assert.equal(rapid.finalId,rapid.original);
    assert.equal(rapid.finalDraft,'초안 '+rapid.original);assert.equal(rapid.approval,'approval-'+rapid.original);assert.equal(replies.length,0);
    await page.waitForFunction(id=>document.querySelector('#files .file')?.title===id+'.txt',rapid.finalId);
    rapid.material=await page.locator('#files .file').getAttribute('title');
    await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/respond')),page.locator('#requests .request button').last().click()]);
    assert.deepEqual(replies.map(row=>[row.id,row.requestId]),[[rapid.finalId,rapid.approval]]);
    rapid.replies=replies.map(({id,requestId})=>({id,requestId}));
    return {measurement:'Native click capture to synchronous status, conversation commit with enabled controls, and two requestAnimationFrame callbacks. Background running badges and API delay are synthetic; no Claude processes.',results,rapid,pageErrors:f.errors};
  }finally{await f.finish();}
}
async function checkFolderModeSelection(page){
  const positions=await page.locator('#new-chat').evaluate(button=>{
    button.click();
    const dialog=document.getElementById('folder-dialog');
    const card=dialog.querySelector('#folder-mode-existing').closest('label');
    const animations=dialog.getAnimations();
    // Sample the opening motion at fixed progress, independent of machine speed.
    animations.forEach(animation=>animation.pause());
    const positions=[0,0.5,1].map(progress=>{
      for(const animation of animations)animation.currentTime=animation.effect.getComputedTiming().endTime*progress;
      const rect=card.getBoundingClientRect();
      return {left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom};
    });
    animations.forEach(animation=>animation.finish());
    return positions;
  });
  for(const rect of positions.slice(1)){
    for(const edge of Object.keys(rect))assert.ok(Math.abs(rect[edge]-positions[0][edge])<0.5,'Folder card moved during opening: '+edge);
  }
  const existing=page.locator('#folder-mode-existing'),fresh=page.locator('#folder-mode-new');
  const path=page.locator('#folder-input');
  for(const tag of ['strong','small']){
    await fresh.check();
    const letter=await page.locator('label:has(#folder-mode-existing) '+tag).evaluate(text=>{
      const range=document.createRange();
      range.setStart(text.firstChild,1);range.setEnd(text.firstChild,2);
      const rect=range.getBoundingClientRect();
      return {start:rect.left+1,end:rect.right-1,y:rect.top+rect.height/2};
    });
    await page.mouse.move(letter.start,letter.y);await page.mouse.down();
    await page.mouse.move(letter.end,letter.y,{steps:2});await page.mouse.up();
    assert.equal(await existing.isChecked(),true,'Small pointer movement on folder '+tag+' should select the option');
    assert.equal(await path.isVisible(),true);
  }
  await existing.focus();await page.keyboard.press('ArrowLeft');
  assert.equal(await fresh.isChecked(),true);assert.equal(await path.isVisible(),false);
  await page.keyboard.press('ArrowRight');
  assert.equal(await existing.isChecked(),true);
  await path.fill('C:/fixture/업무');await path.press('Control+a');
  assert.equal(await path.evaluate(input=>input.value.slice(input.selectionStart,input.selectionEnd)),'C:/fixture/업무');
  await page.keyboard.press('Escape');
}
async function navigationChecks(browser,count){
  const f=await fixture(browser),{page,rows}=f,groups=f.getGroups(),orders=[],creates=[];
  try{
    await checkFolderModeSelection(page);
    await page.route('**/api/session-groups',async route=>{
      const body=route.request().postDataJSON();
      if(body?.action!=='reorder')return route.fallback();
      const items=groups.groups,from=items.findIndex(group=>group.id===body.groupId);
      const [moved]=items.splice(from,1),to=items.findIndex(group=>group.id===body.targetGroupId)+(body.position==='after'?1:0);
      items.splice(to,0,moved);groups.revision++;orders.push(items.map(group=>group.id));
      await route.fulfill({json:{sessionGroups:groups}});
    });
    for(const id of ['large','five','empty']){
      await page.locator('.task-group-header[data-group-id="'+id+'"] .task-group-toggle').click();
      await page.waitForFunction(id=>!WorkspaceSessionGroups.isSaving()&&boot.sessionGroups.groups.find(group=>group.id===id).collapsed,id);
    }
    const handle=page.locator('.task-group-header[data-group-id="large"] .task-group-arrow');
    const empty=page.locator('.task-group-header[data-group-id="empty"]');
    await handle.hover();const source=await handle.boundingBox();
    await page.mouse.move(source.x+source.width/2,source.y+source.height/2);await page.mouse.down();
    await page.locator('.task-group-arrow.is-dragging').waitFor();
    rows[1].state='running';await page.evaluate(()=>WorkspaceAttention.refresh());
    const target=await empty.boundingBox();await page.mouse.move(target.x+40,target.y+target.height-2,{steps:8});await page.mouse.up();
    await page.waitForFunction(()=>boot.sessionGroups.groups.at(-1).id==='large');
    await handle.focus();await page.keyboard.press('Alt+ArrowUp');
    await page.waitForFunction(()=>boot.sessionGroups.groups[1].id==='large');
    assert.deepEqual(orders,[['five','empty','large'],['five','large','empty']]);
    let release;
    await page.route('**/api/create',async route=>{
      const body=route.request().postDataJSON();creates.push(body);
      const item={...rows[0],id:'create-'+creates.length,title:body.title,workspace:'C:/fixture/'+creates.length};
      rows.push(item);await new Promise(resolve=>{release=resolve;});await route.fulfill({json:item});
    });
    await page.evaluate(()=>{boot.managedWorkspaceRoot='C:/fixture';});
    for(let i=1;i<=count;i++){
      await page.locator('#new-chat').click();await page.locator('#task-name').fill('업무 '+i);await page.locator('#trust').check();
      await heldClick(page,page.locator('#confirm-folder'),async()=>{rows[1].state=rows[1].state==='done'?'running':'done';await page.evaluate(()=>WorkspaceAttention.refresh());});
      await page.waitForFunction(()=>document.getElementById('confirm-folder').disabled&&document.getElementById('confirm-folder-label').textContent.includes('생성 중'));
      await page.evaluate(()=>document.getElementById('confirm-folder').click());
      assert.equal(creates.length,i);assert.equal(creates[i-1].managed,true);assert.equal(creates[i-1].trusted,true);
      release();await page.waitForFunction(id=>active?.id===id&&!document.getElementById('folder-dialog').open,'create-'+i);
      await page.locator('#home-button').click();
    }
    return {folderModeSelection:true,groupLongPressOrder:orders[0],groupKeyboardOrder:orders[1],createdOnceWithBusyLabel:creates.length,pageErrors:f.errors};
  }finally{await f.finish();}
}
async function main(){
  const arg=name=>process.argv.includes(name)?process.argv[process.argv.indexOf(name)+1]:null;
  const count=Number(arg('--repetitions')||1),output=arg('--output');
  assert.ok(Number.isSafeInteger(count)&&count>0);
  const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||undefined});
  const report={browser:browser.version(),fixture:'Local synthetic API, native mouse down/up; background update held between press and release.',repetitions:count,trials:[]};
  const save=()=>{if(output){fs.mkdirSync(path.dirname(path.resolve(output)),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2));}};
  try{
    if(process.argv.includes('--selection')){
      report.selection=await selectionTiming(browser);
      console.log(JSON.stringify(report.selection.results.map(({samples,...summary})=>summary)));
    }else if(process.argv.includes('--navigation')){
      report.navigation=await navigationChecks(browser,count);console.log(JSON.stringify(report.navigation));
    }else{
      for(let i=0;i<count;i++){report.trials.push(await run(browser,i===0));save();}
      console.log(JSON.stringify({completed:report.trials.length,first:report.trials[0]}));
    }
  }finally{
    try{save();}finally{await browser.close();}
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
