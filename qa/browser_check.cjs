const {chromium}=require('playwright');
const fs=require('fs');const path=require('path');
const QA=__dirname;
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||undefined});const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});const page=await context.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 await page.addInitScript(()=>localStorage.setItem('meigen-auto-refresh','false'));
 await page.goto('http://127.0.0.1:8876',{waitUntil:'networkidle'});await page.waitForSelector('.kpi-value');
 const screenshots=[];
 for(const view of ['overview','journey','demand','diagnosis','retention','commercial','opportunities','traffic']){
  await page.locator(`#nav a[href="#${view}"]`).click();await page.waitForTimeout(150);
  const file=path.join(QA,`${view}-desktop.png`);await page.screenshot({path:file,fullPage:true});screenshots.push(file);
 }
 await page.setViewportSize({width:390,height:844});await page.goto('http://127.0.0.1:8876/#journey',{waitUntil:'networkidle'});await page.waitForSelector('.flow-node');await page.screenshot({path:path.join(QA,'journey-mobile.png'),fullPage:true});
 console.log(JSON.stringify({errors,title:await page.title(),screenshots,bodyWidth:await page.evaluate(()=>document.body.scrollWidth)}));
 fs.writeFileSync(path.join(QA,'initial-browser.json'),JSON.stringify({errors,screenshots},null,2));await browser.close();
})();
