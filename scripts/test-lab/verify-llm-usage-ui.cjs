/* Real shipped markup/styles with an in-memory usage API, no personal settings. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const output=path.resolve(process.argv[2]||'outputs/usage-ui');fs.mkdirSync(output,{recursive:true});
async function assertUsageUnavailable(page){
 assert.equal(await page.locator('#llm-usage-percent').textContent(),'—');
 assert.equal(await page.locator('#llm-usage-amount').textContent(),'사용량을 조회하지 못했어요.');
 assert.equal(await page.locator('#llm-usage-remaining').textContent(),'');
 assert.equal(await page.locator('#llm-usage-detail-amount').textContent(),'조회 불가');
 assert.equal(await page.locator('#llm-usage-detail-remaining').textContent(),'조회 불가');
 assert.equal(await page.locator('#llm-usage-reset').textContent(),'조회 불가');
 assert.equal(await page.locator('#llm-usage-reset').getAttribute('title'),null);
 assert(await page.locator('#llm-usage-reset-note').isHidden());
 assert(await page.locator('#llm-usage-track').isHidden());
 assert.equal(await page.locator('#llm-usage-track').getAttribute('aria-valuenow'),null);
 assert.equal(await page.locator('#llm-usage').getAttribute('data-level'),'unavailable');
 assert.doesNotMatch(await page.locator('#llm-usage-toggle').innerText(),/\$|\d+%|남음/);
 assert.doesNotMatch(await page.locator('#llm-usage-updated').textContent(),/이전 조회 값|마지막 확인/);
}
async function main(){
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const errors=[],checks=[];
 try{
  for(const theme of ['light','dark']){
   const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:theme==='light'?'America/New_York':'Asia/Seoul'});
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('http://usage-ui.test/**',async route=>{
    const p=new URL(route.request().url()).pathname;
    const filename=path.resolve(web,p==='/'?'index.html':p.slice(1));
    if(!filename.startsWith(web+path.sep)||!fs.existsSync(filename))return route.fulfill({status:404});
    let raw=fs.readFileSync(filename);
    if(p==='/')raw=raw.toString().replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')+'<script src="/llm-usage.js"></script>';
    await route.fulfill({body:raw,contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff':'font/woff'})[path.extname(filename)]||'application/octet-stream'});
   });
   await page.goto('http://usage-ui.test/');
   await page.clock.install();
   await page.clock.pauseAt(new Date(Date.now()+1000));
   await page.evaluate(theme=>{
    document.documentElement.dataset.theme=theme;
    window.calls=[];
    window.value={loading:false,error:'not_configured',diagnostic:{stage:'configuration'},connection:{editable:true,mcpUrl:'',source:'missing'}};
    WorkspaceLlmUsage.start({api:async(_path,data)=>{
     calls.push(data||null);
     if(window.rejectApi)throw new Error('fixture unavailable');
     if(data?.configure){value={...value,error:'http_error',diagnostic:{stage:'initialize',httpStatus:503,networkPath:data.configure.networkMode==='direct'?'direct':'proxy'},connection:{editable:true,mcpUrl:data.configure.mcpUrl,source:'test',networkMode:data.configure.networkMode}};}
     return structuredClone(value);
    }});
   },theme);
   await page.clock.runFor(1200);
   await page.waitForFunction(()=>document.getElementById('llm-usage-connection').hidden===false);
   await page.locator('#llm-usage-toggle').click();
   await page.locator('#llm-usage-url').fill('http://usage.example.test/mcp');
   await page.locator('#llm-usage-connect').click();
   await page.waitForFunction(()=>document.getElementById('llm-usage-diagnostic').textContent.includes('HTTP 503'));
   await assertUsageUnavailable(page);
   assert.equal(await page.locator('#llm-usage-url').inputValue(),'http://usage.example.test/mcp');
   assert.equal(await page.evaluate(()=>calls.filter(x=>x?.configure).length),1);
   assert.match(await page.locator('#llm-usage-diagnostic').textContent(),/서버 연결 초기화/);
   assert.match(await page.locator('#llm-usage-network-state').textContent(),/프록시 경유/);
   await page.locator('[name="llm-usage-network"][value="direct"]').check();
   await page.locator('#llm-usage-connect').click();
   await page.waitForFunction(()=>document.getElementById('llm-usage-network-state').textContent.includes('직접 연결'));
   assert.equal(await page.evaluate(()=>calls.filter(x=>x?.configure).at(-1).configure.networkMode),'direct');
   await page.evaluate(()=>document.fonts.ready);
   const desktopBounds=await page.locator('#llm-usage-details').boundingBox();
   assert(desktopBounds.y>=0 && desktopBounds.y+desktopBounds.height<=1000);
   await page.locator('#llm-usage-details').screenshot({path:path.join(output,theme+'.png')});
   await page.locator('#llm-usage-url').fill('http://edited.example.test/mcp');
   await page.locator('#llm-usage-refresh').click();
   assert.equal(await page.locator('#llm-usage-url').inputValue(),'http://edited.example.test/mcp');
   await page.setViewportSize({width:680,height:680});
   const bounds=await page.locator('#llm-usage-details').boundingBox();
   assert(bounds.y>=0 && bounds.x>=0 && bounds.y+bounds.height<=680 && bounds.x+bounds.width<=680);
   await page.evaluate(()=>{value={loading:false,error:null,pollAfterMs:300000,connection:{editable:false},usage:{spend:2,max:20,remaining:18,percent:10,team:'test',teams:[{id:'test',name:'검증 팀'}],updatedAt:Date.now()/1000,resetAt:'2026-10-28T00:00:00'}};});
   await page.locator('#llm-usage-refresh').click();
   await page.waitForFunction(()=>document.getElementById('llm-usage-percent').textContent==='10%');
   assert(await page.locator('#llm-usage-connection').isHidden());
   assert(await page.locator('#llm-usage-diagnostic').isHidden());
   assert.match(await page.locator('#llm-usage-reset').textContent(),/09:00 KST/);
   assert(await page.locator('#llm-usage-reset-note').isVisible());
   assert.match(await page.locator('#llm-usage-reset').getAttribute('title'),/2026-10-28T00:00:00/);
   await page.locator('#llm-usage-details').screenshot({path:path.join(output,theme+'-reset.png')});
   for(const [raw,expected,naive] of [
    ['2026-10-28T00:00:00Z','09:00 KST',false],
    ['2026-10-28T00:00:00+09:00','00:00 KST',false],
    ['2026-10-28T09:00:00+09:00','09:00 KST',false],
    ['2026-10-28T00:00:00.123456','09:00 KST',true],
    ['2026-10-28 00:00:00','09:00 KST',true],
    ['2026-10-28T18:30:00Z','03:30 KST',false],
    ['2026-02-30T00:00:00Z','시각 확인 필요',false],
    ['2026-10-28T24:00:00Z','시각 확인 필요',false],
    ['invalid','시각 확인 필요',false],['','제공되지 않음',false]
   ]){
    await page.evaluate(raw=>{value.usage.resetAt=raw;},raw);
    await page.locator('#llm-usage-refresh').click();
    assert((await page.locator('#llm-usage-reset').textContent()).includes(expected),raw);
    assert.equal(await page.locator('#llm-usage-reset-note').isVisible(),naive,raw);
    if(raw==='2026-10-28T18:30:00Z')assert.match(await page.locator('#llm-usage-reset').textContent(),/29/);
   }
   await page.evaluate(()=>{value={...value,error:'http_error',pollAfterMs:60000,diagnostic:{stage:'initialize',httpStatus:503}};value.usage.resetAt='2026-10-28T00:00:00';});
   await page.locator('#llm-usage-refresh').click();
   await assertUsageUnavailable(page);
   assert(await page.locator('#llm-usage-team').isEnabled());
   await page.locator('#llm-usage-details').screenshot({path:path.join(output,theme+'-failed.png')});
   const before=await page.evaluate(()=>calls.length);
   await page.clock.runFor(59999);
   assert.equal(await page.evaluate(()=>calls.length),before);
   await page.evaluate(()=>{value={...value,error:null,diagnostic:null,pollAfterMs:300000,usage:{...value.usage,spend:3,remaining:17,percent:15}};});
   await page.clock.runFor(1);
   assert.equal(await page.evaluate(()=>calls.length),before+1);
   assert(await page.locator('#llm-usage-diagnostic').isHidden());
   assert(!(await page.locator('#llm-usage-updated').textContent()).includes('이전 조회 값'));
   assert.equal(await page.locator('#llm-usage-percent').textContent(),'15%');
   assert.equal(await page.locator('#llm-usage-detail-remaining').textContent(),'$17.00');
   assert(await page.locator('#llm-usage-track').isVisible());
   assert.equal(await page.locator('#llm-usage-track').getAttribute('aria-valuenow'),'15');
   await page.evaluate(()=>{value={...value,loading:true,error:'http_error',pollAfterMs:1000,retry:{attempt:1,limit:2},diagnostic:{stage:'initialize',httpStatus:503}};});
   await page.locator('#llm-usage-refresh').click();
   assert.match(await page.locator('#llm-usage-detail-state').textContent(),/다시 확인 중 \(1\/2\)/);
   await assertUsageUnavailable(page);
   assert(await page.locator('#llm-usage-refresh').isDisabled());
   await page.locator('#llm-usage-details').screenshot({path:path.join(output,theme+'-retry.png')});
   await page.evaluate(()=>{value={...value,loading:false,error:null,retry:null,diagnostic:null,pollAfterMs:300000};});
   await page.clock.runFor(1000);
   assert(await page.locator('#llm-usage-refresh').isEnabled());
   assert.equal(await page.locator('#llm-usage-percent').textContent(),'15%');
   // Authentication failure also hides a formerly high usage/warning balance.
   await page.evaluate(()=>{value={...value,error:'key_required',pollAfterMs:60000,diagnostic:{stage:'initialize',httpStatus:401},usage:{...value.usage,spend:19,remaining:1,percent:95}};});
   await page.locator('#llm-usage-refresh').click();
   await assertUsageUnavailable(page);
   assert.match(await page.locator('#llm-usage-detail-state').textContent(),/LLM Key 확인/);
   await page.evaluate(()=>{value={...value,error:null,diagnostic:null,pollAfterMs:300000};});
   await page.locator('#llm-usage-refresh').click();
   assert.equal(await page.locator('#llm-usage-percent').textContent(),'95%');
   // A local API failure also polls in one minute, never leaves a stuck retry label.
   await page.evaluate(()=>{window.rejectApi=true;});
   await page.locator('#llm-usage-refresh').click();
   await assertUsageUnavailable(page);
   const beforeLocalFailure=await page.evaluate(()=>calls.length);
   await page.evaluate(()=>{window.rejectApi=false;});
   await page.clock.runFor(60000);
   assert.equal(await page.evaluate(()=>calls.length),beforeLocalFailure+1);
   assert(!(await page.locator('#llm-usage-updated').textContent()).includes('이전 조회 값'));
   assert.equal(await page.locator('#llm-usage-percent').textContent(),'95%');
   checks.push(theme+': address and route save, 503 diagnosis, polling edit preservation, small viewport, production editor hidden, 24-hour KST reset across host timezones, failure polling at 60s, no cached balances during failure/retry/API error, fresh values after recovery');
   await page.close();
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({checks,errors},null,2));
  console.log(JSON.stringify({checks,errors},null,2));
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
