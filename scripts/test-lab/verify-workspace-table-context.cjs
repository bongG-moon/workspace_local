/* Run with the output folder of serve-workspace-modes-fixture.py.
 * Exercises production preview routes in an isolated local workspace. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || path.join(os.homedir(),
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const fixture = path.resolve(process.argv[2]);
const runtime = JSON.parse(fs.readFileSync(path.join(fixture,'runtime.json'),'utf8'));
assert.equal(runtime.original,path.join(fixture,'original.txt'));
assert.ok(['127.0.0.1','localhost'].includes(new URL(runtime.url).hostname));

(async () => {
  const service = await chromium.launchServer({headless:true, executablePath:process.env.WORKSPACE_BROWSER ||
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const browser = await chromium.connect(service.wsEndpoint());
  try {
    const page = await browser.newPage({viewport:{width:1480,height:880}}), errors = [];
    page.on('pageerror',e => errors.push(e.message));
    await page.goto(runtime.url);
    await page.waitForFunction(() => globalThis.WorkspaceStartupHealth?.snapshot().missing.length === 0);
    assert.equal(await page.evaluate(() => boot.demo),true);
    const work = fs.mkdtempSync(path.join(fixture,'table-context-'));
    const csv = path.join(work,'metrics.csv'), html = path.join(work,'report.html');
    const data = ['항목,생산량,비용',...Array.from({length:80},(_,i) => `행-${i+1},${i+10},${500-i}`)];
    fs.writeFileSync(csv,data.join('\n')); fs.writeFileSync(html,'<h1>생산 보고서</h1>');
    const ids = [];
    for (const title of ['검토 A','검토 B']) {
      ids.push((await page.evaluate(({title,work}) => api('/api/create',{trusted:true,workspace:work,title}),{title,work})).id);
    }
    await page.reload(); await page.locator('#sessions .session').first().waitFor();
    const open = async (file=csv,view) => {
      if (view) await page.evaluate(({file,view}) => preview(file,{view}),{file,view});
      else {
        await page.locator('#files-tab').click();
        await page.locator('#files').getByText(path.basename(file),{exact:true}).click();
      }
      await page.locator(file===html ? '#explorer-content iframe' : '.explorer-view-tabs').waitFor();
    };
    const choose = async id => {
      await page.locator(`#sessions [data-session-id="${id}"] .session`).click();
      await page.waitForFunction(id => active?.id === id && !pendingSelection,id);
    };
    const back = () => page.locator('#explorer-back').click();
    const table = () => page.getByRole('button',{name:'원본 표',exact:true}).click();
    const graph = () => page.getByRole('button',{name:'그래프',exact:true}).click();
    const sorted = page.locator('.explorer-data-pane [aria-sort="descending"]');
    const checked = page.locator('td[aria-selected="true"]');
    const cell = row => page.locator(`td[data-source-row="${row}"][data-column="1"]`);
    const metric = page.locator('.explorer-metric select');
    await choose(ids[0]); await open();
    assert.equal(await metric.count(),0);
    assert.equal(await page.locator('.explorer-range-tools').isVisible(),false);
    await graph();
    await metric.selectOption('1'); await page.locator('.explorer-chart-pages').getByRole('button',{name:'다음'}).click();
    await back(); await open();
    assert.equal(await metric.count(),0);
    await graph();
    assert.equal(await metric.inputValue(),'1');
    await table();
    const sort = page.locator('.explorer-sort').nth(1);
    await sort.click(); await sort.click(); await cell(30).click();
    const scroller = page.locator('.rich-table-scroll');
    const scroll = await scroller.evaluate(e => {e.scrollTop=117;return e.scrollTop;});
    assert.ok(scroll>0);
    await back(); await open(html); await back(); await open();
    assert.equal(await checked.getAttribute('data-source-row'),'30'); assert.match(await sorted.innerText(),/생산량/);
    assert.equal(await scroller.evaluate(e=>e.scrollTop),scroll);
    await back(); await open(csv,'chart');
    assert.equal(await metric.inputValue(),'1'); assert.match(await page.locator('.explorer-bars').innerText(),/행-13/);
    await table(); assert.equal(await checked.getAttribute('data-source-row'),'30'); await back();
    await choose(ids[1]); await open();
    assert.equal(await metric.count(),0); await graph();
    assert.equal(await metric.inputValue(),'0'); await table();
    assert.equal(await sorted.count(),0); assert.equal(await checked.count(),0);
    await cell(4).click();
    await choose(ids[0]); await open();
    assert.equal(await checked.getAttribute('data-source-row'),'30'); assert.match(await sorted.innerText(),/생산량/);
    await page.screenshot({path:path.join(work,'restored.png'),animations:'disabled'});
    // Replacement choices must be explicit and must not overwrite the reader's
    // ordinary navigation state, even for the identical session, path and bytes.
    await page.evaluate(file => preview(file,{view:'table',referenceReview:{old:'fixture old quote'}}),csv);
    assert.equal(await checked.count(),0); assert.equal(await sorted.count(),0);
    await cell(9).click();
    await back(); await open();
    assert.equal(await checked.getAttribute('data-source-row'),'30'); assert.match(await sorted.innerText(),/생산량/);
    await back(); fs.appendFileSync(csv,'\n추가된 행,999,10');
    await open(csv,'table'); assert.equal(await checked.count(),0); assert.equal(await sorted.count(),0);
    await back(); await open(csv,'chart');
    assert.equal(await metric.inputValue(),'0'); assert.match(await page.locator('.explorer-bars').innerText(),/행-1\n/);
    await choose(ids[1]); await open(csv,'table');
    assert.equal(await checked.count(),0);
    await cell(3).click();
    // Identical bytes at different paths are independent. The least recently
    // closed view drops out after the 24-entry navigation budget.
    for (let i=0;i<24;i++) {
      const copy=path.join(work,`copy-${i}.csv`);
      fs.copyFileSync(csv,copy); await open(copy,'table');
      assert.equal(await checked.count(),0);
    }
    await back(); await open(csv,'table');
    assert.equal(await checked.count(),0); assert.deepEqual(errors,[]);
    await back(); await page.setViewportSize({width:375,height:800}); await open(csv,'table');
    const rangeAction=page.locator('.explorer-range-action');
    assert.equal(await rangeAction.isVisible(),false);
    await cell(70).click(); await cell(70).press('Shift+ArrowRight');
    for(let i=0;i<3;i++) await page.keyboard.press('Shift+ArrowDown');
    assert.equal(await checked.count(),8);
    await scroller.evaluate(e=>{e.scrollTop=e.scrollHeight;});
    await page.locator('#preview-content').evaluate(e=>{e.scrollTop=e.scrollHeight;});
    const actionBounds=await rangeAction.boundingBox();
    assert.ok(actionBounds&&actionBounds.x>=0&&actionBounds.x+actionBounds.width<=375&&
      actionBounds.y>=0&&actionBounds.y+actionBounds.height<=800);
    assert.equal(await rangeAction.evaluate(e=>{
      const b=e.getBoundingClientRect();return e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));
    }),true);
    await page.screenshot({path:path.join(work,'narrow-table.png'),animations:'disabled'});
    await rangeAction.click();
    const chip=page.locator('#table-citation-chips .table-citation-chip').last();
    await chip.locator('summary').click();
    const chipBounds=await chip.boundingBox();
    assert.ok(chipBounds&&chipBounds.x>=0&&chipBounds.x+chipBounds.width<=375&&chipBounds.y>=0&&chipBounds.y+chipBounds.height<=800);
    assert.ok(await chip.locator('.citation-values').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(work,'narrow-expanded-citation.png'),animations:'disabled'});
    const evidence={passed:true,scope:'Real loopback HTTP, file bytes, two tasks, headless Edge, no model',restoredScrollTop:scroll,
      narrow:{viewport:[375,800],actionBounds,chipBounds},
      checks:['restore sort, original row, scroll after HTML','explicit chart restores metric and page',
        'independent selections for two tasks sharing the same path','replacement review starts empty and preserves prior context',
        'new file version resets each task','identical copies are independent and the 24-entry budget evicts old state',
        'sticky action reached after scrolling and expanded citation fits at 375px'],pageErrors:errors};
    fs.writeFileSync(path.join(work,'checks.json'),JSON.stringify(evidence,null,2));
    console.log(JSON.stringify({work,...evidence}));
  } finally { await browser.close(); await service.close(); }
})().catch(e => {console.error(e.stack);process.exitCode=1;});
