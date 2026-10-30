import type { ReactNode } from 'react';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { inputClass, type Study, type Execution } from './types';

const features: [string,string][] = [
  ['imbalance','Normalized imbalance'],['bid_depth_ratio','Bid / trailing depth mean'],['ask_depth_ratio','Ask / trailing depth mean'],
  ['bid_depth_z','Bid depth z-score'],['ask_depth_z','Ask depth z-score'],['bid_contracts','Resting bid contracts'],['ask_contracts','Resting ask contracts'],
  ['bid_depletion_rate','Bid depletion contracts/s'],['ask_depletion_rate','Ask depletion contracts/s'],
  ['bid_refill_rate','Bid refill contracts/s'],['ask_refill_rate','Ask refill contracts/s'],
  ['trade_count_rate','Received trade prints/s'],['volume_rate','Traded contracts/s'],
  ['negative_imbalance_seconds','Imbalance ≤ −0.3 duration (s)'],['positive_imbalance_seconds','Imbalance ≥ +0.3 duration (s)'],
  ['bid_thin_seconds','Bid below half trailing mean (s)'],['ask_thin_seconds','Ask below half trailing mean (s)'],
  ['spread','Spread (price points)'],['volatility','Trailing volatility (points/√s)'],
];
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="flex min-w-0 flex-col gap-1.5 text-xs text-app-muted">{label}{children}</label>;
}
export function NumberField({ label,value,onChange,min=0,max=100000,step=1 }: { label: string;value:number;onChange:(v:number)=>void;min?:number;max?:number;step?:number }) {
  return <Field label={label}><input aria-label={label} className={inputClass} type="number" min={min} max={max} step={step} value={value} onChange={e=>onChange(+e.target.value)}/></Field>;
}

