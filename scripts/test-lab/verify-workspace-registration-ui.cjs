// Render the shipped markup/styles and registration/update controllers offline.
// No application process, corporate address, employee ID or key is used.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const playwrightPath=process.env.WORKSPACE_PLAYWRIGHT_PATH||'playwright';
const {chromium}=require(playwrightPath);
const root=path.resolve(__dirname,'../..');
const web=path.join(root,'local_app/web');
const output=path.resolve(process.argv[2]||path.join(root,'outputs/registration-ui'));
const mime={'.css':'text/css','.js':'text/javascript','.woff':'font/woff','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
const initial={currentVersion:'0.23.45',status:'available',autoCheck:false,canInstall:true,
  release:{version:'0.23.46',title:'업데이트 기능 점검',notes:'화면 검증용 합성 배포 정보입니다.'}};
const notice='설치 또는 업데이트가 완료되면 결과를 한 번 전송합니다.\n이 집계 기능은 평소 앱 사용 중 사용 정보를 수집하지 않습니다.';
let browser;
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const launch={headless:true};
  if(process.env.WORKSPACE_BROWSER_PATH)launch.executablePath=process.env.WORKSPACE_BROWSER_PATH;
  browser=await chromium.launch(launch);
  const results=[];
  for(const [name,width,theme,registration,scenario] of [
    ['first-light',1280,'light',{enabled:true,employeeRegistered:false,needsEmployee:true},'first'],
    ['first-dark-mobile',320,'dark',{enabled:true,employeeRegistered:false,needsEmployee:true},'first'],
    ['registered-update',1280,'light',{enabled:true,employeeRegistered:true,needsEmployee:false},'saved'],
    ['missing-update-mobile',320,'light',{enabled:true,employeeRegistered:false,needsEmployee:false},'missing']
  ]){
    const page=await browser.newPage({viewport:{width,height:900},deviceScaleFactor:1,colorScheme:theme});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.hostname!=='workspace.test')return route.abort();
      const target=path.resolve(web,'.'+decodeURIComponent(url.pathname));
      if(!target.startsWith(web+path.sep)||!fs.existsSync(target)||!fs.statSync(target).isFile())return route.abort();
      await route.fulfill({body:fs.readFileSync(target),contentType:mime[path.extname(target)]||'application/octet-stream'});
    });
    let html=fs.readFileSync(path.join(web,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
    html=html.replace('<head>','<head><base href="http://workspace.test/">');
    await page.setContent(html);
    await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;},theme);
    await page.addScriptTag({content:fs.readFileSync(path.join(web,'registration.js'),'utf8')});
    await page.addScriptTag({content:fs.readFileSync(path.join(web,'app-updates.js'),'utf8')});
    await page.evaluate(({registration,initial})=>{
      window.calls=[];
      const openDialog=id=>document.getElementById(id).showModal();
      const api=async(path,data)=>{
        window.calls.push({path,data});
        return path==='/api/app-registration'?{enabled:true,employeeRegistered:true,needsEmployee:false}
          :{...initial,status:data?.action==='install'?'downloading':initial.status};
      };
      WorkspaceRegistration.attach({api,openDialog,canPrompt:()=>true,isClosed:()=>false,
        changed:()=>WorkspaceAppUpdates.registrationChanged()});
      WorkspaceAppUpdates.attach({api,openDialog,canOfferUpdate:()=>!WorkspaceRegistration.hasStartupPrompt()});
      WorkspaceRegistration.start(registration);WorkspaceAppUpdates.start(initial);
    },{registration,initial});
    await page.evaluate(()=>document.fonts.ready);
    let dialog;
    if(scenario==='first'){
      dialog=page.locator('#registration-dialog');assert.equal(await dialog.isVisible(),true);
      assert.equal(await page.locator('#registration-notice').textContent(),notice);
      await page.locator('#registration-input').fill('😀'.repeat(9));
      assert.equal(await page.locator('#registration-save').isDisabled(),true);
      await page.locator('#registration-input').fill('  TEST1234  ');
      assert.equal(await page.locator('#registration-save').isDisabled(),false);
      assert.equal(await page.locator('#registration-count').textContent(),'8 / 8자');
      assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.path==='/api/app-registration').length),0);
    }else{
      await page.locator('#app-update-sidebar-badge').click();dialog=page.locator('#app-update-dialog');
      assert.equal(await page.locator('#registration-dialog').isVisible(),false);
      assert.equal(await page.locator('#app-update-registration').isVisible(),scenario==='missing');
      if(scenario==='missing'){
        await page.locator('#app-update-registration-input').fill('TEST1234');
        assert.equal(await page.locator('#app-update-registration-notice').textContent(),notice);
      }
    }
    const bounds=await dialog.boundingBox();
    assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1,'dialog fits viewport');
    const overflow=await dialog.evaluate(el=>el.scrollWidth>el.clientWidth);
    assert.equal(overflow,false,'dialog has no horizontal clipping');
    await dialog.screenshot({path:path.join(output,name+'.png')});
    if(scenario!=='first'){
      await page.locator('#app-update-install').click();
      const calls=await page.evaluate(()=>window.calls.filter(call=>call.data?.action==='install'||call.data?.employeeId));
      assert.deepEqual(calls.map(call=>call.path),scenario==='missing'
        ?['/api/app-registration','/api/app-update']:['/api/app-update']);
      assert.equal(await page.locator('#app-update-registration').isVisible(),false);
    }
    assert.deepEqual(errors,[]);results.push({name,width,theme,passed:true});
    await page.close();
  }
  fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify({offline:true,results},null,2)+'\n');
  process.stdout.write(JSON.stringify({output,results}));
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();});
