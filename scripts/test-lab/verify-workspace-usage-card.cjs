// Run on the shipped sidebar. All usage amounts, failures and team names are local fixtures.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const {fixture}=require('./verify-workspace-sidebar-layout.cjs');
const outArg=process.argv.indexOf('--output');
const output=outArg<0?null:path.resolve(process.argv[outArg+1]);
const sample=percent=>({team:'fixture',teams:[{id:'fixture',name:'예시 팀'}],percent,spend:percent,max:100,remaining:Math.max(0,100-percent),
  resetAt:'2026-12-01T00:00:00Z',updatedAt:Date.parse('2026-11-07T03:30:00Z')/1000,demo:true});
const cached=sample(42),error503={error:'http_error',diagnostic:{stage:'usage',httpStatus:503},usage:cached};
const states=[
  {id:'normal',body:{usage:cached},level:'normal',percent:42},
  {id:'setup',body:{error:'not_configured',usage:null},level:'unavailable',compact:/조회 불가[\s\S]*연결 설정 필요/,detail:/설정/},
  {id:'503',body:error503,level:'unavailable',compact:/조회 불가[\s\S]*서버 응답 없음/,detail:/503/},
  {id:'retry',body:{...error503,loading:true,retry:{attempt:2,limit:3}},level:'unavailable',compact:/재조회 2\/3/,detail:/2\/3/},
  {id:'retry-no-error-code',body:{loading:true,retry:{attempt:3,limit:3},usage:cached},level:'unavailable',compact:/재조회 3\/3/,detail:/3\/3/},
  {id:'loading',body:{usage:null,loading:true},compact:/조회 중/,detail:/확인 중|조회 중/},
  {id:'refreshing',body:{usage:cached,loading:true},compact:/조회 중/,detail:/확인 중|조회 중|마지막 확인/},
  {id:'uncapped',body:{usage:{...cached,percent:null,max:0,remaining:0}},level:'normal'},
  ...[[80,'warning','주의','80% 이상'],[95,'danger','한도 임박','95% 이상'],[100,'danger','한도 도달','도달'],[105,'danger','한도 초과','초과']].map(
    ([percent,level,label,note])=>({id:'budget-'+percent,body:{usage:sample(percent)},level,percent,compact:new RegExp(label),detail:new RegExp(note)}))
];
async function geometry(page){
  return page.evaluate(()=>{
    const card=document.querySelector('#llm-usage-toggle'),head=card.querySelector('.llm-usage-head');
    const label='LLM 토큰 사용량',walker=document.createTreeWalker(head,NodeFilter.SHOW_TEXT);
    let text;while((text=walker.nextNode())&&!text.textContent.includes(label)){}
    if(!text)throw Error('Card title is missing');
    const start=text.textContent.indexOf(label),range=document.createRange();range.setStart(text,start);range.setEnd(text,start+label.length);
    const r=card.getBoundingClientRect(),t=range.getBoundingClientRect(),list=document.querySelector('#sessions').getBoundingClientRect();
    return {x:r.x,y:r.y,width:r.width,height:r.height,titleX:t.x,titleY:t.y,listHeight:list.height,
      level:document.querySelector('#llm-usage').dataset.level,text:card.innerText,progress:document.querySelector('#llm-usage-track').getAttribute('aria-valuenow')};
  });
}
async function settle(page){await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));}
async function run(){
  const zoom=process.argv.includes('--zoom-equivalent');
  const viewport=zoom?{width:640,height:360}:{width:1280,height:720};
  const browser=await chromium.launch({channel:'msedge',headless:true}),report={fixture:'Synthetic budget and errors; no personal configuration or live service.',viewport,
    layout:zoom?'Reflow equivalent to a 1280 by 720 display at 200 percent browser zoom; native host zoom is not invoked.':'Normal desktop viewport.',samples:[]};
  try{
    const f=await fixture(browser),page=f.page;
    try{
      await page.setViewportSize(viewport);
      await page.evaluate(()=>document.fonts.ready);
      let reply={usage:cached};
      await page.route('**/api/llm-usage',route=>route.fulfill({json:reply}));
      for(const theme of ['light','dark']){
        if(zoom&&await page.evaluate(()=>WorkspaceLayout.snapshot().sidebarOverlay))await page.locator('#sidebar-toggle').click();
        if(await page.evaluate(()=>document.documentElement.dataset.theme)!==theme){
          await page.locator('#appearance-toggle').click();await page.waitForFunction(value=>document.documentElement.dataset.theme===value,theme);
        }
        if(zoom&&await page.evaluate(()=>WorkspaceLayout.snapshot().sidebarCollapsed))await page.locator('#sidebar-toggle').click();
        let baseline;
        for(const state of zoom?states.filter(s=>['normal','setup','retry','budget-105'].includes(s.id)):states){
          reply=state.body;
          await Promise.all([page.waitForResponse(response=>new URL(response.url()).pathname==='/api/llm-usage'),
            page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))]);
          await settle(page);
          const value=await geometry(page);baseline??=value;
          const tag=theme+'-'+state.id;
          if(output)await page.locator('#llm-usage-toggle').screenshot({path:path.join(output,tag+'-card.png'),animations:'disabled',scale:'device'});
          for(const key of ['x','y','width','height','titleX','titleY','listHeight'])
            assert.ok(Math.abs(value[key]-baseline[key])<=0.5,`${tag} ${key}: normal ${baseline[key]}, actual ${value[key]}`);
          if(state.level)assert.equal(value.level,state.level,tag);
          if(state.compact)assert.match(value.text,state.compact,tag);
          if(state.id==='normal'){assert.match(value.text,/예시/);assert.match(value.text,/42%/);assert.match(value.text,/\$42\.00/);assert.match(value.text,/\$58\.00/);}
          const progressBars=await page.locator('#llm-usage-toggle').getByRole('progressbar').count();
          if(state.percent!==undefined){assert.ok(value.text.includes(state.percent+'%'));assert.equal(value.progress,String(Math.min(100,state.percent)));assert.equal(progressBars,1);}
          const unavailable=state.level==='unavailable';
          if(unavailable||['loading','refreshing'].includes(state.id)){
            assert.doesNotMatch(value.text,/\$|\d+(?:\.\d+)?%|남음/,tag);
            assert.equal(value.progress,null,tag);
            assert.equal(progressBars,0,tag);
          }
          if(state.id==='uncapped'){assert.match(value.text,/한도 미설정/);assert.equal(progressBars,0);assert.equal(value.progress,null);}
          const toggle=page.locator('#llm-usage-toggle'),details=page.locator('#llm-usage-details');
          await toggle.focus();await page.keyboard.press('Enter');await details.waitFor();
          assert.equal(await toggle.getAttribute('aria-expanded'),'true');
          const info=await details.innerText();if(state.detail)assert.match(info,state.detail,tag);
          if(unavailable){
            assert.doesNotMatch(info,/\$42\.00|\$58\.00|42%/);
            assert.equal(await page.locator('#llm-usage-detail-amount').textContent(),'조회 불가');
            assert.equal(await page.locator('#llm-usage-detail-remaining').textContent(),'조회 불가');
          }
          if(state.id==='refreshing'&&info.includes('$42.00'))assert.match(info,/이전 조회 값/);
          if(output&&['normal','setup','503','retry'].includes(state.id))
            await details.screenshot({path:path.join(output,tag+'-details.png'),animations:'disabled',scale:'device'});
          await page.keyboard.press('Escape');assert.ok(await details.isHidden());assert.ok(await toggle.evaluate(node=>document.activeElement===node));
          await page.evaluate(()=>document.activeElement.blur());
          if(output&&['normal','setup','503'].includes(state.id))
            await page.locator('#sidebar-panel').screenshot({path:path.join(output,tag+'-sidebar.png'),animations:'disabled',scale:'css'});
          report.samples.push({theme,state:state.id,...value,visibleProgressBars:progressBars,detail:info});
        }
      }
      assert.deepEqual(f.errors,[]);report.pageErrors=f.errors;
    }finally{await f.finish();}
    if(output)fs.writeFileSync(path.join(output,'usage-states.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify({passed:report.samples.length,normal:report.samples[0],pageErrors:report.pageErrors}));
  }finally{await browser.close();}
}
if(output)fs.mkdirSync(output,{recursive:true});
run().catch(err=>{console.error(err);process.exitCode=1;});