export function StudyForm({ value, onChange, disabled }: { value: Study; onChange: (study: Study)=>void; disabled: boolean }) {
  const patch=(p:Partial<Study>)=>onChange({...value,...p});
  const execution=(p:Partial<Execution>)=>patch({execution:{...value.execution,...p}});
  const e=value.execution;
  return <fieldset disabled={disabled} className="space-y-5 disabled:opacity-60">
    <Card className="space-y-4">
      <div><p className="text-xs uppercase tracking-wider text-app-muted">01 · Define the observation</p><h2 className="mt-1 text-lg font-semibold">Conditions with a clear meaning</h2></div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={()=>patch({conditions:[{feature:'bid_depth_ratio',operator:'lte',threshold:.5}]})}>Unusually thin bids</Button>
        <Button size="sm" variant="secondary" onClick={()=>patch({conditions:[{feature:'ask_depth_ratio',operator:'lte',threshold:.5}],execution:{...e,direction:'long'}})}>Unusually thin asks</Button>
        <Button size="sm" variant="secondary" onClick={()=>patch({conditions:[{feature:'imbalance',operator:'lte',threshold:-.3},{feature:'bid_depth_ratio',operator:'lte',threshold:.7},{feature:'volume_rate',operator:'gte',threshold:10}],combine:'all',execution:{...e,direction:'short'}})}>Activity rises as bids thin</Button>
      </div>
      <p className="text-xs text-app-muted">Presets are hypotheses, not fitted signals. Total trade activity is available; validated aggressor buy/sell pressure is not.</p>
      {value.conditions.map((condition,index)=><div className="grid grid-cols-[minmax(0,1fr)_6rem_7rem_2.5rem] items-end gap-2" key={index}>
        <Field label={`Condition ${index+1}`}><select aria-label={`Condition ${index+1} feature`} className={inputClass} value={condition.feature} onChange={event=>patch({conditions:value.conditions.map((c,i)=>i===index?{...c,feature:event.target.value}:c)})}>{features.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></Field>
        <Field label="Comparison"><select aria-label={`Condition ${index+1} comparison`} className={inputClass} value={condition.operator} onChange={event=>patch({conditions:value.conditions.map((c,i)=>i===index?{...c,operator:event.target.value as 'lte'|'gte'}:c)})}><option value="lte">≤</option><option value="gte">≥</option></select></Field>
        <NumberField label={`Threshold ${index+1}`} value={condition.threshold} min={-1000000} step={.1} onChange={v=>patch({conditions:value.conditions.map((c,i)=>i===index?{...c,threshold:v}:c)})}/>
        <Button size="sm" variant="ghost" aria-label={`Remove condition ${index+1}`} disabled={value.conditions.length===1} onClick={()=>patch({conditions:value.conditions.filter((_,i)=>i!==index)})}>×</Button>
      </div>)}
      <div className="flex flex-wrap items-center gap-3"><Button size="sm" variant="ghost" disabled={value.conditions.length>=4} onClick={()=>patch({conditions:[...value.conditions,{feature:'spread',operator:'lte',threshold:.5}]})}>+ Add condition</Button>
        <select aria-label="Combine conditions" className={`${inputClass} max-w-56`} value={value.combine} onChange={ev=>patch({combine:ev.target.value as 'all'|'any'})}><option value="all">All conditions must hold</option><option value="any">Any condition may hold</option></select>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <NumberField label="Persist for (seconds)" value={value.persistence_seconds} max={30} step={.25} onChange={v=>patch({persistence_seconds:v})}/>
        <NumberField label="Episode cooldown (seconds)" value={value.cooldown_seconds} min={1} max={600} onChange={v=>patch({cooldown_seconds:v})}/>
        <Field label="Activity window"><select className={inputClass} value={value.activity_window} onChange={ev=>patch({activity_window:+ev.target.value})}>{[1,5,15].map(n=><option key={n} value={n}>Trailing {n} seconds</option>)}</select></Field>
        <Field label="Outcome horizons (seconds, comma separated)"><input aria-label="Outcome horizons" className={inputClass} defaultValue={value.horizons.join(',')} key={value.horizons.join(',')} onBlur={ev=>{const horizons=ev.target.value.split(',').map(Number);if(horizons.length&&horizons.every(n=>Number.isInteger(n)&&n>0&&n<=300))patch({horizons});}}/></Field>
        <NumberField label="Nothing meaningful (max excursion, ticks)" value={value.nothing_ticks} max={100} step={.5} onChange={v=>patch({nothing_ticks:v})}/>
      </div>
      <Field label="How were these thresholds chosen?"><textarea aria-label="Threshold rationale" className={`${inputClass} h-20 py-2`} maxLength={2000} value={value.threshold_rationale} onChange={ev=>patch({threshold_rationale:ev.target.value})}/></Field>
      <p className="text-xs text-app-muted">One episode per continuous condition, after persistence and cooldown. Controls are earlier ordinary periods in the same session, contract, half-hour, volatility and spread bucket. Matching never uses later prices.</p>
    </Card>
    <Card className="space-y-4">
      <div><p className="text-xs uppercase tracking-wider text-app-muted">02 · Test execution and costs</p><h2 className="mt-1 text-lg font-semibold">A small gross edge can disappear</h2><p className="mt-2 text-sm text-app-muted">At 1:1 reward/risk, a 51% win rate yields just 0.02R before costs. These are research simulations, separate from broker fills and TopBot paper results. The $1.24 fee default is illustrative; enter your actual round-trip costs.</p></div>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Field label="Direction"><select className={inputClass} value={e.direction} onChange={ev=>execution({direction:ev.target.value as 'long'|'short'})}><option value="short">Short</option><option value="long">Long</option></select></Field>
        <Field label="Entry order assumption"><select className={inputClass} value={e.order} onChange={ev=>execution({order:ev.target.value as Execution['order']})}><option value="market">Market at executable BBO</option><option value="limit_unresolved">Limit (unresolved without queue)</option></select></Field>
        <NumberField label="Stop (ticks)" value={e.stop_ticks} min={1} max={1000} onChange={v=>execution({stop_ticks:v})}/>
        <NumberField label="Target (ticks, ignored when equal R:R)" value={e.equal_reward_risk?e.stop_ticks:e.target_ticks} min={1} max={1000} onChange={v=>execution({target_ticks:v,equal_reward_risk:false})}/>
        <NumberField label="Maximum hold (seconds)" value={e.max_hold_seconds} min={1} max={300} onChange={v=>execution({max_hold_seconds:v})}/>
        <NumberField label="Contracts" value={e.size} min={1} max={100} onChange={v=>execution({size:v})}/>
        <NumberField label="Round-trip commissions + fees ($/contract)" value={e.commission_fees_round_trip} max={100} step={.01} onChange={v=>execution({commission_fees_round_trip:v})}/>
        <NumberField label="Slippage (ticks per side)" value={e.slippage_ticks_per_side} max={20} step={.25} onChange={v=>execution({slippage_ticks_per_side:v})}/>
        <NumberField label="Entry delay after trigger (ms)" value={e.entry_delay_ms} max={60000} onChange={v=>execution({entry_delay_ms:v})}/>
        <NumberField label="Entry latency (ms)" value={e.entry_latency_ms} max={10000} onChange={v=>execution({entry_latency_ms:v})}/>
        <NumberField label="Exit latency (ms)" value={e.exit_latency_ms} max={10000} onChange={v=>execution({exit_latency_ms:v})}/>
        <Field label="Overlapping positions"><select className={inputClass} value={e.overlapping} onChange={ev=>execution({overlapping:ev.target.value as Execution['overlapping']})}><option value="skip">Skip until position closes</option><option value="independent">Independent hypothetical trades</option></select></Field>
        <Field label="Unsampled stop/target ordering"><select className={inputClass} value={e.ambiguity} onChange={ev=>execution({ambiguity:ev.target.value as Execution['ambiguity']})}><option value="stop_first">Conservative stop-first</option><option value="exclude">Exclude unresolved exits</option></select></Field>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={e.equal_reward_risk} onChange={ev=>execution({equal_reward_risk:ev.target.checked})}/>Equal gross reward/risk</label>
      <p className="text-xs leading-relaxed text-app-muted">MNQ: 0.25 price point per tick, $0.50 per tick per contract. Longs buy the ask and sell the bid; shorts do the reverse. Spread is included once. Displayed top quantity must cover size. Raw-event price extrema detect barriers between samples; when both are reached, stop-first caps the outcome at a loss. Invalid paths stay unresolved. Targets trigger market exits after sampled detection and latency. Harsher assumptions add one tick per side, 500 ms each way and 50% fees.</p>
    </Card>
    <Card className="space-y-4"><div><p className="text-xs uppercase tracking-wider text-app-muted">03 · Keep discovery separate</p><h2 className="mt-1 text-lg font-semibold">Chronological validation</h2></div>
      <div className="grid gap-3 sm:grid-cols-2"><Field label="Validation begins (futures session date)"><input aria-label="Validation session" type="date" className={inputClass} value={value.validation_session??''} onChange={ev=>patch({validation_session:ev.target.value||null})}/></Field>
      <Field label="Untouched test begins (session date)"><input aria-label="Test session" type="date" className={inputClass} value={value.test_session??''} onChange={ev=>patch({test_session:ev.target.value||null})}/></Field></div>
      <p className="text-xs leading-relaxed text-app-muted">Leave both blank for exploration. Training precedes validation, which precedes test; outcomes crossing a session or split are excluded. A futures session starts at 18:00 Eastern the prior day. Thresholds are manual and frozen across sessions. Session-by-session results provide fixed-rule walk-forward evaluation. Reusing an inspected period does not make it untouched.</p>
    </Card>
  </fieldset>;
}
