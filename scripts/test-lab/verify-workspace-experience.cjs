/* Current shipped UI with synthetic responses. No Claude, account or personal files. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const web=path.resolve(__dirname,'../../local_app/web');
const out=path.resolve(process.argv[2]||path.join(__dirname,'../../outputs/experience-ui'));
fs.mkdirSync(out,{recursive:true});
const calls=[],errors=[],checks=[],geometry=[];
const workspace='C:/fixture/운영 보고서';
const files=[{path:workspace+'/output/q2-revised/최종 보고서.html',name:'output/q2-revised/최종 보고서.html',runId:'run-1',change:'created',size:1000,observedAt:1793400000},...Array.from({length:25},(_,i)=>({path:workspace+`/scratch/check-${i}.txt`,name:`scratch/check-${i}.txt`,runId:'run-1',change:'created',size:40,observedAt:1793400001+i}))];
const base={kind:'work',workspace,state:'done',trusted:true,created:1,updated:1,seq:0,requests:[],executions:[],toolActivity:[],artifacts:files,lastRunId:'run-1',connection:null};
const rows=[{...base,id:'report',title:'분기 운영 보고서',messages:[{role:'user',text:'Q2 수정 보고서를 만들고 기존 보고서는 보존해 주세요.',runId:'run-1'},{role:'assistant',text:'Q2 수정 보고서를 output/q2-revised/최종 보고서.html에 저장했습니다.',runId:'run-1'}]},{...base,id:'other',title:'다른 업무',artifacts:[],messages:[],state:'idle',lastRunId:null}];
const schedules=[{id:'schedule-1',kind:'once',enabled:true,text:'합성 예약입니다. 실제 실행하지 않습니다.',nextRunAt:1893456000,lastRun:{status:'paused',at:1793400000,message:'일시 정지'}}];
let limited=false;
const clone=value=>JSON.parse(JSON.stringify(value));
(async()=>{
 const service=await chromium.launchServer({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const browser=await chromium.connect(service.wsEndpoint());
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
  const drafts=new Map();
  await page.route('http://workspace.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(!p.startsWith('/api/')){
    const file=path.resolve(web,p==='/'?'index.html':p.slice(1));
    if(!file.startsWith(web+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:'missing'});
    return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.woff2':'font/woff2','.svg':'image/svg+xml','.png':'image/png'})[path.extname(file)]||'application/octet-stream'});
   }
   const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};
   calls.push({path:p,method:req.method()});let value={ok:true};
   if(p==='/api/bootstrap')value={sessions:rows,demo:false,appearance:{theme:'light'},executionMode:'normal',sessionGroups:{groups:[],assignments:{},revision:0},appUpdate:{autoCheck:false},runtime:{entry:'fixture'}};
   else if(p==='/api/session')value=rows.find(x=>x.id===url.searchParams.get('id'));
   else if(p==='/api/drafts'){if(req.method()==='GET')value={entries:[...drafts.values()],selectedId:'home'};else{drafts.set(body.id,{...body,revision:1});value={revision:1};}}
   else if(p==='/api/results')value={artifacts:url.searchParams.get('id')==='report'?files:[],lastRunId:'run-1',observation:{limited,errors:0,scope:'workspace-bounded-depth',maxDepth:3},availability:{}};
   else if(p==='/api/files')value={files:[{kind:'directory',name:'output',relativePath:'output',path:workspace+'/output'}],directory:''};
   else if(p==='/api/dispatch')value={queue:[],schedules,policy:{},steer:{supported:false}};
   else if(p==='/api/events'){await new Promise(r=>setTimeout(r,120));value={events:[]};}
   else if(p==='/api/attention')value={items:[],total:0,native:{},desktop:{preferences:{enabled:false},inbox:[],unreadCount:0},sessions:rows};
   else if(p==='/api/notifications')value={desktop:{preferences:{enabled:false},inbox:[],unreadCount:0}};
   else if(p==='/api/preview')value={kind:'html',name:'최종 보고서.html',html:'<!doctype html><html><body><h1>분기 운영 보고서</h1><table><tr><td>검증용 값</td><td>100</td></tr></table></body></html>',text:'<h1>분기 운영 보고서</h1>',message:'본문 확인용 미리보기',truncated:false};
   else if(p==='/api/schedules')value={items:[],total:0};
   else if(p==='/api/progress')value={records:[],hasMore:false,nextBefore:null,progress:{count:0,lastSeq:0,revision:0}};
   await route.fulfill({json:clone(value||{})});
  });
  await page.goto('http://workspace.test/#token=fixture');
  await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
  await page.evaluate(()=>selectSession('report'));
  await page.locator('.run-results').waitFor();
  await page.evaluate(()=>document.fonts.ready);
  assert.equal(await page.locator('#results-list > .result-card').count(),1,'only document candidates are initially rendered');
  for(const width of [1440,1000,760])for(const theme of ['light','dark']){
   await page.setViewportSize({width,height:1000});
   await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;setPanel('results');},theme);
   const report=await page.locator('.run-results').evaluate(node=>({text:node.innerText,overflow:node.scrollWidth-node.clientWidth,visibleFiles:[...node.querySelectorAll('.rich-file')].filter(n=>n.getClientRects().length).map(n=>n.innerText)}));
   assert.match(report.text,/최종 보고서/);assert.ok(report.overflow<=2,`result gallery overflow at ${width} ${theme}`);
   geometry.push({width,theme,galleryOverflow:report.overflow});
   await page.screenshot({path:path.join(out,`results-${width}-${theme}.png`),animations:'disabled'});
  }
  checks.push('nested final report stays visible with 25 auxiliary files across light/dark and narrow layouts');
  await page.setViewportSize({width:1440,height:1000});
  const summary=page.locator('.run-results details summary');
  assert.ok(await summary.count()>0,'large result lists need disclosure');
  await summary.first().click();
  await page.waitForFunction(()=>document.querySelector('.run-results').innerText.includes('check-24'));
  assert.match(await page.locator('.run-results').innerText(),/check-24/);
  await page.screenshot({path:path.join(out,'all-result-files.png'),animations:'disabled'});
  checks.push('all auxiliary files remain reachable after disclosure');
  limited=true;await page.evaluate(()=>refreshResults());
  assert.match(await page.locator('#results-list').innerText(),/확인하지 못|제한/);
  await page.screenshot({path:path.join(out,'scan-limited.png'),animations:'disabled'});
  await page.locator('#prompt').fill('아직 보내지 않은 수정 요청');
  await page.evaluate(()=>selectSession('other'));await page.evaluate(()=>selectSession('report'));
  assert.equal(await page.locator('#prompt').inputValue(),'아직 보내지 않은 수정 요청');
  checks.push('switching work preserves unsent draft and does not submit a model request');
  await page.locator('#workflow-open').click();
  await page.locator('#schedule-list .workflow-row').waitFor();
  const schedule=await page.locator('#schedule-list').innerText();
  assert.match(schedule,/예약 중/);assert.doesNotMatch(schedule,/최근 실행:[^\n]*일시 정지/);
  await page.screenshot({path:path.join(out,'schedule-state.png'),animations:'disabled'});
  await page.locator('#workflow-close').click();
  checks.push('schedule control event is not labelled as a completed execution');
  rows.push({...base,id:'interrupted',title:'완료 확인이 필요한 업무',state:'idle',pendingRunId:'run-1',artifacts:[],messages:[{role:'user',text:'자료를 정리해 주세요.',runId:'run-1'}],executions:[{id:'execution-1',runId:'run-1',tool:'Bash',description:'자료 확인',state:'interrupted',command:'example command',output:''}]});
  await page.evaluate(()=>selectSession('interrupted'));
  assert.equal(await page.locator('#request-recovery').isVisible(),true);
  assert.match(await page.locator('#request-recovery').innerText(),/완료 기록을 확인하지 못/);
  for(const theme of ['light','dark']){
   await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
   await page.screenshot({path:path.join(out,`recovery-${theme}.png`),animations:'disabled'});
  }
  await page.locator('#request-recovery button').filter({hasText:'진행 내용 보기'}).click();
  assert.equal(await page.locator('#progress-view details').getAttribute('open'),'');
  await page.evaluate(()=>WorkspaceProgressView.close());
  await page.setViewportSize({width:760,height:1000});
  await page.evaluate(()=>WorkspaceLayout.closeInspector({focus:false}));
  await page.locator('#request-recovery button').filter({hasText:'자료에서 파일 확인'}).click();
  assert.equal(await page.locator('#source-panel').isVisible(),true);
  const inspector=await page.locator('#inspector-panel').boundingBox();
  assert.ok(inspector.x>=0&&inspector.x+inspector.width<=760,'recovery opens the collapsed inspector inside narrow viewport');
  await page.screenshot({path:path.join(out,'recovery-narrow-materials.png'),animations:'disabled'});
  await page.evaluate(()=>WorkspaceLayout.closeInspector({focus:false}));
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(()=>selectSession('report'));
  assert.equal(await page.locator('#request-recovery').isVisible(),false);
  checks.push('unfinished request notice is scoped to its own conversation and disappears in a completed conversation');
  await page.evaluate(p=>preview(p),files[0].path);
  assert.equal(await page.locator('#preview-dialog').isVisible(),true);
  const frame=page.locator('#preview-content iframe');
  assert.equal(await frame.count(),1);assert.doesNotMatch(await frame.getAttribute('sandbox')||'',/allow-scripts/);
  await page.screenshot({path:path.join(out,'html-preview.png'),animations:'disabled'});
  checks.push('HTML preview remains sandboxed with scripts disabled');
  assert.equal(calls.some(c=>['/api/send','/api/connect','/api/stop'].includes(c.path)),false);
  assert.deepEqual(errors,[]);
  const result={passed:true,scope:'Synthetic API responses and shipped UI in headless Edge; no real model or personal state',checks,geometry,pageErrors:errors};
  fs.writeFileSync(path.join(out,'checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();await service.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
