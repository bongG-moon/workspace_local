const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const cfg=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));const browser=await chromium.launch({channel:'msedge',headless:true});try{
const page=await browser.newPage({viewport:{width:1280,height:800}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(cfg.url);await page.waitForFunction(()=>globalThis.WorkspaceStartupHealth?.snapshot().missing.length===0);await page.evaluate(id=>selectSession(id),cfg.id);
await page.waitForFunction(()=>document.querySelectorAll('.inline-queue-row').length===3);await page.locator('#prompt').fill('KEEP DRAFT');
await page.locator('.inline-queue-delete').nth(1).click();await page.waitForFunction(()=>document.querySelectorAll('.inline-queue-row').length===2);
assert.deepEqual(await page.locator('.inline-queue-text').allTextContents(),['first queued','apply this']);
await page.locator('.inline-queue-handle').nth(1).dragTo(page.locator('.inline-queue-row').first());await page.waitForFunction(()=>document.querySelector('.inline-queue-text').textContent==='apply this');
await page.locator('.inline-queue-apply').first().click();await page.waitForFunction(()=>document.querySelectorAll('.inline-queue-row').length===1&&document.querySelector('.inline-queue-text').textContent==='first queued');
assert.equal(await page.locator('#prompt').inputValue(),'KEEP DRAFT');assert.deepEqual(errors,[]);
console.log(JSON.stringify({realHttp:true,cancel:true,reorder:true,applyNow:true,draft:true,errors}));
await page.evaluate(()=>{appClosed=true;pollController?.abort();});}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
