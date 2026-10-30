import { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { HistoryChart } from '../orderBookHistory/HistoryChart';
import { fmt, percent, when, inputClass, atOrBefore, type Frame, type Point, type WindowResult } from './types';

export function Heatmap({ frames, range, selected, onSelect }: { frames: Frame[]; range: [number, number]; selected: Point;
  onSelect: (time: number) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [future, setFuture] = useState(false);
  const [priceTicks, setPriceTicks] = useState(80);
  useEffect(() => {
    const c = canvas.current, ctx = c?.getContext('2d');
    if (!ctx || !c) return;
    const width = 1160, height = 360, left = 92, right = 24, top = 20, bottom = 320;
    ctx.clearRect(0, 0, width, height);
    const visible = frames.filter(f => f.time >= range[0] && f.time <= range[1] && (future || f.time <= selected.time));
    const center = selected.mid_price ?? visible.find(f => f.mid_price != null)?.mid_price ?? 0;
    const low = center-priceTicks*.125, high = center+priceTicks*.125;
    const x = (t: number) => left+(t-range[0])/(range[1]-range[0])*(width-left-right);
    const y = (p: number) => bottom-(p-low)/(high-low)*(bottom-top);
    const max = visible.reduce((maximum,f)=>[...f.bids,...f.asks].reduce((m,l)=>Math.max(m,l.size),maximum),1);
    ctx.save(); ctx.beginPath(); ctx.rect(left, top, width-left-right, bottom-top); ctx.clip();
    visible.forEach((f, i) => {
      const next = visible[i+1];
      const w = Math.max(.5, x(Math.min(range[1], f.time+Math.min(1, next ? next.time-f.time : .5)))-x(f.time));
      if (f.quality_flags.length) { ctx.fillStyle = '#d99a3240'; ctx.fillRect(x(f.time),top,w,bottom-top); return; }
      for (const [side, color] of [[f.bids, '52,211,153'], [f.asks, '251,113,133']] as const) {
        for (const level of side) {
          ctx.fillStyle = `rgba(${color},${.12+.85*Math.log1p(level.size)/Math.log1p(max)})`;
          ctx.fillRect(x(f.time),y(level.price+.125),w,Math.max(1,(bottom-top)*.25/(high-low)));
        }
      }
      if (f.mid_price != null) { ctx.fillStyle='#93c5fd'; ctx.fillRect(x(f.time),y(f.mid_price)-1,w,2); }
      if (f.last_trade_price != null) { ctx.fillStyle='#f8fafc'; ctx.beginPath(); ctx.arc(x(f.time),y(f.last_trade_price),1.5,0,2*Math.PI); ctx.fill(); }
    });
    ctx.strokeStyle='#e2e8f0'; ctx.setLineDash([4,4]); ctx.beginPath(); ctx.moveTo(x(selected.time),top);ctx.lineTo(x(selected.time),bottom);ctx.stroke();ctx.setLineDash([]);
    ctx.restore(); ctx.font='11px system-ui';ctx.fillStyle='#94a3b8';
    for (let i=0;i<=4;i++) {
      const value=low+(high-low)*i/4;ctx.textAlign='right';ctx.fillText(fmt(value),left-12,y(value)+4);
      const t=range[0]+(range[1]-range[0])*i/4;ctx.textAlign=i===0?'left':i===4?'right':'center';
      ctx.fillText(new Date(t*1000).toLocaleTimeString([],{hour12:false}),x(t),345);
    }
  }, [frames, range, selected, future, priceTicks]);
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="font-semibold">Observed liquidity heatmap</h3><p className="text-xs text-app-muted">Green bids · Rose asks · Blue mid · White recent trade · Brightness = log displayed contracts</p></div>
      <div className="flex items-center gap-3 text-xs text-app-muted">
        <label className="flex items-center gap-2"><input type="checkbox" checked={future} onChange={e=>setFuture(e.target.checked)} />Show future context</label>
        <select aria-label="Heatmap price zoom" className={inputClass} value={priceTicks} onChange={e=>setPriceTicks(+e.target.value)}>
          {[20,40,80,160].map(n=><option key={n} value={n}>{n} ticks high</option>)}
        </select>
      </div>
    </div>
    <canvas ref={canvas} width={1160} height={360} className="w-full rounded-xl border border-app-border bg-slate-950"
      role="img" aria-label="Recorded observed-depth heatmap, price vertically and receipt time horizontally. Click to select time."
      onClick={e=>{const box=e.currentTarget.getBoundingClientRect();const f=Math.max(0,Math.min(1,((e.clientX-box.left)/box.width*1160-92)/1044));onSelect(range[0]+f*(range[1]-range[0]));}} />
    <p className="text-xs text-app-muted">Nearest 50 observed levels per side, at most two frames/second. Blank means unavailable or outside the observed ladder. Amber marks invalid state. No queue or individual-order reconstruction.</p>
  </div>;
}

function FlowChart({ points, selected, range, window }: { points: Point[]; selected: Point; range: [number,number]; window: number }) {
  const fields = [
    { title: `TRADE ACTIVITY · ${window}s window · contracts/s and received prints/s`, values: ['volume_rate','trade_count_rate'], colors: ['#60a5fa','#a78bfa'] },
    { title: `EXPLICIT BID DEPTH CHANGES · ${window}s · contracts/s`, values: ['bid_depletion_rate','bid_refill_rate'], colors: ['#fb7185','#34d399'] },
    { title: `EXPLICIT ASK DEPTH CHANGES · ${window}s · contracts/s`, values: ['ask_depletion_rate','ask_refill_rate'], colors: ['#fb7185','#34d399'] },
  ];
  const x = (t: number) => 92+(t-range[0])/(range[1]-range[0])*1044;
  const visible = points.filter(p=>p.time>=range[0]&&p.time<=range[1]);
  return <svg viewBox="0 0 1160 410" className="w-full rounded-xl border border-app-border bg-app-bg/50" role="img" aria-label="Trade volume, trade count, bid depletion and refill, ask depletion and refill aligned by receipt time">
    {fields.map((panel,index)=>{
      const top=30+index*130,bottom=top+85;
      const value=(p:Point,f:string)=>p.activity[String(window)]?.[f];
      const maximum=Math.max(1,...visible.flatMap(p=>panel.values.map(f=>value(p,f)).filter((v):v is number=>typeof v==='number')));
      return <g key={panel.title}>
        <text x={92} y={top-12} fill="currentColor" fontSize={11}>{panel.title}</text>
        <text x={80} y={top+5} textAnchor="end" fill="currentColor" opacity={.6} fontSize={10}>{fmt(maximum,0)}</text>
        <text x={80} y={bottom} textAnchor="end" fill="currentColor" opacity={.6} fontSize={10}>0</text>
        <line x1={92} x2={1136} y1={bottom} y2={bottom} stroke="currentColor" opacity={.1}/>
        {panel.values.map((f,k)=>{let previous:Point|undefined;const d=visible.map(p=>{
          const v=value(p,f);const linked=previous&&p.time-previous.time<=2&&previous.capture_id===p.capture_id&&previous.generation===p.generation&&previous.reconstruction_id===p.reconstruction_id;
          if(typeof v!=='number'||p.quality_flags.length){previous=undefined;return '';}
          previous=p;return `${linked?'L':'M'}${x(p.time)},${bottom-v/maximum*85}`;
        }).join(' ');return <path key={f} d={d} stroke={panel.colors[k]} fill="none" strokeWidth={1.4}/>;})}
      </g>;
    })}
    <line x1={x(selected.time)} x2={x(selected.time)} y1={10} y2={385} stroke="currentColor" opacity={.4} strokeDasharray="4 4"/>
  </svg>;
}

export function Replay({ data, initialAt, onBookmark }: { data: WindowResult; initialAt?: string | null; onBookmark: (point: Point, note: string)=>Promise<void> }) {
  const [contract,setContract]=useState(data.contracts[0]??'');
  const points=useMemo(()=>data.points.filter(p=>p.contract_id===contract),[data,contract]);
  const [selectedTime,setSelectedTime]=useState(()=>initialAt
    ? data.points.find(p=>p.timestamp===initialAt)?.time??new Date(initialAt).getTime()/1000
    : data.points[0]?.time??0);
  const selected=atOrBefore(points,selectedTime)??points[0];
  const [range,setRange]=useState<[number,number]>([+new Date(data.start)/1000,+new Date(data.end)/1000]);
  const [playing,setPlaying]=useState(false),[speed,setSpeed]=useState(4),[window,setWindow]=useState(5);
  const [note,setNote]=useState(''),[saved,setSaved]=useState(false),[saving,setSaving]=useState(false);
  useEffect(()=>{
    if(!playing)return;
    const start=performance.now(), at=selectedTime;
    const timer=setInterval(()=>{const t=at+(performance.now()-start)/1000*speed;
      if(t>=points.at(-1)!.time){setPlaying(false);setSelectedTime(points.at(-1)!.time);}else setSelectedTime(t);
    },100);return()=>clearInterval(timer);
  },[playing,speed,points]);
  if(!selected)return <Card><h2 className="font-semibold">No reconstructable observations</h2><p className="mt-2 text-sm text-app-muted">Choose a recorded interval. {data.skipped_segments} segments were unavailable or corrupt.</p></Card>;
  const activity=selected.activity[String(window)];
  const select=(t:number)=>{setPlaying(false);setSelectedTime(t);setSaved(false);};
  const step=(d:number)=>select(points[Math.max(0,Math.min(points.length-1,points.indexOf(selected)+d))].time);
  return <>
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><h2 className="text-lg font-semibold">Replay the evidence</h2><p className="mt-1 text-sm tabular-nums text-app-muted">{when(selected.timestamp)}</p></div>
        <select aria-label="Replay delivery contract" className={`${inputClass} max-w-64`} value={contract} onChange={e=>{setContract(e.target.value);setPlaying(false);}}>{data.contracts.map(c=><option key={c}>{c}</option>)}</select>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={()=>setPlaying(v=>!v)}>{playing?'Pause':'Play'}</Button>
        <Button size="sm" variant="secondary" onClick={()=>step(-1)}>← Step</Button><Button size="sm" variant="secondary" onClick={()=>step(1)}>Step →</Button>
        <select aria-label="Replay speed" className={`${inputClass} w-24`} value={speed} onChange={e=>setSpeed(+e.target.value)}>{[.5,1,2,4,10,30].map(n=><option key={n} value={n}>{n}×</option>)}</select>
        <input aria-label="Replay timestamp" type="range" className="min-w-40 flex-1 accent-blue-400" min={points[0].time} max={points.at(-1)!.time} step={.25} value={selected.time} onChange={e=>select(+e.target.value)}/>
        <Button size="sm" variant="ghost" onClick={()=>setRange([Math.max(+new Date(data.start)/1000,selected.time-30),Math.min(+new Date(data.end)/1000,selected.time+30)])}>Zoom 1 minute</Button>
        <Button size="sm" variant="ghost" onClick={()=>setRange([+new Date(data.start)/1000,+new Date(data.end)/1000])}>Reset zoom</Button>
      </div>
      <Heatmap frames={data.frames.filter(f=>f.contract_id===contract)} range={range} selected={selected} onSelect={select}/>
      {selected.quality_flags.length>0&&<p className="text-sm text-amber-400" role="status">Invalid observation: {selected.quality_flags.join(', ').replaceAll('_',' ')}</p>}
      {selectedTime-selected.time>2&&<p className="text-sm text-amber-400" role="status">Recording gap at {when(new Date(selectedTime*1000).toISOString())}. Readouts below describe only the last observed timestamp; no state is filled into this gap.</p>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[
        ['Mid / spread',`${fmt(selected.mid_price)} / ${fmt(selected.spread)} pt`],['Resting bid / ask',`${fmt(selected.bid_contracts,0)} / ${fmt(selected.ask_contracts,0)} contracts`],
        ['Imbalance',percent(selected.imbalance)],['Depth / trailing mean',`${fmt(selected.bid_depth_ratio)}× bid · ${fmt(selected.ask_depth_ratio)}× ask`],
      ].map(([label,value])=><div key={label} className="rounded-lg border border-app-border p-3"><p className="text-xs text-app-muted">{label}</p><p className="mt-2 text-lg font-semibold tabular-nums">{value}</p></div>)}</div>
    </Card>
    <Card className="space-y-4"><h2 className="text-lg font-semibold">Price, depth and imbalance</h2>
      <HistoryChart points={points} selected={selected} range={range} onSelect={p=>select(p.time)} onRange={setRange}/>
    </Card>
    <Card className="space-y-4">
      <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">Trade flow & liquidity</h2>
        <select aria-label="Activity window" className={`${inputClass} max-w-40`} value={window} onChange={e=>setWindow(+e.target.value)}>{[1,5,15].map(n=><option key={n} value={n}>Trailing {n}s</option>)}</select></div>
      <FlowChart points={points} selected={selected} range={range} window={window}/>
      <div className="grid grid-cols-2 gap-3 text-sm lg:grid-cols-4">{[
        ['Traded volume',`${fmt(activity?.traded_volume as number|null)} contracts / ${window}s`],['Trade count',`${fmt(activity?.trade_count as number|null)} received prints / ${window}s`],
        ['Sampled negative / positive persistence',`${fmt(selected.negative_imbalance_seconds)}s / ${fmt(selected.positive_imbalance_seconds)}s`],
        ['Thin bid / ask duration',`${fmt(selected.bid_thin_seconds)}s / ${fmt(selected.ask_thin_seconds)}s`],
        ['Trailing volatility',`${fmt(selected.volatility)} pt/√s`],['Baseline',selected.baseline_warmup?'Warming up':`${selected.baseline_observations} prior samples · 60s`],
        ['Best bid / ask quantity',`${fmt(selected.best_bid_quantity)} / ${fmt(selected.best_ask_quantity)} contracts`],['Directional pressure','Unavailable: aggressor side unverified'],
      ].map(([label,value])=><div key={label}><p className="text-xs text-app-muted">{label}</p><p className="mt-1 tabular-nums">{value}</p></div>)}</div>
      <p className="text-xs text-app-muted">Blue = volume rate; violet = print rate. Rose = accepted explicit depth reductions; green = replenishment, across all observed prices. Reductions are not proven cancellations. Baselines use only earlier valid observations; ratios require 30 seconds and 40 prior observations. Persistence is observed at summary timestamps; brief reversals between samples may be missed.</p>
    </Card>
    <Card className="space-y-4"><h2 className="font-semibold">Bookmark this observation</h2>
      <textarea className={`${inputClass} h-20 py-2`} aria-label="Bookmark hypothesis" maxLength={2000} value={note} onChange={e=>{setNote(e.target.value);setSaved(false);}} placeholder="What do you think might happen—and what would disprove it?"/>
      <Button disabled={!note.trim()||saving} onClick={()=>{setSaving(true);void onBookmark(selected,note).then(()=>setSaved(true)).catch(()=>setSaved(false)).finally(()=>setSaving(false));}}>Save bookmark</Button>
      {saved&&<span className="ml-3 text-sm text-emerald-400" role="status">Saved in your local notebook.</span>}
      <details className="text-xs text-app-muted"><summary className="cursor-pointer">Quality & provenance</summary>
        <div className="mt-3 space-y-2"><p>{data.reconstruction}</p><p>{data.points.length} observations from {fmt(data.raw_events,0)} raw events · {fmt(data.read_bytes/1024**2)} MiB read · {data.skipped_segments} skipped segments</p>
          <p>Provider time: {selected.provider_at?when(selected.provider_at):'unavailable'} · Receipt: {when(selected.timestamp)}</p>
          <p>Explicit depth levels: {selected.explicit_depth_levels.bid} bid / {selected.explicit_depth_levels.ask} ask. Quote-only prices do not establish explicit depth.</p>
          <p>Raw book states since the prior sample: {selected.execution_interval?.valid?'valid observed path':'unavailable or invalid path; execution remains unresolved'}. Bid range {fmt(selected.execution_interval?.bid_low)}–{fmt(selected.execution_interval?.bid_high)}, ask range {fmt(selected.execution_interval?.ask_low)}–{fmt(selected.execution_interval?.ask_high)}.</p>
          <p>Schema {selected.feature_version} · Capture {selected.capture_id} · Receipt {selected.index} · Generation {selected.generation}</p>
          <p className="break-all">Data digest {data.data_id}</p><p>Sequence diagnostics: {JSON.stringify(data.observed_sequence_diagnostics)}. Jumps/repeats are observations, not proof of loss or completeness.</p>
        </div>
      </details>
    </Card>
  </>;
}
