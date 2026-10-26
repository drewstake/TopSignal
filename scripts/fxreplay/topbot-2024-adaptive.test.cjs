const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(__dirname + '/topbot-2024-adaptive.js', 'utf8');
const core = source.split('// CORE START')[1].split('\n').slice(1).join('\n').split('// CORE END')[0];
const c = vm.createContext({});
vm.runInContext(core, c);
const options = {memory:240, minimum:24, advantage:0.05, positive:false, regular:true};
const initial = () => ({last:0,samples:[],pending:[],active:null,nextEntry:0,buys:0,sells:0,status:'',buyScore:0,sellScore:0});
const timestamp = s => Date.parse(s)/1000;
const bar = (t,o,h,l,close) => ({t,o,h,l,c:close,v:100});
let checks = 0;
function test(name, fn) { fn(); checks++; console.log('PASS ' + name); }

test('RTH in winter/summer, DST transition and 2024 holiday guard', () => {
  assert.equal(c.entrySession(timestamp('2024-01-02T14:35:00Z'),true),true);
  assert.equal(c.entrySession(timestamp('2024-01-02T14:30:00Z'),true),false);
  assert.equal(c.entrySession(timestamp('2024-03-11T13:35:00Z'),true),true);
  assert.equal(c.entrySession(timestamp('2024-11-04T14:35:00Z'),true),true);
  assert.equal(c.entrySession(timestamp('2024-01-01T14:35:00Z'),true),false);
  assert.equal(c.entrySession(timestamp('2024-07-03T14:35:00Z'),true),false);
  assert.equal(c.entrySession(timestamp('2024-01-06T14:35:00Z'),true),false);
  assert.equal(c.entrySession(timestamp('2026-01-02T14:35:00Z'),false),false);
});
test('Conservative ambiguous-bar stop, gaps, symmetric short and costs', () => {
  const leg = c.newLeg(1,100,4,6);
  c.settleLeg(leg, bar(0,100,108,94,101));
  assert.equal(leg.reason,'STOP'); assert.equal(leg.exit,96);
  assert.ok(Math.abs(leg.netR-(-10.22/8))<1e-12);
  const short = c.newLeg(-1,100,4,6);
  c.settleLeg(short, bar(0,100,108,94,101));
  assert.equal(short.reason,'STOP'); assert.equal(short.exit,104);
  assert.equal(short.netR,leg.netR);
  const gap = c.newLeg(1,100,4,6);
  c.settleLeg(gap,bar(0,90,91,89,90));
  assert.equal(gap.reason,'STOP GAP'); assert.equal(gap.exit,89.5);
});
test('Time exit occurs after exactly three bars', () => {
  const leg = c.newLeg(1,100,4,6);
  c.settleLeg(leg,bar(0,100,101,99,100)); assert.equal(leg.done,false);
  c.settleLeg(leg,bar(300,100,101,99,100)); assert.equal(leg.done,false);
  c.settleLeg(leg,bar(600,100,101,99,100));
  assert.equal(leg.reason,'TIME'); assert.ok(Math.abs(leg.netR-(-3.22/8))<1e-12);
});
function synthetic(n) {
  const out=[]; let p=17000; const start=timestamp('2024-01-02T10:00:00Z');
  for(let i=0;i<n;i++) {
    const d=(Math.floor(i/180)%2===0?1:-1)*(1+(i%7)*0.25);
    out.push(bar(start+i*300,p,Math.max(p,p+d)+0.25,Math.min(p,p+d)-0.25,p+d)); p+=d;
  }
  return out;
}
const bars=synthetic(1600);
function run(data, opts=options) {
  const state=initial(), signals=[];
  for(let i=21;i<data.length;i++) {
    const r=c.advance(state,data.slice(i-21,i),data[i].t,data[i].o,opts);
    if(r.side) signals.push({i,t:data[i].t,...r});
  }
  return {state,signals};
}
test('No label enters training before the full forward horizon settles', () => {
  const state=initial();
  for(let i=21;i<=23;i++) {
    c.advance(state,bars.slice(i-21,i),bars[i].t,bars[i].o,options);
    assert.equal(state.samples.length,0);
  }
  c.advance(state,bars.slice(3,24),bars[24].t,bars[24].o,options);
  assert.equal(state.samples.length,1);
});
test('Missing candles discard incomplete paths and block new entries', () => {
  const state=initial();
  c.advance(state,bars.slice(0,21),bars[21].t,bars[21].o,options);
  c.advance(state,bars.slice(2,23),bars[23].t,bars[23].o,options);
  assert.equal(state.samples.length,0);
  const gapped=bars.slice(0,21).map(x=>({...x})); gapped[10].t+=300;
  assert.equal(c.features(gapped),null);
});
const full=run(bars);
test('Both directions emitted, bounded state, warmup and nonoverlap', () => {
  assert.ok(full.state.buys>0 && full.state.sells>0);
  assert.ok(full.state.samples.length<=240 && full.state.pending.length<=3);
  assert.ok(full.signals[0].i>=47);
  for(let i=1;i<full.signals.length;i++) assert.ok(full.signals[i].t-full.signals[i-1].t>=900);
});
test('Future suffix cannot alter past decisions', () => {
  const prefix=run(bars.slice(0,600));
  assert.deepEqual(prefix.signals,full.signals.filter(x=>x.i<600));
  const altered=bars.map((b,i)=>i<600?b:bar(b.t,b.o*2,b.h*2,b.l*2,b.c*2));
  assert.deepEqual(run(altered).signals.filter(x=>x.i<600),prefix.signals);
});
test('Current-bar H/L/C/V never influence entry; duplicate evaluation is inert', () => {
  const state=initial();
  for(let i=21;i<100;i++) c.advance(state,bars.slice(i-21,i),bars[i].t,bars[i].o,options);
  const before=JSON.stringify(state);
  const r=c.advance(state,bars.slice(78,99),bars[99].t,999999,options);
  assert.equal(r.side,0); assert.equal(JSON.stringify(state),before);
  assert.ok(!source.includes('high(0)')&&!source.includes('low(0)')&&!source.includes('close(0)')&&!source.includes('volume(0)'));
});
test('Optional positive-payoff gate refuses two negative estimates', () => {
  const state=initial();
  state.samples=Array.from({length:24},()=>({cell:3,buy:-0.2,sell:-0.8}));
  const t=timestamp('2024-01-02T15:00:00Z');
  const rows=bars.slice(0,21).map((b,i)=>({...b,t:t-(21-i)*300}));
  const held=c.advance(state,rows,t,17040,{...options,positive:true});
  assert.equal(held.side,0);
  const directional=initial();directional.samples=state.samples;
  assert.equal(c.advance(directional,rows,t,17040,options).side,1);
});
console.log(JSON.stringify({checks,syntheticBuys:full.state.buys,syntheticSells:full.state.sells,
  note:'Synthetic correctness checks, not a 2024 performance backtest.'}));
