const puppeteer = require('puppeteer');
const URL = process.env.DASH_URL || 'http://127.0.0.1:8899/';
(async ()=>{
  const browser = await puppeteer.launch({headless: true, args:['--no-sandbox','--disable-setuid-sandbox']});
  const page = await browser.newPage();
  page.setDefaultTimeout(30000);
  try{
    await page.goto(URL, {waitUntil: 'networkidle2'});
    await page.waitForSelector('#chart', {timeout: 20000});
    // wait until chart initialization flag or small delay
    await page.waitForFunction(()=> window._chartReady === true, {timeout:20000}).catch(()=>{});

    // Toggle followPrice and autoZoom
    await page.evaluate(()=>{
      const f = document.getElementById('followPrice');
      const a = document.getElementById('autoZoom');
      if (f){ f.checked = true; try{ f.dispatchEvent(new Event('change')); }catch(_){} }
      if (a){ a.checked = true; try{ a.dispatchEvent(new Event('change')); }catch(_){} }
      try{ if (typeof updateChart === 'function') updateChart(); }catch(e){}
    });

  // Wait a bit for Plotly to update
  await new Promise(r=>setTimeout(r, 1200));

    // Evaluate tick presence
    const result = await page.evaluate(()=>{
      function getTickVals(){
        const gd = document.getElementById('chart');
        if (!gd) return {tickvals: null, ticktext: null, annotations: null};
        const tv = (gd.layout && gd.layout.yaxis && gd.layout.yaxis.tickvals) || (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.tickvals) || null;
        const tt = (gd.layout && gd.layout.yaxis && gd.layout.yaxis.ticktext) || (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.ticktext) || null;
        const ann = (gd.layout && gd.layout.annotations) || (gd._fullLayout && gd._fullLayout.annotations) || null;
        return {tickvals: tv, ticktext: tt, annotations: ann};
      }
      const tv = getTickVals();
      const gridMin = (typeof window.GRID_MIN !== 'undefined') ? Number(window.GRID_MIN) : null;
      const formatted = (v)=>{ try{ return Number(v).toFixed(6); }catch(_){ return String(v); } };
      let found = false;
      let foundByText = false;
      let foundBadge = false;
      let foundAnnotation = false;
      let badgeText = null;
      // Check tick arrays as before
      if (tv.tickvals && gridMin != null){
        found = tv.tickvals.some(v => Math.abs(Number(v) - gridMin) <= (Math.abs(gridMin)*1e-9 + 1e-12));
      }
      if (tv.ticktext && gridMin != null){
        const s = formatted(gridMin);
        foundByText = tv.ticktext.some(t => (''+t).includes(s));
      }
      // Check for DOM badge inserted by the dashboard
      try{
        const badge = document.getElementById('compactBoundaryBadge');
        if (badge && badge.style && badge.style.display !== 'none'){
          const txt = (badge.textContent||'').trim();
          if (txt.length) { foundBadge = true; badgeText = txt; }
        }
      }catch(_){ }
      // Check Plotly annotations for presence of gridMin text
      try{
        const ann = tv.annotations || null;
        if (ann && Array.isArray(ann) && gridMin != null){
          const s = formatted(gridMin);
          foundAnnotation = ann.some(a => (a && a.text && (''+a.text).includes(s)));
        }
      }catch(_){ }
      return {
        found, foundByText, foundBadge, foundAnnotation, badgeText,
        tickvals: tv.tickvals ? tv.tickvals.slice(0,200) : null,
        ticktext: tv.ticktext ? tv.ticktext.slice(0,200) : null,
        annotations: tv.annotations ? tv.annotations.slice(0,50) : null,
        gridMin
      };
    });

    const screenshotPath = '/tmp/dash_check_purple_tick.png';
    await page.screenshot({path: screenshotPath, fullPage: true});
    console.log(JSON.stringify({ok: (result.found||result.foundByText), result, screenshot: screenshotPath}, null, 2));
  }catch(e){
    console.error('ERROR', e && e.stack ? e.stack : e);
    await page.screenshot({path: '/tmp/dash_check_purple_tick_error.png', fullPage:true}).catch(()=>{});
    process.exitCode = 2;
  } finally{
    try{ await browser.close(); }catch(_){ }
  }
})();
