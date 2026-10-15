// Usage: node scripts/simulation-benchmark.mjs before|after
// PLAYWRIGHT_MODULE can point to an installed playwright package. No user profile,
// backend, credentials, or existing saved runs are read by this harness.
import { build } from 'esbuild';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import os from 'node:os';
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
import autoprefixer from 'autoprefixer';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const label = process.argv[2] ?? 'after';
const out = resolve(root, '../tmp/simulation-benchmark', label);
await mkdir(out, { recursive: true });
const require = createRequire(import.meta.url);
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : 'playwright');
const fixturePath = resolve(out, '../fixture.json');
let fixture;
try { fixture = await readFile(fixturePath, 'utf8'); } catch {
  const generated = await build({ stdin: { contents: `import {createSimulation} from './src/pages/simulation/simulationPreview'; import {encodeSimulationCheckpoint} from './src/pages/simulation/simulationCheckpoint'; export const fixture=()=>encodeSimulationCheckpoint({market:createSimulation(73521,undefined,288000),running:false,speed:1,generation:0,instrument:'BX',timeframe:'1m',fractionalSteps:0},0);`, resolveDir: root }, bundle: true, write: false, platform: 'node', format: 'esm' });
  const generator = resolve(out, 'fixture-generator.mjs');
  await writeFile(generator, generated.outputFiles[0].text);
  console.log('Generating 1,200 actual one-minute candles from 288,000 deterministic steps…');
  fixture = (await import(pathToFileURL(generator))).fixture();
  await writeFile(fixturePath, fixture);
}
const source=(process.env.BENCH_SOURCE??resolve(root,'src/pages/simulation')).replaceAll('\\','/');
const entry=(await readFile(resolve(root,'scripts/simulation-benchmark-entry.tsx'),'utf8')).replaceAll('../src/pages/simulation/',`${source}/`);
await build({ stdin:{contents:entry,loader:'tsx',resolveDir:resolve(root,'scripts')}, nodePaths:[resolve(root,'node_modules')], outfile: resolve(out, 'app.js'), bundle: true, sourcemap: true, jsx: 'automatic', define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env.DEV': 'false', 'import.meta.hot': 'undefined' }, alias: { 'react-dom/client': require.resolve('react-dom/profiling') }, loader: { '.woff2': 'file', '.woff': 'file' }, plugins:[{name:'shared-ui',setup(b){b.onResolve({filter:/^\.\.\/\.\.\/components\/ui\/Icon$/},()=>({path:resolve(root,'src/components/ui/Icon.tsx')}));}}] });
const cssPath=resolve(out,'app.css');
const css=await postcss([tailwindcss({config:resolve(root,'tailwind.config.js')}),autoprefixer()]).process(await readFile(cssPath,'utf8'),{from:cssPath});
await writeFile(cssPath,css.css);
const server = createServer(async (req, res) => {
  try {
    const name = req.url === '/' ? 'index.html' : req.url.slice(1);
    if (!['index.html','app.js','app.js.map','app.css'].includes(name)) {res.writeHead(404).end();return;}
    res.setHeader('Content-Type', name.endsWith('js') ? 'text/javascript' : name.endsWith('css') ? 'text/css' : 'text/html');
    res.end(await readFile(resolve(out, name)));
  } catch { res.writeHead(404).end(); }
});
await writeFile(resolve(out,'index.html'), '<!doctype html><html><head><link rel="stylesheet" href="/app.css"></head><body style="margin:0;padding:12px;background:#0d121a"><div id="root"></div><script src="/app.js"></script></body></html>');
await new Promise(r => server.listen(0, '127.0.0.1', r));
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH });
const results = { label, conditions: { cpu: os.cpus()[0].model, ramGiB: +(os.totalmem()/2**30).toFixed(1), os: `${os.platform()} ${os.release()}`, browser: browser.version(), viewport: '1440x1000 DPR 1', build: 'React production profiling bundle', seed:73521, fixtureSteps:288000, headless:true }, scenarios: [] };
try {
  const context = await browser.newContext({ viewport:{width:1440,height:1000}, deviceScaleFactor:1 });
  await context.addInitScript(raw => localStorage.setItem('topsignal.simulation.session.v2.owned', raw), fixture);
  const page = await context.newPage();
  page.on('pageerror', e => console.error('PAGE ERROR', e.message));
  const cdp = await context.newCDPSession(page);
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForSelector('.simulation-chart');
  const measure = async (name, setup, interaction = 'hover') => {
    await setup(); await page.waitForTimeout(500);
    const box = await page.locator('.simulation-chart').boundingBox();
    await page.evaluate(() => {
      const b=window.bench;b.renders=[];
      b.measure={start:performance.now(),step:b.runtime.getSnapshot().session.market.step,frames:[],inputs:[],longTasks:[],writes:[],last:0,stop:false};
      const m=b.measure;
      m.observer=new PerformanceObserver(list=>m.longTasks.push(...list.getEntries().map(e=>e.duration)));m.observer.observe({type:'longtask'});
      const original=Storage.prototype.setItem;
      m.restore=()=>Storage.prototype.setItem=original;
      Storage.prototype.setItem=function(...args){const t=performance.now();const r=original.apply(this,args);m.writes.push(performance.now()-t);return r;};
      const input=e=>{const t=e.timeStamp;requestAnimationFrame(()=>{if(!m.stop)m.inputs.push(performance.now()-t);});};
      document.addEventListener('pointermove',input);m.cleanup=()=>document.removeEventListener('pointermove',input);
      const frame=t=>{if(m.stop)return;if(m.last)m.frames.push(t-m.last);m.last=t;requestAnimationFrame(frame);};requestAnimationFrame(frame);
    });
    await cdp.send('Profiler.enable');await cdp.send('Profiler.start');
    const started=Date.now();let n=0;
    if(interaction==='pan')await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);
    if(interaction==='pan')await page.mouse.down();
    while(Date.now()-started<6000){
      const x=box.x+40+(Math.sin(n*.09)+1)*.35*(box.width-120),y=box.y+80+(Math.cos(n*.06)+1)*.25*(box.height-150);
      await page.mouse.move(x,y);
      if(interaction==='zoom'&&n%8===0)await page.mouse.wheel(0,n%16===0?25:-25);
      if(interaction==='adjust'&&n%70===0){await page.getByRole('spinbutton',{name:'Maker liquidity (×)'}).fill(n%140===0?'1.2':'1');await page.getByRole('button',{name:'Apply changes',exact:true}).click();}
      await new Promise(r=>setTimeout(r,8));n++;
    }
    if(interaction==='pan')await page.mouse.up();
    const {profile}=await cdp.send('Profiler.stop');await writeFile(resolve(out,`${name}.cpuprofile`),JSON.stringify(profile));
    const summary=await page.evaluate(()=>{
      const b=window.bench,m=b.measure;m.stop=true;m.observer.disconnect();m.restore();m.cleanup();
      const p=(values,q)=>{const a=[...values].sort((a,b)=>a-b);return +(a[Math.min(a.length-1,Math.floor(a.length*q))]??0).toFixed(2);};
      const stats=a=>({count:a.length,p50:p(a,.5),p95:p(a,.95),p99:p(a,.99),max:p(a,1)});
      const elapsed=(performance.now()-m.start)/1000;
      return {seconds:+elapsed.toFixed(2),frames:stats(m.frames),inputToRaf:stats(m.inputs),reactCommit:stats(b.renders),longTasks:stats(m.longTasks),storageWrites:stats(m.writes),steps:b.runtime.getSnapshot().session.market.step-m.step,stepsPerSecond:+((b.runtime.getSnapshot().session.market.step-m.step)/elapsed).toFixed(2),candleCount:b.runtime.getSnapshot().session.market.books.BX.candles['1m'].length,domNodes:document.querySelectorAll('*').length};
    });results.scenarios.push({name,...summary});console.log(JSON.stringify({name,...summary}));
  };
  const paused=async()=>page.evaluate(()=>{const r=window.bench.runtime;if(r.getSnapshot().session.running)r.dispatch({type:'toggle'});});
  const play=async speed=>page.evaluate(speed=>{const r=window.bench.runtime;r.dispatch({type:'speed',speed});if(!r.getSnapshot().session.running)r.dispatch({type:'toggle'});},speed);
  await measure('paused-hover',paused);
  await measure('paused-pan',paused,'pan');
  await measure('paused-zoom',paused,'zoom');
  await measure('play-1x',()=>play(1));
  await measure('play-4x',()=>play(4));
  await measure('dense-indicators',async()=>{await paused();await page.locator('.simulation-chart-tool-menu summary').click();await page.getByLabel('SMA',{exact:true}).check();await page.getByLabel('Retained-tape VWAP',{exact:true}).check();await page.getByRole('button',{name:'Fit History',exact:true}).click();await page.getByRole('button',{name:'Close tools',exact:true}).click();await play(4);},'zoom');
  await measure('participant-adjustments',async()=>{await page.getByRole('button',{name:'Participants',exact:true}).last().click();await play(4);},'adjust');
  await measure('order-book',async()=>{await page.getByRole('button',{name:'Book',exact:true}).click();await play(4);});
  await measure('activity',async()=>{await page.getByRole('button',{name:'Activity',exact:true}).click();await play(4);});
  await page.screenshot({path:resolve(out,'desktop.png')});
  await paused();await page.getByLabel('Simulation instrument',{exact:true}).selectOption('BT');await page.getByLabel('Chart timeframe',{exact:true}).selectOption('5m');
  await page.getByRole('button',{name:'Expand workspace',exact:true}).click();await page.waitForTimeout(300);
  await page.screenshot({path:resolve(out,'expanded.png')});
  await page.getByRole('button',{name:'Exit expanded view',exact:true}).click();await page.setViewportSize({width:390,height:844});await page.waitForTimeout(300);await page.screenshot({path:resolve(out,'mobile.png')});
} finally {await writeFile(resolve(out,'results.json'),JSON.stringify(results,null,2));await browser.close();server.close();}
