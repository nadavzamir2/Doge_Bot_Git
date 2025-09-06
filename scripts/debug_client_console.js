const puppeteer = require('puppeteer');
const URL = process.env.DASH_URL || 'http://127.0.0.1:8899/';
(async ()=>{
  const browser = await puppeteer.launch({headless: true, args:['--no-sandbox','--disable-setuid-sandbox']});
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  const logs = [];
  page.on('console', msg => {
    const text = msg.text();
    logs.push({type:'console', text});
    console.log('PAGE_CONSOLE:', text);
  });
  page.on('pageerror', err => {
    logs.push({type:'pageerror', text: err.stack || err.message});
    console.error('PAGE_ERROR:', err.stack || err.message);
  });
  page.on('requestfailed', req => {
    logs.push({type:'requestfailed', url: req.url(), reason: req.failure()?.errorText});
    console.warn('REQ_FAILED:', req.url(), req.failure && req.failure().errorText);
  });
  try{
    await page.goto(URL, {waitUntil: 'networkidle2'});
    // wait some seconds to collect SSE and init
    await new Promise(r=>setTimeout(r, 5000));
    // print summary
    console.log('---COLLECTED LOGS---');
    console.log(JSON.stringify(logs.slice(0,200), null, 2));
    await page.screenshot({path:'/tmp/dash_client_debug.png', fullPage:true}).catch(()=>{});
  }catch(e){
    console.error('SCRIPT_ERROR', e && e.stack ? e.stack : e);
  } finally{
    await browser.close();
  }
})();
