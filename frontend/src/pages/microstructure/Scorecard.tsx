import { useState } from 'react';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { fmt, percent, when, inputClass, type Episode, type Metrics, type StudyResult } from './types';

const tableClass='w-full text-left text-sm [&_th]:px-3 [&_th]:py-3 [&_th]:text-xs [&_th]:font-normal [&_th]:text-app-muted [&_td]:px-3 [&_td]:py-3 [&_td]:tabular-nums [&_tr]:border-b [&_tr]:border-app-border';
function Exclusions({ counts }: { counts: Record<string,number> }) {
  return <p className="text-xs text-app-muted">{Object.entries(counts).length?Object.entries(counts).map(([reason,n])=>`${n} ${reason.replaceAll('_',' ')}`).join(' · '):'No excluded cases in this calculation.'}</p>;
}
function Comparison({ baseline, harsh }: { baseline: Metrics; harsh: Metrics }) {
  const rows:[string, keyof Metrics, boolean?][]=[['Trades','trades'],['Gross expectancy ($/trade)','gross_expectancy'],['Net expectancy ($/trade)','net_expectancy'],['Net expectancy (R/trade)','net_expectancy_r'],['Net win rate','win_rate',true],['Realized average win / loss','realized_reward_risk'],['Profit factor','profit_factor'],['Net profit ($)','net_profit'],['Maximum drawdown ($)','max_drawdown'],['Longest loss streak','longest_loss_streak'],['Exposure (position seconds)','exposure_seconds'],['Break-even total round-trip cost ($/position)','break_even_round_trip_cost'],['Ambiguous cases','ambiguous']];
  return <div className="overflow-auto"><table className={tableClass}><thead><tr><th>Measure</th><th>Baseline execution</th><th>Harsher execution</th></tr></thead><tbody>{rows.map(([label,key,pct])=><tr key={key}><td>{label}</td>{[baseline,harsh].map((m,i)=><td key={i}>{pct?percent(m[key] as number|null):fmt(m[key] as number|null)}</td>)}</tr>)}</tbody></table></div>;
}

