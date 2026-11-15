// Actual LocalApp/Server + Chromium: related files, JS, policy and view cleanup.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),http=require('node:http');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'../..');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function run(){
  const index=process.argv.indexOf('--output');assert.ok(index>=0&&process.argv[index+1],'--output is required');
  const output=path.resolve(process.argv[index+1]);fs.mkdirSync(output,{recursive:true});
  const fixtureRoot=path.join(output,'fixture-'+Date.now());
  const python=process.env.WORKSPACE_PYTHON||'python';
  const child=spawn(python,['-X','utf8','-B',path.join(__dirname,'serve-project-preview-fixture.py'),'--output',fixtureRoot],
    {cwd:ROOT,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let childLog='',browser,context,runtime,outsideServer,outsideRequests=0,stopped=false;
  const exit=new Promise(resolve=>child.once('exit',(code,signal)=>resolve({code,signal})));
  child.stdout.on('data',chunk=>childLog+=chunk);child.stderr.on('data',chunk=>childLog+=chunk);
  const report={passed:false,realApi:true,isolatedProfile:true,realCliUsed:false,productionKeyUsed:false,checks:[]};
  const auth=()=>({Authorization:'Bearer '+new URL(runtime.url).hash.slice('#token='.length)});
  const status=async()=>fetch(runtime.origin+'/fixture-status',{headers:auth()}).then(r=>r.json());
  try{
    for(let attempt=0;attempt<150;attempt++){
      const file=path.join(fixtureRoot,'runtime.json');if(fs.existsSync(file)){runtime=JSON.parse(fs.readFileSync(file,'utf8'));break;}
      if(child.exitCode!==null)throw Error('Fixture exited before ready: '+childLog);await delay(100);
    }
    assert.ok(runtime,'Fixture did not start: '+childLog);
    outsideServer=http.createServer((_request,response)=>{outsideRequests++;response.writeHead(200,{'Content-Type':'text/html'});response.end('<h1>must not be requested</h1>');});
    await new Promise(resolve=>outsideServer.listen(0,'127.0.0.1',resolve));
    const outsideUrl='http://127.0.0.1:'+outsideServer.address().port+'/blocked-navigation';
    browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
    context=await browser.newContext({viewport:{width:2048,height:1152}});
    let externalNetworkAttempts=0;const errors=[],consoleViolations=[],page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>{if(/Content Security Policy|content security policy|Refused|violates/.test(message.text()))consoleViolations.push(message.text());});
    await context.route('**/*',route=>{
      const url=route.request().url();
      if(url.startsWith(runtime.origin+'/')||url===outsideUrl)return route.continue();
      externalNetworkAttempts++;return route.abort();
    });
    await page.goto(runtime.url);await page.waitForFunction(()=>typeof boot==='object'&&boot.application==='company-workspace');
    await page.evaluate(async id=>{await selectSession(id);},runtime.workId);
    await page.waitForFunction(id=>active?.id===id,runtime.workId);
    await page.evaluate(async entry=>{await preview(entry);},runtime.entry);
    await page.locator('#preview-content iframe').waitFor();
    assert.equal(await page.locator('#preview-content iframe').getAttribute('sandbox'),'');
    assert.equal((await status()).activePreviews,0);
    report.checks.push('Default HTML screen is scriptless and creates no project lease');
    await page.locator('#preview-web').click();
    const frameLocator=page.frameLocator('#preview-content iframe');
    await frameLocator.locator('body[data-fixture-ready="true"]').waitFor();
    const frame=await page.locator('#preview-content iframe').elementHandle();
    const webFrame=await frame.contentFrame();
    assert.equal(await page.locator('#preview-content iframe').getAttribute('sandbox'),'allow-scripts');
    assert.equal(await frameLocator.locator('#metric').innerText(),'42건');
    assert.equal(await frameLocator.locator('#metric').evaluate(node=>getComputedStyle(node).color),'rgb(92, 81, 181)');
    assert.equal(await frameLocator.locator('#fixture-image').evaluate(node=>node.complete&&node.naturalWidth===128),true);
    assert.equal(await frameLocator.locator('#chart-bar').evaluate(node=>node.style.width),'42%');
    await frameLocator.locator('#fixture-action').click();assert.equal(await frameLocator.locator('#fixture-result').innerText(),'분석 완료 42건');
    const security=await webFrame.evaluate(()=>window.previewFixture);
    assert.deepEqual(security,{parentDenied:true,appApiBlocked:true,externalBlocked:true,count:42});
    assert.equal(externalNetworkAttempts,0);
    report.checks.push('Separate CSS and @import, root-relative image, module JS/import and local JSON render and respond');
    report.checks.push('Opaque project frame cannot read parent document, call app API or fetch external resources');
    const before=await status();assert.equal(before.activePreviews,1);
    assert.ok(before.resourceRequests.includes('styles/tokens.css'));assert.ok(before.resourceRequests.includes('js/math.js'));
    assert.ok(before.resourceRequests.includes('data/metrics.json'));assert.ok(!before.resourceRequests.some(value=>value.startsWith('unused/')));
    const marker=await webFrame.evaluate(()=>window.fixtureDocumentIdentity=Math.random());
    const leaseUrl=await page.locator('#preview-content iframe').getAttribute('src');
    for(let count=0;count<5;count++)await page.locator('#preview-web').click();
    assert.equal(await webFrame.evaluate(()=>window.fixtureDocumentIdentity),marker);
    assert.equal((await status()).resourceRequests.length,before.resourceRequests.length);
    await page.locator('#explorer-expand').click();
    assert.equal(await webFrame.evaluate(()=>window.fixtureDocumentIdentity),marker);
    await page.locator('#explorer-popup').click();await page.locator('#explorer-dialog[open]').waitFor();
    assert.equal(await webFrame.evaluate(()=>window.fixtureDocumentIdentity),marker);
    await page.locator('#explorer-popup').click();
    assert.equal(await webFrame.evaluate(()=>window.fixtureDocumentIdentity),marker);
    // Closing a dialog keeps its native backdrop during the CSS exit transition.
    // Wait for that actual transition instead of capturing a blurred midway frame.
    await page.waitForFunction(()=>!document.querySelector('#explorer-dialog').open&&
      getComputedStyle(document.querySelector('#explorer-dialog')).display==='none');
    report.checks.push('Repeated web selection and expand/popup moves preserve one document without repeating asset requests');
    for(const width of [2048,1280]){
      await page.setViewportSize({width,height:width===2048?1152:900});
      for(const theme of ['light','dark']){
        if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme){
          await page.locator('#appearance-toggle').click();await page.waitForFunction(value=>document.documentElement.dataset.theme===value,theme);
        }
        await page.evaluate(async()=>{
          await document.fonts.ready;
          await Promise.all(document.getAnimations().filter(animation=>
            animation.effect?.getComputedTiming().endTime!==Infinity).map(animation=>animation.finished.catch(()=>{})));
          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        });
        assert.equal(await page.locator('dialog[open]').count(),0);
        assert.equal(await page.locator('#explorer-dialog').evaluate(node=>getComputedStyle(node).display),'none');
        await page.screenshot({path:path.join(output,'project-preview-'+width+'-'+theme+'.png')});
      }
    }
    report.screenshots=['2048-light','2048-dark','1280-light','1280-dark'];
    await page.setViewportSize({width:2048,height:1152});
    await webFrame.evaluate(url=>{window.location.href=url;},outsideUrl).catch(error=>{
      if(!/context|destroyed|closed|detached/i.test(error.message))throw error;
    });
    await delay(300);assert.equal(outsideRequests,0);
    report.checks.push('Parent frame policy blocks navigation to another loopback origin before any request reaches it');
    await page.locator('#preview-code').click();await page.waitForFunction(()=>document.querySelector('#preview-content iframe')===null);
    for(let attempt=0;attempt<50&&(await status()).activePreviews;attempt++)await delay(30);
    assert.equal((await status()).activePreviews,0);
    assert.ok((await fetch(runtime.origin+leaseUrl)).status>=400);
    assert.ok((await page.locator('#preview-content').innerText()).includes('script'));
    report.checks.push('Switching to code removes the running frame and invalidates its old resource URL');
    await page.locator('#preview-web').click();await page.frameLocator('#preview-content iframe').locator('body[data-fixture-ready="true"]').waitFor();
    assert.equal((await status()).activePreviews,1);
    await page.locator('#explorer-back').click();
    for(let attempt=0;attempt<50&&(await status()).activePreviews;attempt++)await delay(30);
    assert.equal((await status()).activePreviews,0);assert.equal(await page.locator('#preview-content iframe').count(),0);
    report.checks.push('Closing the explorer frees the project lease and iframe');
    assert.deepEqual(errors,[]);
    report.pageErrors=errors;report.expectedPolicyDenials=consoleViolations.length;
    report.externalNetworkAttempts=externalNetworkAttempts;report.outsideNavigationRequests=outsideRequests;
    report.fixtureState=await status();report.passed=true;
  }finally{
    await context?.close();await browser?.close();
    if(outsideServer)await new Promise(resolve=>outsideServer.close(resolve));
    if(runtime){try{await fetch(runtime.origin+'/fixture-stop',{method:'POST',headers:auth()});}catch{}}
    const result=await Promise.race([exit,delay(10000).then(()=>null)]);
    if(!result){child.kill();throw Error('Owned fixture did not stop normally');}
    stopped=result.code===0;
    report.fixtureStopped=stopped;report.fixtureExit=result;
    fs.writeFileSync(path.join(output,'fixture.log'),childLog);
    fs.writeFileSync(path.join(output,'project-preview-browser-report.json'),JSON.stringify(report,null,2));
    assert.equal(stopped,true,'Owned fixture failed to shut down: '+childLog);
  }
  console.log(JSON.stringify(report,null,2));
}
if(require.main===module)run().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
