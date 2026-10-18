/* Shipped UI in an isolated browser; all APIs are synthetic and read-only. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const checks=[],errors=[],historyCalls=[],mutations=[];
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
const appUpdate={currentVersion:'0.23.0',status:'current',autoCheck:false,release:null,canInstall:false,source:{provider:'gitlab',label:'사내 GitLab'}};
let history={status:'ready',currentVersion:'0.23.0',source:{provider:'gitlab',label:'사내 GitLab'},checkedAt:null,error:null,entries:[
 {version:'0.23.0',title:'더 편하게 사용하는 업무 공간',notes:'간편 화면\n복잡한 도구를 접고 대화와 요청에 집중할 수 있어요.\n\n패널 열기와 닫기\n열린 패널은 패널 안에서 닫고, 닫힌 패널은 상단에서 다시 열 수 있어요.\n\n업데이트 내역\n현재 버전과 이전 버전의 변경 내용을 함께 확인할 수 있어요.',publishedAt:null,origin:'bundled',url:''},
 {version:'0.22.0',title:'업무 연결과 업데이트 개선',notes:'업데이트 전환 중 작성 내용을 보관합니다.\n지난 버전의 변경 내용도 이어서 볼 수 있습니다.',publishedAt:'2026-10-01T00:00:00Z',origin:'published',url:'https://untrusted.example/release'},
]};
let offline=false,loading=false;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(7000);
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(url.origin!=='http://workspace.test')return route.abort();
   if(!p.startsWith('/api/')){
    const filename=path.resolve(web,p==='/'?'index.html':p.slice(1));
    if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});
    return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.woff2':'font/woff2','.svg':'image/svg+xml','.png':'image/png'})[path.extname(filename)]||'application/octet-stream'});
   }
   const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
   if(req.method()==='POST')mutations.push({path:p,body});
   let result={ok:true};
   if(p==='/api/app-update-history'){
    historyCalls.push({method:req.method(),body});
    result={...history,status:loading?'loading':offline?'error':'ready',error:offline?'배포 서버에 연결하지 못했어요.':null};
   }else if(p==='/api/bootstrap')result={sessions:[],demo:false,appearance:{theme:'light'},layout:null,appUpdate,executionMode:'normal',sessionGroups:{groups:[],assignments:{},revision:0},runtime:{entry:'fixture'}};
   else if(p==='/api/app-update')result=appUpdate;
   else if(p==='/api/drafts')result=req.method()==='GET'?{entries:[],selectedId:'home'}:{revision:1};
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:[]};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/events'){await new Promise(resolve=>setTimeout(resolve,100));result={events:[]};}
   await route.fulfill({json:result});
  });
  const waitReady=()=>page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
  const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const shot=async name=>{if(output)await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});};
  const dialog=page.locator('#release-history-dialog'),opener=page.locator('#release-history-open');
  const open=async()=>{await opener.click();await page.waitForFunction(()=>document.getElementById('release-history-notes').textContent.length>0);await page.waitForFunction(()=>document.getElementById('release-history-refresh').disabled===false);await settle();};
  const close=async()=>{await page.keyboard.press('Escape');assert.equal(await dialog.isVisible(),false);assert.equal(await opener.evaluate(node=>document.activeElement===node),true);};
  const geometry=()=>page.evaluate(()=>{const box=document.getElementById('release-history-dialog'),notes=document.getElementById('release-history-notes'),r=box.getBoundingClientRect();return {overflow:document.documentElement.scrollWidth>innerWidth||box.scrollWidth>box.clientWidth,inside:r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight,notesFont:getComputedStyle(notes).fontSize,notesLine:getComputedStyle(notes).lineHeight,close:document.getElementById('release-history-close').getBoundingClientRect().width};});
  await page.goto('http://workspace.test/#token=synthetic-history');await waitReady();await page.evaluate(()=>document.fonts.ready);
  assert.equal(historyCalls.length,0);await page.locator('#prompt').fill('보존되어야 하는 작성 중 요청');
  await page.locator('#settings-open').click();await opener.scrollIntoViewIfNeeded();
  assert.equal(await opener.isVisible(),true);assert.equal(await page.locator('#app-update-notes-open').isVisible(),false);assert.equal(historyCalls.length,0);await shot('history-settings-entry');
  checks.push('latest version settings always expose history, with no history requests on startup or settings open');
  const before=mutations.length;await open();
  assert.deepEqual(historyCalls.map(call=>call.method),['GET','POST']);assert.deepEqual(historyCalls[1].body,{action:'check',force:false});
  assert.equal(await page.locator('#release-history-version').inputValue(),'0.23.0');assert.equal(await page.locator('#release-history-current').isVisible(),true);
  assert.match(await page.locator('#release-history-meta').textContent(),/앱 변경 이력 · 앱에 포함된 변경 내역/);
  assert.equal(await page.locator('#app-update-dialog').isVisible(),false);assert.equal(await dialog.locator('button').count(),3);
  checks.push('opening reads cache then performs one lazy check; bundled current version displays without installation controls');
  await page.locator('#release-history-version').selectOption('0.22.0');assert.equal(await page.locator('#release-history-current').isVisible(),false);
  assert.match(await page.locator('#release-history-meta').textContent(),/사내 배포 설명 · 배포 기록일/);assert.equal(await page.locator('#release-history-link').isVisible(),false);
  assert.equal(historyCalls.length,2);await page.locator('#release-history-version').selectOption('0.23.0');
  checks.push('older version selection is local and distinguishes published history; untrusted GitLab link stays hidden');
  await page.locator('#release-history-close').focus();
  for(let i=0;i<14;i++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.getElementById('release-history-dialog').contains(document.activeElement)),true,'Tab stays in native dialog');}
  await close();assert.equal(await page.locator('#prompt').inputValue(),'보존되어야 하는 작성 중 요청');await open();
  checks.push('keyboard Tab remains in dialog, Escape restores settings entry focus, and typed draft survives');
  for(const theme of ['light','dark'])for(const width of [1440,640,360]){
   await page.setViewportSize({width,height:width===360?800:1000});await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);await settle();
   const g=await geometry();assert.equal(g.overflow,false);assert.equal(g.inside,true);assert.equal(g.notesFont,'13px');assert.equal(g.notesLine,'25px');assert.equal(g.close,32);
   await page.locator('#release-history-title').click();await shot(`history-${width}-${theme}`);
  }
  checks.push('light/dark at 1440, 640 and 360px: no overflow, 32px close control, 13px notes with 25px line height');
  await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>document.documentElement.dataset.theme='light');
  offline=true;await page.locator('#release-history-refresh').click();await page.waitForFunction(()=>document.getElementById('release-history-status').textContent.includes('저장된 변경 내역'));
  assert.equal(await page.locator('#release-history-notes').isVisible(),true);await shot('history-offline');
  assert.deepEqual(historyCalls.at(-1).body,{action:'check',force:true});await close();await open();assert.equal(await opener.isDisabled(),false);
  checks.push('offline response preserves cached notes and entry access; refresh explicitly uses force:true');
  offline=false;loading=true;await page.locator('#release-history-refresh').click();await page.waitForFunction(()=>document.getElementById('release-history-dialog').dataset.state==='loading');
  const prior=historyCalls.length;await page.waitForFunction(()=>document.getElementById('release-history-dialog').dataset.state==='loading');await page.waitForTimeout(2200);
  assert.ok(historyCalls.length>prior);assert.equal(historyCalls.at(-1).method,'GET');await close();const afterClose=historyCalls.length;await page.waitForTimeout(2300);assert.equal(historyCalls.length,afterClose);
  checks.push('loading polls local GET only; closing stops polling');loading=false;
  history={...history,entries:[
   {...history.entries[0],notes:'앱 설치본에 포함된 기본 설명'},
   {version:'0.23.0',title:'AX Workspace 0.23.0',origin:'published',noteSource:'package',publishedAt:'2026-10-17T00:00:00Z',notes:'',url:''},
   {version:'0.22.0',title:'사내 업무 환경 개선',origin:'published',noteSource:'deployment',publishedAt:'2026-10-02T00:00:00Z',notes:'사내 배포 담당자가 작성한 변경 설명\n\n업무 자료를 확인할 때 패널을 더 편하게 열고 닫을 수 있습니다.\n이 설명은 이번 사내 배포에 적용된 변경 사항입니다.',url:''},
   {version:'0.21.0',title:'업무 연결 안정성 개선',origin:'published',noteSource:'gitlab-release',publishedAt:'2026-09-25T00:00:00Z',notes:'GitLab 릴리스에 등록된 변경 설명\n\n연결이 끊긴 뒤 작성 중이던 요청을 이어서 사용할 수 있습니다.\n업무 종료 후 상태 표시를 개선했습니다.',url:''},
  ]};
  await open();assert.equal(await page.locator('#release-history-version option').count(),3);
  assert.equal(await page.locator('#release-history-notes').textContent(),'당시 배포 설명을 확인할 수 없습니다.');
  assert.match(await page.locator('#release-history-meta').textContent(),/배포 파일 기록 · 패키지 등록일/);
  assert.match(await page.locator('#release-history-help').textContent(),/조회 권한·지원 범위/);
  checks.push('package-only current version overrides bundled defaults and explicitly shows unavailable explanation with package registration date');
  const provenanceRequests=historyCalls.length;
  for(const [version,sourceLabel,dateLabel,name] of [['0.23.0','배포 파일 기록','패키지 등록일','package'],['0.22.0','사내 배포 설명','배포 기록일','deployment'],['0.21.0','GitLab 릴리스 설명','릴리스 등록일','native-release']]){
   await page.locator('#release-history-version').selectOption(version);
   const meta=await page.locator('#release-history-meta').textContent();assert.ok(meta.includes(sourceLabel)&&meta.includes(dateLabel));
   if(name!=='package'){assert.equal(await page.locator('#release-history-notes').textContent(),history.entries.find(row=>row.version===version).notes);}
   for(const theme of ['light','dark'])for(const width of [1440,360]){
    await page.setViewportSize({width,height:width===360?800:1000});await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);await settle();
    const g=await geometry();assert.equal(g.overflow,false);assert.equal(g.inside,true);
    await page.locator('#release-history-title').click();await shot(`history-${name}-${width}-${theme}`);
   }
  }
  assert.equal(historyCalls.length,provenanceRequests);await close();assert.equal(await page.locator('#prompt').inputValue(),'보존되어야 하는 작성 중 요청');
  checks.push('deployment and native release explanations retain exact server text and distinct dates; all three sources fit desktop/narrow light/dark without extra requests');
  history={...history,source:{provider:'github',label:'GitHub 공개 배포'},entries:[{...history.entries[2],url:'https://github.com/bongG-moon/workspace_local/releases/tag/v0.22.0',notes:'<img src=x onerror="globalThis.historyXss=true">\n<script>alert(1)</script>'}]};
  await open();assert.equal(await page.locator('#release-history-notes img, #release-history-notes script').count(),0);assert.equal(await page.evaluate(()=>globalThis.historyXss),undefined);
  assert.equal(await page.locator('#release-history-link').getAttribute('href'),history.entries[0].url);assert.equal(await page.locator('#release-history-link').getAttribute('rel'),'noopener noreferrer');
  checks.push('HTML in release text stays literal; only fixed GitHub version link is clickable with safe new-tab relation');
  const historyMutations=mutations.slice(before).filter(row=>row.path==='/api/app-update-history');assert.ok(historyMutations.length>0);assert.ok(historyMutations.every(row=>row.body.action==='check'));
  assert.equal(mutations.some(row=>['/api/send','/api/connect','/api/create','/api/restart'].includes(row.path)||row.path==='/api/app-update'&&row.body.action==='install'),false);
  assert.deepEqual(errors,[]);checks.push('history never sends model/session/install mutations and browser reported no page errors');
  await context.close();
 }finally{await browser.close();}
 console.log(JSON.stringify({passed:checks.length,checks,errors},null,2));
 if(output)fs.writeFileSync(path.join(output,'release-history-checks.json'),JSON.stringify({passed:checks.length,checks,errors},null,2));
})().catch(error=>{console.error(error);if(output)fs.writeFileSync(path.join(output,'release-history-checks.json'),JSON.stringify({passed:checks.length,checks,errors,error:error.stack},null,2));process.exitCode=1;});
