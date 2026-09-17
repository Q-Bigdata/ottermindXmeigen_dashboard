const {chromium}=require('playwright');
const assert=require('assert/strict');
const base=process.env.DASHBOARD_URL||'http://127.0.0.1:8876';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||undefined});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],checks=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/#opportunities',{waitUntil:'networkidle'});
 await page.waitForSelector('[data-strategy="value-payment"]');
 assert.equal(await page.locator('[data-strategy]').count(),9);
 const report=await(await page.request.get(base+'/api/report')).json();
 const content=await page.locator('#content').textContent();
 for(const text of ['未再主动操作','地区','语言','订阅创建','试用后首次收费','历史证据单独标明截止时间'])assert(content.includes(text),text);
 for(const text of ['undefined','NaN','rows.join'])assert(!content.includes(text));
 for(const id of ['authentication','stop-states','image-value','video-value','return-recovery','second-value','result-registration','growth','value-payment']){
  const details=page.locator(`[data-strategy="${id}"]`);await details.locator('summary').click();
  const body=await details.innerText();for(const text of ['目标人群：','数据依据：','证据范围：','具体改动：','验证实验：','主指标：','护栏：','解释边界：'])assert(body.includes(text),id+' '+text);
 }
 checks.push('nine_actions_with_actual_evidence_experiments_and_guardrails');
 assert((await page.locator('[data-strategy="stop-states"]').innerText()).includes(report.data.deep_comparison.immediate.stopped.toLocaleString('en-US')));
 assert((await page.locator('[data-strategy="growth"]').innerText()).includes('919→1117'));
 checks.push('separate_current_and_reviewed_snapshot_evidence');
 const demands=await page.locator('#demandSelect option').evaluateAll(options=>options.map(o=>o.value));
 for(const demand of demands){
  await page.selectOption('#demandSelect',demand);
  assert(!/undefined|NaN/.test(await page.locator('#content').innerText()),demand);
  if(demand==='blur')assert.equal(await page.locator('[data-strategy="video-value"]').count(),0);
  if(demand==='product_video')assert.equal(await page.locator('[data-strategy="image-value"]').count(),0);
 }
 const noSample=await page.evaluate(async()=>{const {renderStrategy}=await import('/strategy.js');const r=await(await fetch('/api/report')).json();return renderStrategy(r,{demand:'no_such_demand'});});
 assert(noSample.includes('当前需求暂无合格首次使用对照样本'));assert(!noSample.includes('首次使用后继续主动操作'));assert(!noSample.includes('2,696'));
 checks.push('no_missing_demand_fallback_to_all');
 const updated=await page.evaluate(async()=>{const {renderStrategy}=await import('/strategy.js');const r=await(await fetch('/api/report')).json();r.data.deep_comparison.immediate.continued=1234;r.data.deep_comparison.immediate.stopped=4321;r.data.deep_comparison.meta.cutoff='2026-09-17T01:02:03+00:00';return renderStrategy(r,{});});
 assert(updated.includes('1,234'));assert(updated.includes('4,321'));assert(updated.includes('2026/9/17 09:02:03'));
 checks.push('new_generation_updates_numbers_and_evidence_timestamp');
 await page.selectOption('#demandSelect','all');await page.click('#filterToggle');await page.selectOption('#countrySelect','IN');
 assert((await page.locator('#content').innerText()).includes('当前日期、设备、地区、语言和行为筛选不重算本模块'));
 await page.click('#resetFilters');checks.push('partial_filter_scope_explicit');
 for(const width of [1440,768,390]){
  await page.setViewportSize({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth)<=width+1,'document overflow at '+width);
  await page.locator('[data-strategy="stop-states"] summary').click();assert(await page.evaluate(()=>document.documentElement.scrollWidth)<=width+1,'open details overflow at '+width);
  await page.screenshot({path:`/tmp/meigen-strategy-${width}.png`,fullPage:true});
 }
 assert.deepEqual(errors,[]);checks.push('chrome_desktop_tablet_mobile_no_overflow_or_runtime_errors');
 await browser.close();console.log(JSON.stringify({passed:true,checks,generation:report.generation_id,errors},null,2));
})().catch(error=>{console.error(error);process.exit(1);});
