/* Real application, native browser focus/keyboard/pointer events; synthetic API data only. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../local_app/web');
const outputIndex=process.argv.indexOf('--output');
if(outputIndex!==-1&&!process.argv[outputIndex+1])throw new Error('--output requires a directory');
const output=outputIndex===-1?null:path.resolve(process.argv[outputIndex+1]);
if(output)fs.mkdirSync(output,{recursive:true});
const checks=[],errors=[];
const row={id:'focus-work',kind:'work',title:'월간 실적 보고서',workspace:'C:/fixture/reports',state:'idle',trusted:true,created:1,updated:1,seq:0,messages:[],requests:[],artifacts:[],toolActivity:[],executions:[]};
const desktop={preferences:{enabled:false},inbox:[],unreadCount:0};

(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.WORKSPACE_BROWSER||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const page=await context.newPage();
  page.setDefaultTimeout(7000);
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
   let result={ok:true};
   if(p==='/api/bootstrap')result={sessions:[row],demo:false,appearance:{theme:'light'},executionMode:'normal',sessionGroups:{groups:[],assignments:{},revision:0},runtime:{entry:'fixture'}};
   else if(p==='/api/drafts')result=req.method()==='GET'?{entries:[],selectedId:'home'}:{revision:1};
   else if(p==='/api/session')result=row;
   else if(p==='/api/attention')result={items:[],total:0,native:{},desktop,sessions:[row]};
   else if(p==='/api/notifications')result={desktop};
   else if(p==='/api/appearance')result={theme:body.theme,nativeApplied:true};
   else if(p==='/api/files')result={files:[]};
   else if(p==='/api/results')result={artifacts:[],lastRunId:null};
   else if(p==='/api/dispatch')result={queue:[],schedules:[],policy:{},steer:{supported:false}};
   else if(p==='/api/events'){await new Promise(resolve=>setTimeout(resolve,100));result={events:[]};}
   return route.fulfill({json:result});
  });
  const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const shot=async name=>{if(output)await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'})};
  const style=locator=>locator.evaluate(el=>{
   const s=getComputedStyle(el);
   return {focused:document.activeElement===el,nativeFocusVisible:el.matches(':focus-visible'),outline:s.outlineStyle,outlineWidth:parseFloat(s.outlineWidth),outlineColor:s.outlineColor,border:s.borderColor};
  });
  const transparent=color=>color==='transparent'||/^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(color);
  const ring=state=>!['none','hidden'].includes(state.outline)&&state.outlineWidth>0&&!transparent(state.outlineColor);
  const expectRing=async(locator,expected,label)=>assert.equal(ring(await style(locator)),expected,label);
  const expectNoFocusedRing=async label=>{
   const outlined=await page.evaluate(()=>{
    const results=[];
    for(let el=document.activeElement;el&&el!==document.documentElement;el=el.parentElement){
     const s=getComputedStyle(el);
     if(!['none','hidden'].includes(s.outlineStyle)&&parseFloat(s.outlineWidth)>0&&s.outlineColor!=='transparent'&&!/^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(s.outlineColor))results.push(el.id||el.className||el.tagName);
    }
    return results;
   });
   assert.deepEqual(outlined,[],label);
  };
  const tabTo=async(locator,{backward=false}={})=>{
   for(let count=0;count<90;count++){
    await page.keyboard.press(backward?'Shift+Tab':'Tab');
    if(await locator.evaluate(el=>document.activeElement===el))return;
   }
   throw new Error('Tab navigation did not reach '+locator);
  };
  const pointerNeutral=async()=>{await page.mouse.click(700,95);await page.mouse.move(700,96);await settle()};
  const home=page.locator('#home-button'),search=page.locator('#session-search'),searchWrap=page.locator('.session-search');
  const shortcuts=page.locator('#shortcuts-dialog'),shortcutSearch=page.locator('#shortcuts-search');
  const handle=page.locator('#inspector-resizer');
  const divider=()=>handle.evaluate(el=>{const s=getComputedStyle(el,'::before');return {width:parseFloat(s.width),background:s.backgroundColor,focused:document.activeElement===el,dragging:document.querySelector('.app').classList.contains('inspector-resizing')}});
  const painted=state=>!transparent(state.background);

  await page.goto('http://workspace.test/#token=fixture');
  await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);
  await page.evaluate(()=>document.fonts.ready);await settle();
  await home.focus();
  assert.equal((await style(home)).nativeFocusVisible,true,'fixture must reproduce Chromium initial programmatic :focus-visible');
  if(output){
   // Evidence only: temporarily reveal the prior CSS result on this synthetic
   // page, preserving the same DOM focus before restoring the real policy.
   const mode=await page.evaluate(()=>{const root=document.documentElement,mode=root.getAttribute('data-focus-navigation');root.removeAttribute('data-focus-navigation');return mode});
   try{await expectRing(home,true,'simulated prior CSS exposes the unwanted native focus ring');await shot('simulated-previous-css-home')}
   finally{await page.evaluate(mode=>{if(mode!==null)document.documentElement.setAttribute('data-focus-navigation',mode)},mode)}
  }
  await expectRing(home,false,'initial programmatic home focus must not paint a ring');
  assert.equal((await style(home)).focused,true,'suppressing a ring must preserve DOM focus');
  checks.push('fresh programmatic home focus reproduces native :focus-visible without a visible ring');
  await shot('initial-pointer-focus');

  await page.keyboard.press('Tab');
  assert.equal((await style(page.locator('#sidebar-toggle'))).focused,true);
  await expectRing(page.locator('#sidebar-toggle'),true,'real Tab must expose focus');
  await page.keyboard.press('Shift+Tab');
  assert.equal((await style(home)).focused,true);
  await expectRing(home,true,'real Shift+Tab must expose focus');
  await shot('keyboard-home-initial-light');
  // Emulate the window blur/focus notifications supplied by the native host;
  // the browser keeps its DOM focus target across this reactivation sequence.
  await page.evaluate(()=>{window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('focus'))});
  assert.equal((await style(home)).focused,true,'window reactivation must retain DOM focus');
  await expectRing(home,false,'window reactivation alone must not restore a keyboard ring');
  await page.keyboard.press('Tab');
  await expectRing(page.locator('#sidebar-toggle'),true,'Tab after reactivation must restore keyboard visibility');
  await page.keyboard.press('Shift+Tab');
  checks.push('window blur/focus reactivation retains DOM focus quietly until the next Tab');
  const homeBox=await home.boundingBox();
  await page.mouse.move(homeBox.x+homeBox.width/2,homeBox.y+homeBox.height/2);await page.mouse.down();
  assert.equal((await style(home)).focused,true,'pointerdown must not blur the focused control');
  await expectRing(home,false,'pointerdown must immediately remove keyboard ring');
  await page.mouse.up();await settle();await expectNoFocusedRing('home click must not restore a ring');
  checks.push('Tab and Shift+Tab expose rings; pointerdown immediately hides them without blurring');

  for(let cycle=0;cycle<2;cycle++){
   await page.locator('#shortcuts-open').click();
   assert.equal(await shortcuts.evaluate(el=>el.open),true);
   assert.equal(await page.locator('#shortcuts-title').evaluate(el=>document.activeElement===el),true);
   await expectNoFocusedRing('pointer-opened shortcut dialog autofocus');
   await page.locator('#shortcuts-close').click();await settle();
   assert.equal(await page.locator('#shortcuts-open').evaluate(el=>document.activeElement===el),true);
   await expectNoFocusedRing('pointer-closed shortcut dialog focus return');
  }
  await page.locator('#shortcuts-open').click();await page.keyboard.press('Escape');await settle();
  await expectNoFocusedRing('Escape after pointer opening does not invent keyboard navigation');
  await page.locator('#shortcuts-open').click();await page.keyboard.press('Tab');
  assert.equal((await style(page.locator('#shortcuts-close'))).focused,true);
  await expectRing(page.locator('#shortcuts-close'),true,'Tab inside shortcut dialog');
  await page.keyboard.press('Tab');
  assert.equal((await style(shortcutSearch)).focused,true);
  await expectRing(page.locator('.shortcuts-search'),true,'keyboard focus must ring the search wrapper');
  await expectRing(shortcutSearch,false,'search input does not get a second nested ring');
  await shot('keyboard-shortcuts-search');
  await page.keyboard.press('Escape');await settle();
  await expectRing(page.locator('#shortcuts-open'),true,'keyboard navigation survives dialog focus restoration');
  checks.push('repeated pointer popup open/close has no ring; keyboard dialog navigation and return keep rings');

  await page.locator('#shortcuts-open').click();await shortcutSearch.click();
  await page.keyboard.type('Enter');await page.keyboard.press('Home');await page.keyboard.press('ArrowRight');await page.keyboard.press('End');
  assert.equal(await shortcutSearch.inputValue(),'Enter');
  await expectNoFocusedRing('typing and caret movement in a pointer-focused popup search');
  await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  assert.equal((await style(shortcutSearch)).focused,true);
  await expectRing(page.locator('.shortcuts-search'),true,'returning to search using Tab must expose wrapper ring');
  await page.locator('#shortcuts-close').click();await settle();
  await page.locator('#settings-open').click();
  assert.equal(await page.locator('#settings-dialog').evaluate(el=>el.open),true);
  await expectNoFocusedRing('pointer-opened settings autofocus');
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(()=>document.getElementById('settings-dialog').contains(document.activeElement)));
  const settingsFocus=page.locator('#settings-dialog :focus');
  await expectRing(settingsFocus,true,'keyboard navigation inside settings');
  await page.locator('#settings-close').click();await settle();await expectNoFocusedRing('pointer settings close');
  checks.push('popup input typing/caret keys stay pointer mode; Tab restores wrapper ring; settings remains navigable');

  for(const key of ['Enter','Space']){
   await pointerNeutral();await page.locator('#settings-open').focus();
   await page.keyboard.press(key);await settle();
   assert.equal(await page.locator('#settings-dialog').evaluate(el=>el.open),true,key+' activates focused settings button');
   await expectRing(page.locator('#settings-dialog :focus'),true,key+' activation exposes dialog focus');
   await page.locator('#settings-close').click();await settle();
  }
  checks.push('Enter and Space activate controls with visible focus from an initial pointer state');

  await pointerNeutral();
  const idleSearchBorder=(await style(searchWrap)).border;
  await search.click();await page.keyboard.type('월간');
  for(const key of ['Home','ArrowRight','End','ArrowLeft'])await page.keyboard.press(key);
  await expectNoFocusedRing('sidebar search typing must not produce outer ring');
  assert.notEqual((await style(searchWrap)).border,idleSearchBorder,'input focus border feedback remains visible');
  await page.keyboard.press('Control+A');await page.keyboard.press('Backspace');
  await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  assert.equal((await style(search)).focused,true);
  await expectRing(searchWrap,true,'sidebar search reached by keyboard');
  await expectRing(search,false,'sidebar search has exactly one wrapper ring');
  checks.push('pointer typing preserves subtle input border; keyboard search shows a single wrapper ring');

  const rowTitle=page.locator('#sessions .session-title-drag').first();
  const rowWrap=page.locator('#sessions .session-row').first();
  await pointerNeutral();await rowTitle.focus();
  await expectRing(rowWrap,false,'programmatic session focus must not ring its ancestor');
  await expectRing(rowTitle,false,'programmatic session focus must not ring its button');
  await page.keyboard.press('Shift+Tab');await tabTo(rowTitle);
  await expectRing(rowWrap,true,'keyboard session focus rings the whole row');
  await expectRing(rowTitle,false,'keyboard row has no inner duplicate outline');
  await shot('keyboard-session-row');
  await rowTitle.click();await settle();
  await expectRing(rowWrap,false,'pointer session selection must remove ancestor focus ring');
  checks.push('session keyboard focus uses whole row; programmatic and pointer focus show no ancestor ring');

  await pointerNeutral();await page.mouse.move(700,95);await handle.focus();await settle();
  assert.equal((await divider()).focused,true);
  assert.equal(painted(await divider()),false,'programmatic resizer focus must not paint focus pseudo-element');
  await page.keyboard.press('ArrowLeft');await settle();
  assert.equal(painted(await divider()),true,'arrow navigation enables resizer indicator');
  assert.equal((await divider()).width,4,'keyboard resizer indicator remains thick');
  const afterArrow=Number(await handle.getAttribute('aria-valuenow'));
  await page.keyboard.press('Home');assert.equal(await handle.getAttribute('aria-valuenow'),'260');
  await page.keyboard.press('End');assert.equal(await handle.getAttribute('aria-valuenow'),'560');
  await page.keyboard.press('Enter');assert.equal(await handle.getAttribute('aria-valuenow'),'292');
  assert.equal(afterArrow,302,'focus handling preserves actual keyboard resizing');
  await pointerNeutral();await handle.focus();
  const handleBox=await handle.boundingBox();
  await page.mouse.move(handleBox.x+handleBox.width/2,handleBox.y+150);await settle();
  assert.equal(painted(await divider()),true,'pointer hover must still paint divider');
  assert.equal((await divider()).width,2,'pointer hover must not inherit keyboard thickness');
  await page.mouse.down();await page.mouse.move(handleBox.x-35,handleBox.y+150,{steps:6});await settle();
  assert.equal((await divider()).dragging,true);assert.equal(painted(await divider()),true,'pointer dragging remains visible');
  assert.equal((await divider()).focused,true,'pointer resize retains DOM focus');
  await page.mouse.up();await page.mouse.move(700,95);await settle();
  assert.equal(painted(await divider()),false,'release away from divider leaves no stray focus paint');
  checks.push('resizer keyboard arrows/Home/End/Enter work; programmatic focus stays quiet; hover and drag remain visible');

  for(const theme of ['light','dark']){
   await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
   await pointerNeutral();await home.focus();await expectRing(home,false,theme+' pointer programmatic home');
   await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');await expectRing(home,true,theme+' keyboard home');
   await shot('keyboard-home-'+theme);
  }
  await page.emulateMedia({forcedColors:'active'});await settle();
  assert.equal(await page.evaluate(()=>matchMedia('(forced-colors:active)').matches),true);
  await pointerNeutral();await home.focus();await expectRing(home,false,'forced colors programmatic focus');
  await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');await expectRing(home,true,'forced colors keyboard focus');
  await shot('keyboard-forced-colors');
  await pointerNeutral();await handle.focus();assert.equal(painted(await divider()),false,'forced colors programmatic resizer');
  await page.keyboard.press('ArrowLeft');assert.equal(painted(await divider()),true,'forced colors keyboard resizer');
  checks.push('light, dark and forced colors preserve pointer suppression and keyboard visibility');
  assert.deepEqual(errors,[]);
  const result={passed:true,checks,pageErrors:errors};
  if(output)fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));await context.close();
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
