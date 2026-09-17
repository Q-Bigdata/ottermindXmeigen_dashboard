const {chromium}=require('playwright');const fs=require('fs');const path=require('path');const assert=require('assert/strict');
(async()=>{
 const {createModel,formatNumber}=await import('../report/data.js');const report=JSON.parse(fs.readFileSync(path.join(__dirname,'../data/report.json')));const model=createModel(report);
 const baseline=model.summary({});assert.equal(baseline.visits,report.data.quality.visits);
 for(const a of model.axis({})){const ref=report.data.branches.axis.find(x=>x.node===a.node);assert.equal(a.arrived,ref.arrived);assert.equal(a.denominator,ref.denominator);assert.equal(a.continued,ref.continued);assert.equal(a.exit,ref.no_later_valid_action);}
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||undefined});const ctx=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});await ctx.addInitScript(()=>localStorage.setItem('meigen-auto-refresh','false'));
 const page=await ctx.newPage(),errors=[],checks=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 const goto=async view=>{await page.goto('http://127.0.0.1:8876/#'+view,{waitUntil:'networkidle'});await page.waitForSelector('#content .card');};
 await goto('overview');await page.waitForSelector('.kpi-value');assert.equal(await page.locator('.kpi-value').first().innerText(),formatNumber(baseline.visits));checks.push('real_api_kpi');
 await page.selectOption('#demandSelect','blur');assert.equal(await page.locator('.kpi-value').first().innerText(),formatNumber(model.summary({demand:'blur'}).visits));checks.push('demand_filter');
 await page.click('#filterToggle');await page.selectOption('#deviceSelect','mobile');await page.selectOption('#browserSelect','ios');assert.equal(await page.locator('.kpi-value').first().innerText(),formatNumber(model.summary({demand:'blur',device:'mobile',browser:'ios'}).visits));checks.push('combined_environment_filter');
 await page.selectOption('#maturitySelect','paid');assert.match(await page.locator('#content').innerText(),/当前筛选没有访问记录/);await page.locator('#content [data-action="reset"]').click();checks.push('empty_and_reset');
 await page.locator('#nav a[href="#demand"]').click();const cell=page.locator('[data-cell-demand="blur"][data-cell-stage="registered_used"]');await cell.click();assert.match(await page.locator('#pageTitle').innerText(),/用户使用流程/);const ax=model.axis({demand:'blur',maturity:'registered_used'}).find(x=>x.node==='use');assert.equal(await page.locator('.flow-node[data-node="use"] .node-value').innerText(),formatNumber(ax.arrived));checks.push('matrix_to_journey_cohort');
 await page.click('[data-node="register"]');assert.match(await page.locator('.node-detail-head h3').innerText(),/注册/);checks.push('click_node');
 await page.click('#resetFilters');await page.locator('#nav a[href="#overview"]').click();await page.locator('[data-date="2026-09-14"]').click();assert.match(await page.locator('.trend-selected').innerText(),/2026-09-14/);await page.locator('[data-filter-date="2026-09-14"]').click();assert.equal(await page.locator('.flow-node[data-node="entry"] .node-value').innerText(),formatNumber(model.axis({start:'2026-09-14',end:'2026-09-14'})[0].arrived));checks.push('chart_date_to_journey');
 await page.click('#resetFilters');await page.click('[data-node="payment"]');assert.match(await page.locator('.node-detail').innerText(),/尚无可关联的支付记录/);checks.push('payment_missing_state');
 await page.locator('.node-detail [data-action="methods"]').click();assert.equal(await page.locator('#methodDialog').evaluate(d=>d.open),true);await page.click('#closeDialog');checks.push('metric_dialog');
 await page.locator('#nav a[href="#opportunities"]').click();await page.locator('.action-card summary').first().click();assert.match(await page.locator('.action-card[open]').innerText(),/怎么验证/);checks.push('experiment_disclosure');
 await page.locator('#autoRefresh').check();assert.equal(await page.evaluate(()=>localStorage.getItem('meigen-auto-refresh')),'true');await page.locator('#autoRefresh').uncheck();checks.push('auto_refresh_setting');
 const overflow=[];
 for(const width of [1440,1100,768,390]){
  await page.setViewportSize({width,height:900});
  for(const view of ['overview','demand','journey','diagnosis','retention','commercial','opportunities','traffic']){
   await goto(view);const sw=await page.evaluate(()=>document.documentElement.scrollWidth);if(sw>width+1)overflow.push({width,view,scrollWidth:sw});
   if(view==='journey')await page.locator('.flow-node[data-node="use"]').click();
   if(width===390&&['overview','journey','demand','diagnosis'].includes(view))await page.screenshot({path:path.join(__dirname,view+'-mobile.png'),fullPage:true,animations:'disabled'});
  }
 }
 assert.deepEqual(overflow,[]);checks.push('responsive_no_page_overflow_32_views');
 await page.click('#navOpen');assert.equal(await page.locator('#sidebar').evaluate(e=>e.classList.contains('open')),true);await page.locator('#nav a[href="#journey"]').click();assert.equal(await page.locator('#sidebar').evaluate(e=>e.classList.contains('open')),false);checks.push('mobile_navigation');
 // Isolated UI state simulation: never writes mock data into the backend.
 const statusPage=await ctx.newPage();let phase='idle',refreshRequests=0;
 const status=()=>({refresh:{state:phase,error:phase==='failed'?{code:'test_failure'}:null},report:{available:true,generation_id:report.generation_id,window:report.window},serving_previous_report:phase==='failed'});
 await statusPage.route('**/api/status',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(status())}));
 await statusPage.route('**/api/refresh',route=>{refreshRequests++;phase='running';return route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({status:status()})});});
 await statusPage.goto('http://127.0.0.1:8876',{waitUntil:'networkidle'});await statusPage.waitForSelector('.kpi-value');await statusPage.click('#refreshButton');assert.equal(refreshRequests,1);assert.equal(await statusPage.locator('#refreshButton').isDisabled(),true);await statusPage.locator('#statusBanner').waitFor({state:'visible'});assert.match(await statusPage.locator('#statusBanner').innerText(),/上一批结果/);checks.push('refresh_loading_preserves_values');
 phase='failed';await statusPage.waitForTimeout(4500);assert.match(await statusPage.locator('#statusBanner').innerText(),/本次更新未完成/);assert.equal(await statusPage.locator('.kpi-value').first().innerText(),formatNumber(report.data.quality.visits));checks.push('refresh_failure_retains_values');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(__dirname,'interaction-results.json'),JSON.stringify({passed:true,checks,consoleErrors:errors,overflow,baselineGeneration:report.generation_id},null,2));console.log(JSON.stringify({passed:true,checks,errors,overflow}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
