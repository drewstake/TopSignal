// Execute the actual indicator's computational core, with no strategy changes.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const assert = require('node:assert/strict'), crypto = require('node:crypto');
const out = path.resolve(process.argv[2] || 'backend/storage/databento/exports/fxreplay/adaptive-2024-backtest');
const source = fs.readFileSync(path.join(__dirname, 'topbot-2024-adaptive.js'), 'utf8');
const core = source.split('// CORE START')[1].split('\n').slice(1).join('\n').split('// CORE END')[0];
const c = vm.createContext({}); vm.runInContext(core, c);
const bars = JSON.parse(fs.readFileSync(path.join(out, 'bars.json'), 'utf8'));
const options = {memory:240,minimum:24,advantage:0.05,positive:false,regular:true};
const state = {last:0,samples:[],pending:[],active:null,nextEntry:0,buys:0,sells:0,status:'',buyScore:0,sellScore:0};
const trades=[], unresolved=[], equity=[], statuses={};
let opened=null, cumulative=0;
for(let i=21;i<bars.length;i++) {
  const b=bars[i-1], current=bars[i], prior=state.active;
  const r=c.advance(state,bars.slice(i-21,i),current.t,current.o,options);
  statuses[state.status]=(statuses[state.status]||0)+1;
  if(prior && !prior.leg.done && !state.active && !r.exitReason) {
    unresolved.push({...opened,reason:'Invalid candle discarded active trade'}); opened=null;
  }
  if(r.exitReason) {
    assert.ok(opened, 'Exit without entry');
    if(r.exitReason.startsWith('DATA GAP')) {
      unresolved.push({...opened,reason:r.exitReason}); opened=null;
    } else {
      const pnl=opened.side*(r.exit-opened.entry)*2-1.22;
      assert.ok(Math.abs(pnl-r.exitR*opened.risk*2)<1e-8);
      assert.equal(opened.instrument,b.instrument,'Contract changed during trade');
      assert.ok(current.t-opened.entryTime<=900);
      cumulative+=pnl;
      trades.push({...opened,exitTime:current.t,exitBarTime:b.t,exit:r.exit,reason:r.exitReason,
        pnl:Math.round(pnl*100)/100,cumulative:Math.round(cumulative*100)/100});
      opened=null;
    }
  }
  // Five-minute close liquidation equity, including assumed exit slippage/fees.
  let marked=cumulative;
  if(prior && !prior.leg.done && opened) marked+=opened.side*(b.c-opened.entry)*2-2*0.5-1.22;
  equity.push({t:b.t+300,closed:cumulative,marked});
  if(r.side) {
    assert.equal(opened,null,'Overlapping entry');
    opened={entryTime:current.t,side:r.side,entry:r.entry,rawOpen:current.o,stop:r.stop,
      target:r.target,risk:r.risk,instrument:current.instrument,symbolCode:current.symbolCode,
      buyScore:state.buyScore,sellScore:state.sellScore};
  }
}
if(opened) unresolved.push({...opened,reason:'Open at end of data'});
assert.equal(trades.length+unresolved.length,state.buys+state.sells);
function drawdown(points,key) {
  let peak=0, peakTime=bars[0].t, worst=0, start=peakTime, end=peakTime;
  for(const p of points) {
    if(p[key]>peak){peak=p[key];peakTime=p.t;}
    if(peak-p[key]>worst){worst=peak-p[key];start=peakTime;end=p.t;}
  }
  return {dollars:worst,peakTime:start,troughTime:end};
}
const wins=trades.filter(t=>t.pnl>0), losses=trades.filter(t=>t.pnl<0);
const grossProfit=wins.reduce((s,t)=>s+t.pnl,0), grossLoss=-losses.reduce((s,t)=>s+t.pnl,0);
const summary={strategy:'TopBot 2024 Adaptive',scriptSha256:crypto.createHash('sha256').update(source).digest('hex'),
  options,contracts:1,pointValue:2,commissionPerSide:0.61,slippagePointsPerSide:0.5,
  trades:trades.length,unresolved:unresolved.length,buys:state.buys,sells:state.sells,
  wins:wins.length,losses:losses.length,breakeven:trades.length-wins.length-losses.length,
  winRate:100*wins.length/trades.length,grossProfit,grossLoss,profitFactor:grossProfit/grossLoss,
  netProfit:grossProfit-grossLoss,averageTrade:(grossProfit-grossLoss)/trades.length,
  maxClosedTradeDrawdown:drawdown(equity,'closed'),maxFiveMinuteLiquidationDrawdown:drawdown(equity,'marked'),
  exitTiming:'Exit time records candle close/availability; intrabar stop/target fill time cannot be determined from OHLC.',
  drawdownBasis:'Fixed one MNQ, no compounding; dollars from cumulative net P&L. No assumed account balance or percentage drawdown.',
  statusCounts:statuses};
fs.writeFileSync(path.join(out,'summary.json'),JSON.stringify(summary,null,2));
fs.writeFileSync(path.join(out,'trades.json'),JSON.stringify(trades));
fs.writeFileSync(path.join(out,'unresolved.json'),JSON.stringify(unresolved,null,2));
fs.writeFileSync(path.join(out,'equity.json'),JSON.stringify(equity));
fs.writeFileSync(path.join(out,'script-tested.js'),source);
const columns=Object.keys(trades[0]);
fs.writeFileSync(path.join(out,'trades.csv'),columns.join(',')+'\n'+trades.map(t=>columns.map(k=>t[k]).join(',')).join('\n')+'\n');
console.log(JSON.stringify(summary,null,2));
