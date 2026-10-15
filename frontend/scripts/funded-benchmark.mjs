// Real worker + full funded TradingPage, fresh browser context, fixed seed.
// BENCH_SOURCE optionally points to a preserved simulation/ directory for A/B.
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, dirname, extname, basename } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import os from 'node:os';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
import autoprefixer from 'autoprefixer';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'), require=createRequire(import.meta.url);
const label=process.argv[2]??'funded-after', out=resolve(root,'../tmp/simulation-benchmark',label);
const source=process.env.BENCH_SOURCE??resolve(root,'src/pages/simulation');
await mkdir(out,{recursive:true});
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE?pathToFileURL(process.env.PLAYWRIGHT_MODULE).href:'playwright');
const fixturePath=resolve(out,'../funded-fixture.json');
let fixture;
try {fixture=await readFile(fixturePath,'utf8');}catch{
  const generator=resolve(out,'generate.mjs');
  await build({stdin:{contents:`import {createSimulation} from '${source.replaceAll('\\','/')}/funded/participants'; export const generate=()=>JSON.stringify(createSimulation(73521,288000),(_,v)=>v instanceof Set?{__set:[...v]}:v);`,resolveDir:root},outfile:generator,bundle:true,platform:'node',format:'esm'});
  console.log('Generating real funded history: seed 73521, 288000 steps.');fixture=(await import(pathToFileURL(generator))).generate();await writeFile(fixturePath,fixture);
}
const entry=`import {Profiler} from 'react'; import {createRoot} from 'react-dom/client'; import {TradingPage} from '${source.replaceAll('\\','/')}/funded/TradingPage'; import {getFundedRuntime} from '${source.replaceAll('\\','/')}/funded/runtime'; import './src/styles/globals.css'; import './src/styles/workspace.css'; import './src/styles/signal.css'; window.bench={runtime:getFundedRuntime(),renders:[]}; createRoot(document.getElementById('root')).render(<Profiler id="funded" onRender={(_,__,d)=>window.bench.renders.push(d)}><TradingPage/></Profiler>);`;
const options={bundle:true,format:'esm',jsx:'automatic',sourcemap:true,nodePaths:[resolve(root,'node_modules')],define:{'process.env.NODE_ENV':'"production"','import.meta.env.DEV':'false','import.meta.hot':'undefined'},loader:{'.woff2':'file'}};
await build({...options,stdin:{contents:entry,resolveDir:root,loader:'tsx'},outfile:resolve(out,'app.js'),alias:{'react-dom/client':require.resolve('react-dom/profiling')}});
const cssPath=resolve(out,'app.css');
const css=await postcss([tailwindcss({config:resolve(root,'tailwind.config.js')}),autoprefixer()]).process(await readFile(cssPath,'utf8'),{from:cssPath});
await writeFile(cssPath,css.css);
// Only the benchmark worker loads this fixture. Production still does genuine warmup.
let worker=await readFile(resolve(source,'funded/worker.ts'),'utf8');
// Synchronous fixture initialization leaves the worker's message handler installed
// before the queued initialize command is delivered (top-level await would race it).
worker=`const benchmarkFixture = JSON.parse(${JSON.stringify(fixture)},(_,v)=>v?.__set?new Set(v.__set):v); let benchmarkSpeed=1;\n`+worker;
worker=worker.replace('state = createSimulation(seed, 0);','state = structuredClone(benchmarkFixture);');
worker=worker.replace('delete state.accounts[PLAYER]; state.accountOrder = state.accountOrder.filter(id => id !== PLAYER);','');
worker=worker.replace('flags = { ...initialView(seed), generation };','flags = { ...initialView(seed), generation, warming: false, warmupProgress: 1, playing: false };');
// Production playback stays fixed. A test-only command multiplies elapsed time
// to stress the same ordered engine steps at 4x; it is never shipped in the app.
if(!worker.includes("c.type === 'speed'")) {
  worker=worker.replace('const c = event.data;',"const c = event.data; if (c.type === 'benchmark-speed') { benchmarkSpeed=c.speed; return; }");
  worker=worker.replaceAll('clock.accrue(now - lastWall)','clock.accrue((now - lastWall) * benchmarkSpeed)');
}
await build({...options,stdin:{contents:worker,resolveDir:resolve(source,'funded'),loader:'ts'},outfile:resolve(out,'worker.ts')});
await writeFile(resolve(out,'fixture.json'),fixture);
await writeFile(resolve(out,'index.html'),'<html><head><link rel="stylesheet" href="/app.css"></head><body style="margin:0"><div id="root"></div><script type="module" src="/app.js"></script></body></html>');
const server=createServer(async(req,res)=>{try{const name=req.url==='/'?'index.html':basename(req.url);res.setHeader('Content-Type',({'.js':'text/javascript','.ts':'text/javascript','.css':'text/css','.json':'application/json','.woff2':'font/woff2'})[extname(name)]??'text/html');res.end(await readFile(resolve(out,name)));}catch{res.writeHead(404).end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH});
const results={label,conditions:{cpu:os.cpus()[0].model,ramGiB:+(os.totalmem()/2**30).toFixed(1),os:os.release(),browser:browser.version(),viewport:'1440x1000 DPR1',headless:true,seed:73521,steps:288000,build:'React production profiling, real worker, fixture initialization only'},scenarios:[],checks:[]};
try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});await context.addInitScript(()=>localStorage.setItem('topsignal.get-funded.v3.seed','73521'));
  const page=await context.newPage();page.on('pageerror',e=>{results.checks.push({error:e.message});console.error(e.message);});
  page.on('console',m=>{if(m.type()==='error')console.error(m.text());});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  try { await page.waitForSelector('.simulation-chart',{timeout:30000}); }
  catch(error) { await page.screenshot({path:resolve(out,'failure.png'),fullPage:true}); console.error(await page.locator('body').innerText()); throw error; }
  const cdp=await context.newCDPSession(page);
  const play=async(speed)=>{await page.evaluate(speed=>{const r=window.bench.runtime; r.dispatch({type:r.getSnapshot().speed===undefined?'benchmark-speed':'speed',speed}); r.dispatch({type:'play',playing:speed>0});},speed);await page.waitForTimeout(250);};
  const measure=async(name,setup,gesture='hover')=>{
    // Each workload starts from the same executed state, without inherited debt
    // or a different market regime caused by a previous timed workload.
    await page.evaluate(()=>window.bench.runtime.dispatch({type:'initialize',seed:73521}));
    await page.waitForFunction(()=>window.bench.runtime.getSnapshot().step===288000 && !window.bench.runtime.getSnapshot().playing);
    await setup();await page.waitForTimeout(500);const box=await page.locator('.simulation-chart').boundingBox();
    await page.evaluate(()=>{const b=window.bench;b.renders=[];const m=b.m={start:performance.now(),step:b.runtime.getSnapshot().step,frames:[],inputs:[],tasks:[],writes:[],last:0,stop:false};m.observer=new PerformanceObserver(l=>m.tasks.push(...l.getEntries().map(e=>e.duration)));m.observer.observe({type:'longtask'});const input=e=>{const t=e.timeStamp;requestAnimationFrame(()=>{if(!m.stop)m.inputs.push(performance.now()-t);});};document.addEventListener('pointermove',input);const save=Storage.prototype.setItem;Storage.prototype.setItem=function(...a){const t=performance.now(),r=save.apply(this,a);m.writes.push(performance.now()-t);return r;};m.cleanup=()=>{m.observer.disconnect();document.removeEventListener('pointermove',input);Storage.prototype.setItem=save;};const frame=t=>{if(m.stop)return;if(m.last)m.frames.push(t-m.last);m.last=t;requestAnimationFrame(frame);};requestAnimationFrame(frame);});
    await cdp.send('Profiler.enable');await cdp.send('Profiler.start');
    if(gesture==='pan'){await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.down();}
    const start=Date.now();let n=0;
    while(Date.now()-start<6000){await page.mouse.move(box.x+30+(Math.sin(n*.09)+1)*.35*(box.width-120),box.y+60+(Math.cos(n*.06)+1)*.25*(box.height-120));if(gesture==='zoom'&&n%8===0)await page.mouse.wheel(0,n%16===0?25:-25);if(gesture==='adjust'&&n%60===0)await page.getByLabel('Contracts',{exact:true}).fill(n%120===0?'2':'1');await new Promise(r=>setTimeout(r,8));n++;}
    if(gesture==='pan')await page.mouse.up();
    const {profile}=await cdp.send('Profiler.stop');await writeFile(resolve(out,`${name}.cpuprofile`),JSON.stringify(profile));
    const summary=await page.evaluate(()=>{const b=window.bench,m=b.m;m.stop=true;m.cleanup();const p=(a,q)=>{const s=[...a].sort((x,y)=>x-y);return +(s[Math.min(s.length-1,Math.floor(s.length*q))]??0).toFixed(2);};const stats=a=>({count:a.length,p50:p(a,.5),p95:p(a,.95),p99:p(a,.99),max:p(a,1)});const seconds=(performance.now()-m.start)/1000;return {seconds:+seconds.toFixed(2),frames:stats(m.frames),inputToRaf:stats(m.inputs),reactCommit:stats(b.renders),longTasks:stats(m.tasks),storageWrites:stats(m.writes),steps:b.runtime.getSnapshot().step-m.step,stepsPerSecond:+((b.runtime.getSnapshot().step-m.step)/seconds).toFixed(2),backlog:b.runtime.getSnapshot().backlog,candles:b.runtime.getSnapshot().session.market.books.BX.candles['1m'].length,domNodes:document.querySelectorAll('*').length};});results.scenarios.push({name,...summary});console.log(JSON.stringify({name,...summary}));
  };
  if(!process.env.BENCH_SMOKE_ONLY) {
  await measure('paused-hover',()=>play(0));await measure('paused-pan',()=>play(0),'pan');await measure('paused-zoom',()=>play(0),'zoom');
  await measure('play-1x',()=>play(1));await measure('play-4x',()=>play(4));
  await measure('dense-indicators',async()=>{await page.locator('.simulation-chart-tool-menu summary').click();await page.getByLabel('SMA',{exact:true}).check();await page.getByLabel('Retained-tape VWAP',{exact:true}).check();await page.getByRole('button',{name:'Fit History',exact:true}).click();await page.getByRole('button',{name:'Close tools',exact:true}).click();await play(4);},'zoom');
  await measure('ticket-adjustments',()=>play(4),'adjust');
  await measure('activity-open',async()=>{await page.getByRole('button',{name:'Fills',exact:true}).click();await play(4);});
  if(await page.getByRole('button',{name:'Logarithmic Price Scale'}).count())await measure('log-auto-history',async()=>{await page.getByRole('button',{name:'Logarithmic Price Scale'}).click();await page.getByRole('button',{name:'Auto Price Scale',exact:true}).evaluate(b=>{if(b.getAttribute('aria-pressed')==='false')b.click();});await play(4);},'zoom');
  } else {
    await page.locator('.simulation-chart-tool-menu summary').click();
    await page.getByRole('button',{name:'Fit History',exact:true}).click();
    await page.getByRole('button',{name:'Close tools',exact:true}).click();
    await page.getByRole('button',{name:'Logarithmic Price Scale'}).click();
  }
  await play(0);await page.screenshot({path:resolve(out,'desktop.png')});
  for(const symbol of ['BT','BG','BX']){await page.locator('.gf-markets > button').filter({has:page.locator('b',{hasText:symbol})}).click();for(const tf of ['5m','15m','1h','1D','1m']){await page.getByRole('button',{name:tf,exact:true}).click();const valid=await page.locator('.simulation-chart').evaluate(e=>!e.outerHTML.match(/NaN|Infinity/));if(!valid)throw Error('Invalid geometry');}}
  results.checks.push('All three instruments and five timeframes: finite geometry');
  if(await page.getByRole('button',{name:'Expand chart',exact:true}).count()){await page.getByRole('button',{name:'Expand chart',exact:true}).click();await page.waitForTimeout(250);await page.screenshot({path:resolve(out,'expanded.png')});await page.keyboard.press('Escape');results.checks.push('Expanded view and Escape');}
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);await page.screenshot({path:resolve(out,'mobile.png'),fullPage:true});results.checks.push({mobileOverflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
  if(process.env.BENCH_SMOKE_ONLY) {
    const marker=await page.locator('.simulation-price-marker rect').boundingBox(), controls=await page.locator('.simulation-axis-controls').boundingBox();
    if(marker.y+marker.height>controls.y)throw Error('Price marker overlaps axis controls');
    results.checks.push('Mobile price marker clears Auto/Log controls');
  }
}finally{await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));await browser.close();server.close();}
