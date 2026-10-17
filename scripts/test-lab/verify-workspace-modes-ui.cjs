/* Real shipped UI with synthetic HTTP only: never starts Claude or reads user files.
 * NODE_PATH=<playwright node_modules> node this-file --output <screenshots-dir>
 */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'local_app/web');
const output=process.argv.includes('--output')?path.resolve(process.argv[process.argv.indexOf('--output')+1]):null;
if(output)fs.mkdirSync(output,{recursive:true});
const session=(id,kind,title)=>({id,kind,title,workspace:'C:/fixture/'+id,state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[]});
const rows=[session('work-one','work','월간 실적 보고서'),session('work-two','work','다음 달 계획'),session('chat-one','chat','자료에서 핵심 찾기')];
const calls=[],errors=[],drafts=new Map();let n=0;
const desktop={preferences:{enabled:false,completed:true,attention:true,errors:true},inbox:[],unreadCount:0};
const bootstrap=()=>({sessions:rows.map(r=>({...r})),demo:false,appearance:{theme:'light'},executionMode:'normal',attachmentPolicy:{extensions:['.txt','.md','.csv','.png','.exe','.zip']},chatAttachmentPolicy:{extensions:['.txt','.md','.csv','.png']},sessionGroups:{groups:[{id:'reports',name:'보고서',collapsed:false}],assignments:{'work-one':'reports'},revision:0},appUpdate:{autoCheck:false},managedWorkspaceRoot:'C:/fixture',runtime:{entry:'fixture',configRoot:'fixture'}});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://workspace.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),p=url.pathname;
   if(!p.startsWith('/api/')){const filename=path.join(web,p==='/'?'index.html':p.slice(1));if(!filename.startsWith(web)||!fs.existsSync(filename))return route.fulfill({status:404,body:'missing'});const ext=path.extname(filename);return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff','.png':'image/png','.svg':'image/svg+xml'})[ext]||'application/octet-stream'});}
   let body={};if(req.headers()['content-type']?.includes('application/json'))body=req.postDataJSON()||{};
   calls.push({path:p,body});let result={ok:true};
   if(p==='/api/bootstrap')result=bootstrap();
   else if(p==='/api/drafts'){if(req.method()==='GET')result={entries:[...drafts.values()],selectedId:'home:work'};else {drafts.set(body.id,{...body,revision:(body.revision||0)+1});result={revision:(body.revision||0)+1};}}
   else if(p==='/api/session')result=rows.find(r=>r.id===url.searchParams.get('id'));
   else if(p==='/api/create'){result=session('chat-new-'+(++n),'chat','새 대화');rows.push(result);}
   else if(p==='/api/send'){const row=rows.find(r=>r.id===body.id);row.messages.push({role:'user',text:body.text,files:body.attachments});row.state='done';}
   else if(p==='/api/session/update'){result={session:Object.assign(rows.find(r=>r.id===body.id),{title:body.title})};}
   else if(p==='/api/session/delete'){rows.splice(rows.findIndex(r=>r.id===body.id),1);result={ok:true,deletedId:body.id,cleanupPending:false};}
   else if(p==='/api/session/hide'){rows.splice(rows.findIndex(r=>r.id===body.id),1);}
   else if(p==='/api/events'){await new Promise(r=>setTimeout(r,120));result={events:[]};}
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:rows.map(r=>({id:r.id,kind:r.kind,state:r.state}))};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/attachments/upload')result={path:'C:/fixture/copies/'+decodeURIComponent(req.headers()['x-file-name']),copied:true};
   await route.fulfill({status:result?200:404,json:result||{error:'not found'}});
  });
  await page.goto('http://workspace.test/#token=fixture');await page.locator('#sessions .session').first().waitFor();
  await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
  assert.equal(await page.locator('#sessions .session').count(),2,'Work excludes chats');
  assert.equal(await page.locator('#sessions .session-title').first().evaluate(e=>getComputedStyle(e).fontSize),'13px');
  await page.locator('#sessions [data-session-id="work-one"] .session').click();await page.waitForFunction(()=>active?.id==='work-one');
  await page.locator('#prompt').fill('업무 초안');await page.locator('#session-search').fill('실적');
  const before=calls.length;await page.locator('#mode-chat').click();
  assert.equal(await page.locator('#sessions .session').count(),1,'Chat excludes work');
  assert.equal(await page.locator('#prompt').inputValue(),'');
  assert.equal(await page.locator('#inspector-panel').isVisible(),false);
  assert.equal(await page.locator('#composer-permission').isVisible(),false);
  assert.equal(await page.locator('#capabilities-open').isVisible(),false);
  assert.equal(calls.slice(before).some(c=>['/api/connect','/api/create','/api/stop','/api/send'].includes(c.path)),false,'Switch has no execution side effect');
  await page.locator('#prompt').fill('대화 홈 초안');await page.locator('#session-search').fill('핵심');
  await page.locator('#mode-work').click();await page.waitForFunction(()=>active?.id==='work-one');
  assert.equal(await page.locator('#prompt').inputValue(),'업무 초안');assert.equal(await page.locator('#session-search').inputValue(),'실적');
  await page.locator('#session-search').fill('');
  await page.locator('#sessions [data-session-id="work-one"]').hover();
  await page.locator('#sessions [data-session-id="work-one"] .session-rename').click();
  await page.locator('#rename-input').fill('수정한 업무 이름');await page.locator('#rename-input').press('Enter');await page.locator('#rename-dialog').waitFor({state:'hidden'});
  assert.equal(rows[0].title,'수정한 업무 이름');
  await page.locator('#prompt').focus();await page.mouse.move(900,100);
  if(output)await page.screenshot({path:path.join(output,'work-light.png')});
  await page.locator('#appearance-toggle').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');await page.waitForTimeout(400);
  if(output)await page.screenshot({path:path.join(output,'work-dark.png')});
  await page.locator('#mode-chat').click();assert.equal(await page.locator('#prompt').inputValue(),'대화 홈 초안');assert.equal(await page.locator('#session-search').inputValue(),'핵심');
  await page.locator('#session-search').fill('');
  await page.locator('#chat-file-input').setInputFiles({name:'sample.txt',mimeType:'text/plain',buffer:Buffer.from('selected file only')});
  await page.waitForFunction(()=>document.querySelector('#attachments').textContent.includes('sample.txt'));
  assert.ok(calls.some(c=>c.path==='/api/create'&&c.body.kind==='chat'));
  assert.ok(calls.some(c=>c.path==='/api/attachments/upload'));
  assert.equal(await page.locator('#folder-dialog').isVisible(),false,'No workspace/trust dialog');
  await page.locator('#prompt').fill('이 파일의 핵심은?');await page.locator('#prompt').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#prompt').value==='');
  const sent=calls.filter(c=>c.path==='/api/send').at(-1);assert.equal(sent.body.attachments.length,1);
  assert.equal(sent.body.attachments[0],'C:/fixture/copies/sample.txt');
  await page.locator('#toast').waitFor({state:'hidden'});if(output)await page.screenshot({path:path.join(output,'chat-dark.png')});
  await page.locator('#appearance-toggle').click();await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');await page.waitForTimeout(400);
  if(output)await page.screenshot({path:path.join(output,'chat-light.png')});
  const current=rows.at(-1).id;await page.locator(`#sessions [data-session-id="${current}"]`).hover();await page.locator(`#sessions [data-session-id="${current}"] .session-remove`).click();
  await page.locator('#action-cancel').click();assert.ok(rows.some(r=>r.id===current));
  await page.locator(`#sessions [data-session-id="${current}"] .session-remove`).click();await page.locator('#action-confirm').click();
  await page.locator(`#sessions [data-session-id="${current}"]`).waitFor({state:'detached'});
  assert.ok(calls.some(c=>c.path==='/api/session/delete'&&c.body.confirmed===true));
  assert.equal(calls.some(c=>c.path==='/api/session/hide'),false,'Chat delete must not archive');
  const invalidBefore=calls.filter(c=>c.path==='/api/attachments/upload').length;
  await page.locator('#chat-file-input').setInputFiles({name:'program.exe',mimeType:'application/octet-stream',buffer:Buffer.from('no execution')});
  await page.locator('#toast').filter({hasText:'지원하지 않는'}).waitFor();
  assert.equal(calls.filter(c=>c.path==='/api/attachments/upload').length,invalidBefore);
  await page.locator('#prompt').fill('/skills');await page.waitForTimeout(400);assert.equal(await page.locator('#composer-suggestions').isVisible(),false);
  await page.setViewportSize({width:680,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(output)await page.screenshot({path:path.join(output,'chat-narrow.png')});
  assert.deepEqual(errors,[]);
  const result={passed:true,checks:['mode filtering','no connection on mode switch','per-mode selections, drafts, search','sidebar rename Enter','light/dark A2','general chat upload copies','lazy create without folder trust','send attachment reference','delete cancellation and permanent route','unsupported exe rejection','no slash controls','narrow layout'],pageErrors:errors};
  if(output)fs.writeFileSync(path.join(output,'ui-checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
