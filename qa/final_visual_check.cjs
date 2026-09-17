const {chromium}=require('playwright');const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||undefined});const errors=[];
 for(const width of [1440,390]){
  const c=await browser.newContext({viewport:{width,height:960},reducedMotion:'reduce'});await c.addInitScript(()=>localStorage.setItem('meigen-auto-refresh','false'));const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
  for(const view of ['overview','journey','diagnosis','commercial','demand']){
   await p.goto('http://127.0.0.1:8876/#'+view,{waitUntil:'networkidle'});await p.waitForSelector('#content .card');
   if(view==='journey')await p.locator('.flow-node[data-node="use"]').click();
   assert((await p.evaluate(()=>document.documentElement.scrollWidth))<=width+1);
   await p.screenshot({path:path.join(__dirname,`${view}-${width===390?'mobile':'desktop'}-final.png`),fullPage:true,animations:'disabled'});
   if(view==='journey'){assert(await p.locator('.path-example').count());const scroll=await p.locator('.flow-axis').evaluate(e=>e.scrollLeft);if(width===390)assert(scroll>0);}
  }
  await c.close();
 }
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(__dirname,'final-visual-results.json'),JSON.stringify({passed:true,viewports:[1440,390],pages:['overview','journey','diagnosis','commercial','demand'],consoleErrors:errors,selectedNodeStaysVisible:true},null,2));console.log('Final visual and representative-path checks passed.');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