export function Scorecard({ data, onEpisode }: { data: StudyResult; onEpisode: (episode: Episode)=>void }) {
  const [breakdown,setBreakdown]=useState('session'),[page,setPage]=useState(0);
  const firstHorizon=Object.keys(data.outcome_summary)[0];
  const interval=data.tester.metrics.uncertainty;
  return <>
    <Card className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs uppercase tracking-wider text-app-muted">Setup scorecard</p><h2 className="mt-1 text-xl font-semibold">{{insufficient_data:'Insufficient data for repeatability',invalid_data:'Invalid data: no usable observations',unresolved_execution:'Execution remains unresolved'}[data.status]??'No independently confirmed edge'}</h2></div><span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs text-amber-400">Research only</span></div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[['Matching samples',fmt(data.occurrences,0)],['Independent episodes',fmt(data.independent_episodes,0)],['Sessions / days',`${data.sessions.length} / ${data.days.length}`],['Other recorded runs',fmt(data.tested_variations_before_this_run,0)]].map(([label,value])=><div key={label} className="rounded-lg border border-app-border p-3"><p className="text-xs text-app-muted">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>)}</div>
      <p className="text-sm text-app-muted">{data.confirmation} {data.invalid_measurements} invalid observations and {data.unavailable_feature_measurements} observations with unavailable condition inputs were excluded from triggering.</p>
      <p className="text-xs text-app-muted">{data.methodology.episodes}</p>
    </Card>
    <Card className="space-y-4"><h2 className="text-lg font-semibold">What happened after the signal?</h2><p className="text-xs text-app-muted">Direction-adjusted price points, before trading costs. Positive means favorable to the tested direction.</p>
      <div className="overflow-auto"><table className={tableClass}><thead><tr><th>Horizon</th><th>Valid / missing</th><th>Mean</th><th>Median</th><th>10th–90th percentile</th><th>Mean favorable / adverse</th><th>Nothing meaningful</th><th>Prior control mean</th><th>Paired difference</th></tr></thead>
      <tbody>{Object.entries(data.outcome_summary).map(([h,s])=><tr key={h}><td>{h}s</td><td>{s.valid} / {s.missing}</td><td>{fmt(s.mean)}</td><td>{fmt(s.median)}</td><td>{fmt(s.p10)} to {fmt(s.p90)}</td><td>{fmt(s.mean_favorable)} / {fmt(s.mean_adverse)}</td><td>{percent(s.nothing_rate)}</td><td>{fmt(s.baseline?.mean)} ({s.baseline?.valid??0})</td><td>{fmt(s.paired_mean_difference)}</td></tr>)}</tbody></table></div>
      <div className="grid gap-4 md:grid-cols-3">{Object.entries(data.outcome_summary).map(([h,s])=><div key={h} className="rounded-lg border border-app-border p-3"><h3 className="text-sm font-medium">{h}s return distribution</h3>
        <div className="mt-3 flex h-20 items-end gap-1">{s.distribution.map((b,i)=><div key={i} title={`${b.low} to ${b.high} points: ${b.count}`} className="flex flex-1 flex-col items-center justify-end text-[10px] text-app-muted"><span>{b.count}</span><div className="mt-1 w-full rounded-t bg-blue-400/60" style={{height:`${b.count/Math.max(1,...s.distribution.map(v=>v.count))*52}px`}}/></div>)}</div>
        <p className="mt-2 text-[10px] text-app-muted">Bins: &lt;−5 · −5/−2 · −2/−0.5 · ±0.5 · 0.5/2 · 2/5 · ≥5 pt</p>
        <div className="mt-3 space-y-2"><Exclusions counts={s.exclusions}/><Exclusions counts={s.baseline?.exclusions??{}}/><p className="text-xs text-app-muted">Paired 95% session interval: {s.uncertainty?.status==='estimated'?`${fmt(s.uncertainty.low)} to ${fmt(s.uncertainty.high)} pt`:'Insufficient sessions (minimum 3)'}</p></div>
      </div>)}</div>
      <p className="text-xs text-app-muted">{data.methodology.controls}</p>
    </Card>
    <Card className="space-y-4"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">Where is the effect stable?</h2><select aria-label="Study breakdown" className={`${inputClass} max-w-52`} value={breakdown} onChange={e=>setBreakdown(e.target.value)}>{Object.keys(data.breakdowns).map(k=><option key={k} value={k}>{k.replaceAll('_',' ')}</option>)}</select></div>
      <div className="overflow-auto"><table className={tableClass}><thead><tr><th>{breakdown.replaceAll('_',' ')}</th><th>Episodes</th><th>Valid at {firstHorizon}s</th><th>Mean points</th><th>Missing</th></tr></thead><tbody>{Object.entries(data.breakdowns[breakdown]??{}).map(([name,results])=><tr key={name}><td>{name}</td><td>{results[firstHorizon].n}</td><td>{results[firstHorizon].valid}</td><td>{fmt(results[firstHorizon].mean)}</td><td>{results[firstHorizon].missing}</td></tr>)}</tbody></table></div>
      <p className="text-xs text-app-muted">Fixed volatility bins: &lt;0.5, 0.5–2, ≥2 points/√s. Spread bins: ≤1 tick, ≤2 ticks, wider. These bins were not fit to subsequent returns.</p>
    </Card>
    <Card className="space-y-4"><h2 className="text-lg font-semibold">Does it survive costs?</h2><Comparison baseline={data.tester.metrics} harsh={data.harsher.metrics}/>
      <p className="text-sm text-amber-400">{data.methodology.execution}</p><Exclusions counts={data.tester.metrics.exclusions}/>
      <p className="text-xs text-app-muted">Harsher exclusions: {JSON.stringify(data.harsher.metrics.exclusions)}. Gross expectancy already includes bid/ask spread and modeled slippage; net also deducts fees. Negative break-even cost means a subsidy would be required.</p>
      <div className="flex flex-wrap gap-3">{data.tester.metrics.cost_sensitivity.map(c=><div key={c.additional_round_trip_cost} className="rounded-lg border border-app-border px-4 py-3"><p className="text-xs text-app-muted">+${c.additional_round_trip_cost} / position</p><p className="mt-1 font-semibold">${fmt(c.net_expectancy)} net/trade</p></div>)}</div>
      <p className="text-sm text-app-muted">Net expectancy 95% session interval: {interval.status==='estimated'?`$${fmt(interval.low)} to $${fmt(interval.high)}`:`Unavailable: ${interval.sessions} session(s), minimum 3.`} {interval.method}.</p>
      <details className="text-sm"><summary className="cursor-pointer text-app-muted">Inspect simulated attempts and exclusions</summary><div className="mt-3 max-h-72 overflow-auto"><table className={tableClass}><thead><tr><th>Trigger</th><th>Status</th><th>Reason</th><th>Gross $</th><th>Fees $</th><th>Net $</th></tr></thead><tbody>{data.tester.trades.map((t,i)=><tr key={i}><td>{when(t.timestamp)}</td><td>{t.status}{t.ambiguous?' · ambiguous':''}</td><td>{t.reason.replaceAll('_',' ')}</td><td>{fmt(t.gross)}</td><td>{fmt(t.fees)}</td><td>{fmt(t.net)}</td></tr>)}</tbody></table></div></details>
    </Card>
    <Card className="space-y-4"><h2 className="text-lg font-semibold">Chronological walk-forward</h2><p className="text-xs text-app-muted">Fixed rules, evaluated one session at a time; no tick randomization or future threshold fitting.</p>
      <div className="overflow-auto"><table className={tableClass}><thead><tr><th>Session</th><th>Earlier sessions</th><th>Partition</th><th>Trades</th><th>Net $/trade</th><th>Win rate</th></tr></thead><tbody>{data.walk_forward.map(r=><tr key={r.session}><td>{r.session}</td><td>{r.prior_sessions}</td><td>{r.partition}</td><td>{r.metrics.trades}</td><td>{fmt(r.metrics.net_expectancy)}</td><td>{percent(r.metrics.win_rate)}</td></tr>)}</tbody></table></div>
      <p className="text-xs text-app-muted">{data.methodology.validation}</p>
    </Card>
    <Card className="space-y-4"><div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">Inspect episodes, including failures</h2><span className="text-xs text-app-muted">Chronological · {data.episodes.length} episodes</span></div>
      <div className="overflow-auto"><table className={tableClass}><thead><tr><th>Episode</th><th>Receipt time</th><th>Partition</th><th>After {firstHorizon}s</th><th>Matched control</th><th/></tr></thead><tbody>{data.episodes.slice(page*20,(page+1)*20).map(ep=><tr key={ep.episode}><td>#{ep.episode}</td><td>{when(ep.timestamp)}</td><td>{ep.partition}</td><td>{ep.outcomes[firstHorizon].valid?`${fmt(ep.outcomes[firstHorizon].return_points)} pt`:ep.outcomes[firstHorizon].reason?.replaceAll('_',' ')}</td><td>{ep.control_timestamp?when(ep.control_timestamp):'No eligible earlier control'}</td><td><Button size="sm" variant="secondary" onClick={()=>onEpisode(ep)}>Inspect episode {ep.episode}</Button></td></tr>)}</tbody></table></div>
      {!data.episodes.length&&<p className="text-sm text-app-muted">No qualifying episodes. This is a valid failed setup search, not missing evidence to hide.</p>}
      <div className="flex gap-2"><Button size="sm" variant="ghost" disabled={!page} onClick={()=>setPage(v=>v-1)}>Previous</Button><Button size="sm" variant="ghost" disabled={(page+1)*20>=data.episodes.length} onClick={()=>setPage(v=>v+1)}>Next</Button></div>
    </Card>
  </>;
}
