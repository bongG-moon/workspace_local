/* Representative real dialog markup/styles; synthetic API and test content only. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const row=(id,kind,title)=>({id,kind,title,workspace:'C:/fixture/'+id,state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[]});
const rows=[row('work-one','work','월간 보고서')],calls=[],errors=[];
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
let updateState={currentVersion:'0.23.26',status:'available',autoCheck:false,lastChecked:1792083600,release:{version:'0.23.27',title:'더 편해진 일반 대화',notes:'질문 예시와 최근 대화를 한눈에 볼 수 있어요.\n첨부 자료의 읽기 결과를 확인할 수 있어요.',publishedAt:'2026-10-16T00:00:00Z'},progress:null,error:null,canInstall:true};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://workspace.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(!p.startsWith('/api/')){const filename=path.join(web,p==='/'?'index.html':p.slice(1));if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.png':'image/png','.svg':'image/svg+xml'})[path.extname(filename)]||'application/octet-stream'});}
   const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};calls.push({path:p,body});let result={ok:true};
   if(p==='/api/bootstrap')result={sessions:rows,demo:false,appearance:{theme:'light'},executionMode:'normal',chatAttachmentPolicy:{extensions:['.txt']},sessionGroups:{groups:[],assignments:{},revision:0},appUpdate:updateState,runtime:{entry:'fixture'}};
   else if(p==='/api/drafts')result=req.method()==='GET'?{entries:[],selectedId:'home'}:{revision:1};
   else if(p==='/api/session')result=rows.find(r=>r.id===url.searchParams.get('id'));
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:rows};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/app-update'){if(body.action==='install')updateState={...updateState,status:'downloading',progress:56};result=updateState;}
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/browse-paths')result={path:'C:/fixture',parent:'C:/',entries:[{name:'월간 실적표.xlsx',path:'C:/fixture/월간 실적표.xlsx',kind:'file',size:24000}],shortcuts:[{name:'바탕화면',path:'C:/fixture'}],breadcrumbs:[{name:'자료',path:'C:/fixture'}],limited:false};
   else if(p==='/api/sessions/archived')result={total:1,sessions:[row('archived-one','work','지난 분기 보고서')],activeCount:1,activeLimit:500};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/events'){await new Promise(r=>setTimeout(r,150));result={events:[]};}
   await route.fulfill({json:result});
  });
  await page.goto('http://workspace.test/#token=fixture');
  await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);

  await page.evaluate(async()=>{await document.fonts.ready;document.getElementById('rename-input').value='월간 실적 보고서';document.getElementById('action-title').textContent='업무를 목록에서 지울까요?';document.getElementById('action-message').textContent='목록에서만 지워집니다. 원래 작업 폴더와 Claude 대화는 그대로 유지해요.\n보관한 업무에서 다시 꺼낼 수 있습니다.';document.getElementById('action-confirm').textContent='목록에서 지우기';document.getElementById('customizations-context').textContent='월간 보고서 · 현재 업무에 적용되는 스킬과 플러그인';document.getElementById('customizations-list').innerHTML='<div class="customization-row"><div><strong>자료 정리</strong><p>문서의 핵심 내용을 정리하고 요약합니다.</p></div><select aria-label="자료 정리 사용 상태"><option>공통·기존 설정 따르기</option></select></div>';document.getElementById('request-editor-text').value='매주 월요일에 지난주 실적을 요약해 주세요.';document.getElementById('request-editor-context').textContent='월간 실적 보고서';document.getElementById('schedule-overview-empty').hidden=false;document.getElementById('import-sessions').innerHTML='<button class="import-session"><strong>월간 실적 보고서 정리</strong><small>C:\\업무\\월간 보고서</small><small>3f9d1b28-1234-5678-9012-000000000001</small></button>';document.getElementById('import-preview').innerHTML='<h3>대화 미리보기</h3><div class="import-message"><strong>나</strong><p>지난달 실적을 정리해 주세요.</p></div><div class="import-message"><strong>Claude</strong><p>핵심 실적과 다음 달 계획을 정리했습니다.</p></div>';document.getElementById('upgrade-detail').textContent='작성 중인 내용을 보관하고 있어요. 준비가 끝나면 새 버전이 열립니다.';document.getElementById('upgrade-recovery-task').innerHTML='<option>월간 실적 보고서</option>';document.getElementById('upgrade-recovery-text').value='검토 내용을 반영해 주세요.\n수정할 부분은 표로 정리해 주세요.';document.getElementById('upgrade-recovery-files').textContent='C:\\업무\\실적표.xlsx';document.getElementById('app-update-title').textContent='새 버전이 준비됐어요';document.getElementById('app-update-release-meta').textContent='0.23.26 → 0.23.27';document.getElementById('app-update-notes').textContent='더 편해진 일반 대화\n• 질문 예시와 최근 대화를 한눈에 볼 수 있어요.\n• 첨부 자료의 읽기 결과를 확인할 수 있어요.';document.getElementById('app-update-install').disabled=false;});
  const ids=['settings-dialog','folder-dialog','rename-dialog','path-dialog','action-dialog','import-dialog','customizations-dialog','shortcuts-dialog','help-dialog','request-editor-dialog','schedule-overview-dialog','upgrade-dialog','upgrade-recovery-dialog','app-update-dialog'];
  const checks=[];
  for(const width of [1440,480]){
   await page.setViewportSize({width,height:1000});
   for(const theme of ['light','dark']){
    await page.evaluate(theme=>{document.documentElement.dataset.theme=theme},theme);
    for(const id of ids){
     await page.evaluate(id=>{for(const d of document.querySelectorAll('dialog[open]'))d.close();const d=document.getElementById(id);d.showModal();d.scrollTop=0;for(const b of d.querySelectorAll('.dialog-scroll-body,.customizations-body'))b.scrollTop=0;},id);
     const info=await page.locator('#'+id).evaluate(d=>{const r=d.getBoundingClientRect(),h=d.querySelector('h2'),hs=getComputedStyle(h);return {id:d.id,x:r.x,y:r.y,width:r.width,height:r.height,clientWidth:d.clientWidth,scrollWidth:d.scrollWidth,font:hs.fontFamily,titleSize:hs.fontSize,titleWeight:hs.fontWeight,hasFont:document.fonts.check('14px "Noto Sans KR"'),close:[...d.querySelectorAll('.icon-button[aria-label*="닫기"]')].map(b=>{const icon=b.querySelector('svg');const br=b.getBoundingClientRect(),ir=icon?.getBoundingClientRect();return {svg:!!icon,centerDelta:ir?Math.abs(ir.y+ir.height/2-br.y-br.height/2):null}})}});
     assert.ok(info.x>=0&&info.y>=0,`${id} viewport placement`);assert.ok(info.width<=width,`${id} too wide`);assert.ok(info.scrollWidth-info.clientWidth<=2,`${id} horizontal content overflow ${info.scrollWidth-info.clientWidth}`);assert.match(info.font,/Noto Sans KR/);assert.equal(info.hasFont,true);assert.ok(info.close.every(v=>v.svg&&v.centerDelta<1),`${id} vector close alignment`);
     checks.push({width,theme,...info});
     await page.locator('#'+id).evaluate(d=>{if(document.activeElement instanceof HTMLElement)document.activeElement.blur()});
     if(output)await page.locator('#'+id).screenshot({path:path.join(output,`${id}-${width}-${theme}.png`)});
     if(id==='settings-dialog'||id==='help-dialog'){
      const before=await page.locator('#'+id+' > .dialog-head').boundingBox();await page.locator('#'+id+' .dialog-scroll-body').evaluate(el=>el.scrollTop=el.scrollHeight);const after=await page.locator('#'+id+' > .dialog-head').boundingBox();assert.equal(after.y,before.y,`${id} header moves while body scrolls`);assert.ok(after.y+after.height<1000);await page.locator('#'+id+' .dialog-scroll-body').evaluate(el=>el.scrollTop=0);
     }
     await page.evaluate(id=>document.getElementById(id).close(),id);
    }
   }
  }

  const dynamicChecks=[];
  for(const id of ['path-picker-dialog','archived-tasks-dialog']){
   for(const width of [1440,480])for(const theme of ['light','dark']){
    await page.setViewportSize({width,height:1000});
    await page.evaluate(({id,theme})=>{document.documentElement.dataset.theme=theme;if(id==='path-picker-dialog')void WorkspacePathPicker.open({kind:'files'});else WorkspaceArchivedTasks.open();},{id,theme});
    await page.locator(id==='path-picker-dialog'?'.path-picker-row':'.archived-task').first().waitFor({timeout:5000}).catch(async e=>{throw new Error(e.message+' '+JSON.stringify({id,calls:calls.slice(-8),ui:await page.locator('#'+id).textContent(),errors}))});
    const info=await page.locator('#'+id).evaluate(d=>({width:d.getBoundingClientRect().width,overflow:d.scrollWidth-d.clientWidth,closeSvg:!!d.querySelector('.icon-button svg'),titleSize:getComputedStyle(d.querySelector('h2')).fontSize}));
    assert.ok(info.width<=width&&info.overflow<=2,`${id} narrow overflow`);assert.equal(info.closeSvg,true);if(id==='path-picker-dialog'&&width===480)assert.ok((await page.locator('.path-picker-shortcuts').boundingBox()).height<=72,'narrow picker shortcuts must remain a compact row');dynamicChecks.push({id,width,theme,...info});
    await page.evaluate(()=>document.activeElement.blur());if(output)await page.locator('#'+id).screenshot({path:path.join(output,`${id}-${width}-${theme}.png`)});
    await page.locator('#'+id+' .icon-button').click();assert.equal(await page.locator('#'+id).isVisible(),false);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   }
  }
  // Exercise real close listeners and browser modal focus restoration, not only geometry.
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#settings-open').click();await page.locator('#settings-close').click();assert.equal(await page.locator('#settings-dialog').isVisible(),false);
  await page.locator('#new-chat').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#folder-dialog').isVisible(),false);
  await page.locator('#app-update-sidebar-badge').click();
  await page.locator('#app-update-install').click();
  await page.waitForFunction(()=>document.getElementById('app-update-dialog-progress').value===56);
  assert.equal(await page.locator('#app-update-dialog').getAttribute('data-progress'),'true');
  assert.equal(await page.locator('#app-update-dialog-progress-wrap').isVisible(),true);
  for(const theme of ['light','dark']){await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);if(output)await page.locator('#app-update-dialog').screenshot({path:path.join(output,`app-update-progress-${theme}.png`)});}
  assert.deepEqual(errors,[]);const result={passed:true,dialogCount:ids.length+2,renderCases:checks.length+dynamicChecks.length,checks,dynamicChecks,pageErrors:errors};if(output)fs.writeFileSync(path.join(output,'dialog-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({passed:true,dialogCount:ids.length+2,renderCases:checks.length+dynamicChecks.length,pageErrors:errors}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
