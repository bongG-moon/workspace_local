/* Shipped markup/styles with synthetic API responses. No Claude or user files. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const row=(id,kind,title)=>({id,kind,title,workspace:'C:/fixture/'+id,state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[]});
const rows=[row('work-one','work','월간 보고서')],calls=[],errors=[];
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://workspace.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(!p.startsWith('/api/')){const filename=path.join(web,p==='/'?'index.html':p.slice(1));if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.png':'image/png','.svg':'image/svg+xml'})[path.extname(filename)]||'application/octet-stream'});}
   const body=req.headers()['content-type']?.includes('application/json')?req.postDataJSON()||{}:{};calls.push({path:p,body});let result={ok:true};
   if(p==='/api/bootstrap')result={sessions:rows,demo:false,appearance:{theme:'light'},executionMode:'normal',chatAttachmentPolicy:{extensions:['.txt']},sessionGroups:{groups:[],assignments:{},revision:0},appUpdate:{autoCheck:false},runtime:{entry:'fixture'}};
   else if(p==='/api/drafts')result=req.method()==='GET'?{entries:[],selectedId:'home'}:{revision:1};
   else if(p==='/api/session')result=rows.find(r=>r.id===url.searchParams.get('id'));
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:rows};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/events'){await new Promise(r=>setTimeout(r,150));result={events:[]};}
   await route.fulfill({json:result});
  });
  await page.goto('http://workspace.test/#token=fixture');
  await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
  assert.equal(await page.locator('.chat-home').isVisible(),false);
  assert.equal(await page.locator('#prompt-shortcuts').isVisible(),true);
  await page.locator('#mode-chat').click();
  assert.equal(await page.locator('.chat-starter').count(),4);
  assert.equal(await page.locator('.work-home-only').first().isVisible(),false);
  assert.equal(await page.locator('#prompt-shortcuts').isVisible(),false);
  assert.equal(await page.locator('.chat-recents-empty').isVisible(),true);
  const before=calls.length;
  await page.locator('.chat-starter').first().click();
  assert.match(await page.locator('#prompt').inputValue(),/내 생각을 정리/);
  assert.equal(await page.locator('#prompt').evaluate(el=>el===document.activeElement),true);
  await page.locator('#prompt').fill('제가 쓴 질문은 그대로 유지해 주세요.');
  await page.evaluate(()=>{attachments=[{path:'C:/fixture/owned/report.txt',copied:true}];renderAttachments();});
  await page.locator('.chat-starter').nth(2).click();
  assert.match(await page.locator('#prompt').inputValue(),/^제가 쓴 질문은 그대로 유지해 주세요\.\n\n내가 쓴 글/);
  assert.equal(await page.evaluate(()=>attachments[0].path),'C:/fixture/owned/report.txt');
  assert.equal(calls.slice(before).some(c=>['/api/create','/api/connect','/api/send','/api/stop'].includes(c.path)),false);
  await page.evaluate(()=>{attachments=[];renderAttachments();document.getElementById('prompt').value='';document.getElementById('toast').hidden=true;});
  const shot=async(name)=>{await page.mouse.move(1000,60);await page.locator('#chat-title').click();if(output)await page.screenshot({path:path.join(output,name+'.png')});};
  await shot('chat-home-empty-light');
  await page.locator('#appearance-toggle').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
  await shot('chat-home-empty-dark');
  const recent=[row('chat-one','chat','회의에서 나온 생각을 다음 단계로 정리하기'),row('chat-two','chat','아주 긴 이름의 대화도 자연스럽게 한 줄로 표시되도록 하는 화면 검증용 대화 제목입니다'.repeat(3))];rows.push(...recent);
  await page.evaluate(value=>{sessions=value;renderSessions();},rows);
  assert.equal(await page.locator('#home-recents .recent-card').count(),2);
  assert.equal(await page.locator('.chat-recents-empty').count(),0);
  const geometry=await page.evaluate(()=>{const bounds=el=>{const r=el.getBoundingClientRect();return {x:r.x,width:r.width,right:r.right}};return {cards:bounds(document.querySelector('.chat-starters')),composer:bounds(document.querySelector('.composer')),overflow:document.documentElement.scrollWidth>innerWidth,recent:[...document.querySelectorAll('#home-recents .recent-card strong')].map(el=>({scroll:el.scrollWidth,client:el.clientWidth,overflow:getComputedStyle(el).textOverflow}))};});
  assert.equal(geometry.overflow,false);assert.ok(Math.abs(geometry.cards.x-geometry.composer.x)<1);assert.ok(Math.abs(geometry.cards.width-geometry.composer.width)<1);assert.equal(geometry.recent[1].overflow,'ellipsis');
  await shot('chat-home-recents-dark');
  await page.locator('#appearance-toggle').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');await shot('chat-home-recents-light');
  await page.setViewportSize({width:680,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await shot('chat-home-narrow-light');
  await page.locator('#appearance-toggle').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');await shot('chat-home-narrow-dark');
  await page.setViewportSize({width:420,height:850});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.equal(await page.locator('.chat-starter').first().isVisible(),true);await shot('chat-home-mobile-dark');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#home-recents .recent-card').first().click();await page.waitForFunction(()=>active?.id==='chat-one');assert.equal(await page.locator('.chat-home').isVisible(),false);assert.equal(calls.some(c=>c.path==='/api/connect'),false);
  await page.locator('#mode-work').click();assert.equal(await page.locator('.chat-home').isVisible(),false);assert.equal(await page.locator('#prompt-shortcuts').isVisible(),true);assert.equal(await page.locator('.work-home-only').first().isVisible(),true);
  assert.deepEqual(errors,[]);
  const result={passed:true,checks:['work home unchanged','chat-only hero and four starters','empty recent panel','starter inserts editable draft','existing draft and attachments preserved','no create/connect/send side effect','light and dark home','recent cards and long title ellipsis','composer alignment','680px and 420px no overflow','recent card navigation','no home decoration in conversation'],geometry,pageErrors:errors};
  if(output)fs.writeFileSync(path.join(output,'chat-home-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
