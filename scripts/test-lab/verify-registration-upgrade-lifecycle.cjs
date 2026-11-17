// Opt-in lifecycle browser probe. APIs are the real isolated LocalApp server;
// the update launcher is replaced by the Python fixture, never the user's app.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.WORKSPACE_PLAYWRIGHT_PATH||'playwright');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'local_app/web');
const mime={'.css':'text/css','.js':'text/javascript','.woff':'font/woff','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
let browser;
(async()=>{
  const options=JSON.parse(fs.readFileSync(0,'utf8'));
  fs.mkdirSync(options.output,{recursive:true});
  const launch={headless:true};
  if(process.env.WORKSPACE_BROWSER_PATH)launch.executablePath=process.env.WORKSPACE_BROWSER_PATH;
  browser=await chromium.launch(launch);
  const page=await browser.newPage({viewport:{width:1280,height:900},deviceScaleFactor:1,colorScheme:'light'});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!==options.origin)return route.abort();
    if(url.pathname.startsWith('/api/'))return route.continue();
    if(url.pathname==='/'){
      const html=fs.readFileSync(path.join(web,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
      return route.fulfill({body:html,contentType:'text/html; charset=utf-8'});
    }
    const target=path.resolve(web,'.'+decodeURIComponent(url.pathname));
    if(!target.startsWith(web+path.sep)||!fs.existsSync(target)||!fs.statSync(target).isFile())return route.abort();
    return route.fulfill({body:fs.readFileSync(target),contentType:mime[path.extname(target)]||'application/octet-stream'});
  });
  await page.goto(options.origin+'/');
  await page.addScriptTag({content:fs.readFileSync(path.join(web,'registration.js'),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.join(web,'app-updates.js'),'utf8')});
  await page.evaluate(async({token})=>{
    window.lifecycleCalls=[];
    const api=async(route,data)=>{
      window.lifecycleCalls.push({path:route,action:data?.action||null});
      const response=await fetch(route,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+token,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});
      const result=await response.json();
      if(!response.ok)throw Error(result.error||'Local fixture request failed');
      return result;
    };
    const registration=await api('/api/app-registration'),update=await api('/api/app-update');
    window.lifecycleInitial=registration;
    const openDialog=id=>document.getElementById(id).showModal();
    WorkspaceRegistration.attach({api,openDialog,isClosed:()=>false,canPrompt:()=>true,
      changed:()=>WorkspaceAppUpdates.registrationChanged()});
    WorkspaceAppUpdates.attach({api,openDialog,canOfferUpdate:()=>!WorkspaceRegistration.hasStartupPrompt()});
    WorkspaceRegistration.start(registration);WorkspaceAppUpdates.start(update);
  },{token:options.token});
  await page.evaluate(()=>document.fonts.ready);
  const initial=await page.evaluate(()=>window.lifecycleInitial);
  const firstVisible=await page.locator('#registration-dialog').isVisible();
  assert.equal(firstVisible,options.expectPrompt,'first update alone prompts for registration');
  if(options.expectPrompt){
    await page.locator('#registration-input').fill('  TEST1234  ');
    await page.locator('#registration-dialog').screenshot({path:path.join(options.output,options.phase+'-registration.png')});
    await page.locator('#registration-save').click();
    await page.waitForFunction(()=>!document.getElementById('registration-dialog').open);
    await page.locator('#registration-dialog').waitFor({state:'hidden'});
  }
  await page.locator('#app-update-sidebar-badge').click();
  assert.equal(await page.locator('#registration-dialog').isVisible(),false);
  assert.equal(await page.locator('#app-update-registration').isVisible(),false,'registered/public update has no employee input');
  assert.equal(await page.locator('#app-update-dialog').innerText().then(text=>text.includes('TEST1234')),false,'saved employee value is not displayed');
  await page.locator('#app-update-dialog').screenshot({path:path.join(options.output,options.phase+'-update.png')});
  if(options.install){
    await page.locator('#app-update-install').click();
    await page.waitForFunction(()=>window.lifecycleCalls.some(call=>call.action==='install'));
  }
  const calls=await page.evaluate(()=>window.lifecycleCalls);
  assert.equal(calls.filter(call=>call.path==='/api/app-registration'&&call.action===null).length,options.expectPrompt?2:1,
    'exactly one local registration GET, plus first-only POST');
  assert.deepEqual(errors,[]);
  const evidence={phase:options.phase,initial,firstPromptVisible:firstVisible,
    updateEmployeeInputVisible:false,savedEmployeeValueVisible:false,
    registrationApiCalls:calls.filter(call=>call.path==='/api/app-registration').length,
    installApiCalls:calls.filter(call=>call.action==='install').length,passed:true};
  fs.writeFileSync(path.join(options.output,options.phase+'.json'),JSON.stringify(evidence,null,2)+'\n');
  process.stdout.write(JSON.stringify(evidence));
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();});
