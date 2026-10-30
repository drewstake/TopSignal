import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Link, useLocation, useNavigate, useOutletContext, useSearchParams } from 'react-router-dom';
import type { AppShellOutletContext } from '../../app/AppShell';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { useDemoMode } from '../../lib/demoMode';
import { getAccessTokenSync, subscribeSupabaseAuthChanges } from '../../lib/supabase';
import { localInput } from '../orderBookHistory/history';
import { Replay } from './Replay';
import { StudyForm, Field } from './StudyForm';
import { Scorecard } from './Scorecard';
import { useResearch } from './useResearch';
import { defaultStudy, fmt, inputClass, when, type Coverage, type Episode, type Experiment, type Study,
  type StudyResult, type Window, type WindowResult } from './types';

const subscribe=(callback:()=>void)=>subscribeSupabaseAuthChanges(()=>callback());

export function ResearchPage() {
  const {selectedAccountId}=useOutletContext<AppShellOutletContext>();
  const token=useSyncExternalStore(subscribe,getAccessTokenSync);
  const demo=useDemoMode();
  if(demo.enabled)return <Card>Exit Demo Mode to research your recorded market data.</Card>;
  if(selectedAccountId===null)return <Card>Select an owned ProjectX account to open market research.</Card>;
  return <ResearchView key={`${selectedAccountId}:${token}`} accountId={selectedAccountId}/>;
}

export function ResearchView({accountId}:{accountId:number}) {
  const research=useResearch(accountId);
  const {job,notebook,busy,error,request,report}=research;
  const [coverage,setCoverage]=useState<Coverage|null>(null);
  const [from,setFrom]=useState(''),[until,setUntil]=useState(''),[scope,setScope]=useState('10');
  const [ticks,setTicks]=useState('1,5,10,20');
  const [windows,setWindows]=useState<Window[]>([]);
  const [study,setStudy]=useState<Study>(structuredClone(defaultStudy));
  const [hypothesis,setHypothesis]=useState(''),[notes,setNotes]=useState(''),[freeze,setFreeze]=useState(false);
  const [saved,setSaved]=useState(''),[saving,setSaving]=useState(false);
  const [parent,setParent]=useState<string|null>(null),[forward,setForward]=useState<Experiment|null>(null);
  const location=useLocation(),navigate=useNavigate();
  const [params]=useSearchParams();
  const at=params.get('at'),jobId=params.get('job');
  const mode=location.pathname==='/event-study'?'study':location.pathname==='/research-notebook'?'notebook':'observe';
  const restored=useRef<string|null>(null);
  const currentAt=useRef<string|null>(null);
  useEffect(()=>{let active=true;void request<Coverage>('/coverage').then(c=>{if(!active)return;setCoverage(c);if(c.latest&&!at&&!jobId){const end=new Date(c.latest);setUntil(localInput(end.toISOString()));setFrom(localInput(new Date(+end-300000).toISOString()));}}).catch(report);return()=>{active=false;};},[request,report]);
  useEffect(()=>{if(jobId)void research.open(jobId);},[jobId,research.open]);
  useEffect(()=>{
    if(!at||currentAt.current===at)return;currentAt.current=at;
    const t=new Date(at);if(!Number.isFinite(+t))return;
    setFrom(localInput(new Date(+t-120000).toISOString()));setUntil(localInput(new Date(+t+90000).toISOString()));
  },[at]);
  useEffect(()=>{
    if(!jobId||!job||job.id!==jobId||restored.current===job.id)return;
    restored.current=job.id;setStudy(job.request.study);setScope(job.request.scope);setTicks(job.request.tick_distances.join(','));
    const first=job.request.windows[0];setFrom(localInput(first.start));setUntil(localInput(first.end));
    setWindows(job.request.windows.length>1?job.request.windows:[]);
  },[job,jobId]);
  const getWindow=():Window=>{
    const a=new Date(from),b=new Date(until);
    if(!Number.isFinite(+a)||!Number.isFinite(+b)||+b<=+a||+b-+a>600000)throw new Error('Choose a valid interval of at most 10 minutes. Five minutes is a useful starting point.');
    return {start:a.toISOString(),end:b.toISOString()};
  };
  const submit=(kind:'window'|'study')=>{
    try{
      if(jobId)navigate(`${kind==='study'?'/event-study':'/microstructure'}?account=${accountId}`,{replace:true});
      setSaved('');void research.submit({kind,windows:kind==='study'&&windows.length?windows:[getWindow()],scope,
        tick_distances:ticks.split(',').map(Number),study,experiment_id:forward?.id??null,forward:!!forward});
    }catch(e){report(e);}
  };
  const inspect=(episode:Episode)=>{
    const start=new Date(episode.time*1000-120000).toISOString(),end=new Date(episode.time*1000+90000).toISOString();
    navigate(`/microstructure?account=${accountId}&at=${encodeURIComponent(episode.timestamp)}`);
    setFrom(localInput(start));setUntil(localInput(end));
    void research.submit({kind:'window',windows:[{start,end}],scope,tick_distances:ticks.split(',').map(Number),study});
  };
  const reopen=(id:string,kind:'window'|'study',moment?:string)=>navigate(`${kind==='study'?'/event-study':'/microstructure'}?account=${accountId}&job=${id}${moment?`&at=${encodeURIComponent(moment)}`:''}`);
  const loadExperiment=(e:Experiment,observeForward=false)=>{
    setStudy(e.request.study);setScope(e.request.scope);setTicks(e.request.tick_distances.join(','));setHypothesis(e.hypothesis);setNotes(e.notes);
    setParent(e.id);setForward(observeForward?e:null);setFreeze(false);setWindows([]);
    if(observeForward){navigate(`/event-study?account=${accountId}`);setSaved(`Frozen rules loaded. Choose windows after ${when(e.forward_after)}.`);}
    else reopen(e.result_job_id,'study');
  };
  const exportJob=async(json=false)=>{
    if(!job?.result)return;
    try{const blob=json?new Blob([JSON.stringify(job,null,2)],{type:'application/json'}):await research.download(`/jobs/${job.id}/export`);
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`microstructure-${job.id}.${json?'json':'csv'}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){report(e);}
  };
  const save=async()=>{
    if(!job)return;setSaving(true);
    try{const e=await request<Experiment>('/experiments',{job_id:job.id,hypothesis,notes,freeze,parent_id:parent});setSaved(`Saved experiment version ${e.version}${e.frozen?' and froze its rules for forward observation':''}.`);setParent(e.id);research.refresh();}
    catch(e){report(e);}finally{setSaving(false);}
  };
  const result=job?.state==='complete'?job.result:null;
  return <div className="space-y-6 pb-10">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs uppercase tracking-[.18em] text-app-muted">Market microstructure · Research only</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">{mode==='study'?'Event study':mode==='notebook'?'Experiment notebook':'Liquidity research'}</h1>
      <p className="mt-2 max-w-3xl text-sm text-app-muted">Follow an observation from order-book evidence to independent episodes, realistic costs and out-of-sample scrutiny.</p></div>
      <Link className="text-sm text-app-accent underline" to={`/order-book-history?account=${accountId}`}>Compact history →</Link></header>
    <nav aria-label="Research tools" className="flex gap-1 rounded-xl border border-app-border bg-app-panel p-1">{[
      ['observe','/microstructure','Observe & replay'],['study','/event-study','Event study & tester'],['notebook','/research-notebook','Experiments & bookmarks'],
    ].map(([key,path,label])=><Link key={key} to={`${path}?account=${accountId}`} className={`flex-1 rounded-lg px-3 py-3 text-center text-sm ${mode===key?'bg-app-accent/15 font-semibold text-app-accent':'text-app-muted hover:text-app-text'}`}>{label}</Link>)}</nav>
    {coverage&&<div className="flex flex-wrap justify-between gap-2 rounded-xl border border-app-border px-4 py-3 text-xs text-app-muted">
      <span><strong className="text-app-text">{coverage.source}</strong> · {coverage.available_days.length} recorded UTC day(s) · {coverage.segments} committed raw segments</span>
      <span>{coverage.earliest?when(coverage.earliest):'No history'} → {coverage.latest?when(coverage.latest):'—'}</span>
      <span>Observed reconstruction · Snapshot / sequence completeness unverified</span>
    </div>}
    {error&&<Card><p role="alert" className="text-sm text-app-negative">{error}</p></Card>}
    {saved&&<Card><p role="status" className="text-sm text-emerald-400">{saved}</p></Card>}
    {mode!=='notebook'&&<Card className="space-y-4">
      <h2 className="font-semibold">Choose the evidence window</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="From (local time)"><input aria-label="Research start" className={inputClass} type="datetime-local" step="1" value={from} onChange={e=>setFrom(e.target.value)}/></Field>
        <Field label="To (local time)"><input aria-label="Research end" className={inputClass} type="datetime-local" step="1" value={until} onChange={e=>setUntil(e.target.value)}/></Field>
        <Field label="Depth scope"><select aria-label="Research depth scope" className={inputClass} value={scope} disabled={!!forward} onChange={e=>setScope(e.target.value)}><option value="all">All observed levels</option>{[1,5,10,20,50,100].map(n=><option key={n} value={n}>Nearest {n} levels</option>)}{ticks.split(',').map(Number).filter(n=>n>0&&n<=100).map(n=><option key={`t${n}`} value={`ticks:${n}`}>Within {n} ticks of best</option>)}</select></Field>
        <Field label="Fixed tick distances (comma separated)"><input aria-label="Fixed tick distances" className={inputClass} value={ticks} disabled={!!forward} onChange={e=>setTicks(e.target.value)}/></Field>
      </div>
      <div className="flex flex-wrap items-center gap-2"><Button size="sm" variant="secondary" disabled={!coverage?.latest||busy} onClick={()=>{const end=new Date(coverage!.latest!);setFrom(localInput(new Date(+end-300000).toISOString()));setUntil(localInput(end.toISOString()));}}>Latest completed 5m</Button>
        {mode==='observe'?<Button disabled={busy||!from} onClick={()=>submit('window')}>Load replay & flow</Button>:<Button size="sm" variant="secondary" disabled={windows.length>=12} onClick={()=>{try{setWindows(v=>[...v,getWindow()]);}catch(e){report(e);}}}>Add window to study</Button>}
        <span className="text-xs text-app-muted">Max 10 minutes / 256 MiB raw per window · Up to 12 study windows · Source times use receipt order</span>
      </div>
      {mode==='study'&&windows.length>0&&<div className="space-y-2 border-t border-app-border pt-3">{windows.map((w,i)=><div key={i} className="flex items-center justify-between gap-2 text-xs text-app-muted"><span>{when(w.start)} — {when(w.end)}</span><Button variant="ghost" size="sm" onClick={()=>setWindows(v=>v.filter((_,j)=>j!==i))}>Remove window {i+1}</Button></div>)}<p className="text-xs text-app-muted">The study uses these windows. Gaps and independent reconstruction boundaries remain excluded.</p></div>}
    </Card>}
    {mode==='study'&&<>
      {forward&&<Card><p className="text-sm text-amber-400">Frozen experiment v{forward.version}: {forward.hypothesis}. Only new windows after {when(forward.forward_after)} are eligible.</p><Button size="sm" variant="ghost" onClick={()=>setForward(null)}>Leave forward mode</Button></Card>}
      <details open={!result||job?.kind!=='study'} className="rounded-xl border border-app-border p-4"><summary className="mb-4 cursor-pointer text-sm font-medium">Signal, execution and validation settings</summary><StudyForm value={study} onChange={setStudy} disabled={busy||!!forward}/></details>
      <div className="flex flex-wrap items-center gap-3"><Button disabled={busy||!from} onClick={()=>submit('study')}>{forward?'Run frozen-rule observation':'Run bounded study & cost test'}</Button><span className="text-xs text-app-muted">Every run and variation is retained locally.</span></div>
    </>}
    {job&&mode!=='notebook'&&<Card><div className="flex items-center justify-between gap-3"><div><p role="status" className="text-sm font-medium">{job.message}</p><p className="mt-1 text-xs text-app-muted">{job.kind==='study'?'Event study':'Reconstruction'} · {job.state} · {Math.round(job.progress*100)}%</p></div>{busy&&<Button size="sm" variant="secondary" onClick={()=>void research.cancel()}>Cancel job</Button>}{result&&<div className="flex gap-2"><Button size="sm" variant="ghost" onClick={()=>void exportJob()}>Export results CSV</Button><Button size="sm" variant="ghost" onClick={()=>void exportJob(true)}>Export evidence JSON</Button></div>}</div>
      {busy&&<progress className="mt-3 h-1 w-full" max={1} value={job.progress}/>}</Card>}
    {result&&job?.kind==='window'&&mode==='observe'&&<Replay key={job.id} data={result as WindowResult} initialAt={at} onBookmark={async(point,note)=>{try{await request('/bookmarks',{job_id:job.id,timestamp:point.timestamp,note});research.refresh();}catch(e){report(e);throw e;}}}/>}
    {result&&job?.kind==='study'&&mode==='study'&&<>
      <Scorecard key={job.id} data={result as StudyResult} onEpisode={inspect}/>
      <Card className="space-y-4"><h2 className="text-lg font-semibold">Record the experiment</h2><Field label="Hypothesis"><textarea aria-label="Experiment hypothesis" maxLength={4000} className={`${inputClass} h-20 py-2`} value={hypothesis} onChange={e=>setHypothesis(e.target.value)} placeholder="State the expected effect and what evidence would reject it."/></Field>
        <Field label="Results, failures and next questions"><textarea aria-label="Experiment notes" maxLength={4000} className={`${inputClass} h-20 py-2`} value={notes} onChange={e=>setNotes(e.target.value)}/></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={freeze} onChange={e=>setFreeze(e.target.checked)}/>Freeze these rules for future observation</label>
        <p className="text-xs text-app-muted">Saved versions are immutable and retain the exact rules, thresholds, coverage, code/data versions and result checksum. Forward observation must begin after freezing. All previously inspected results remain in the run registry.</p>
        <Button disabled={saving||!hypothesis.trim()} onClick={()=>void save()}>Save experiment version</Button>
      </Card>
    </>}
    {mode==='notebook'&&notebook&&<>
      <Card className="space-y-4"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Frozen rules and experiment versions</h2><Button size="sm" variant="ghost" onClick={research.refresh}>Refresh notebook</Button></div>
        {!notebook.experiments.length&&<p className="text-sm text-app-muted">Run an event study, record its hypothesis and save a version. Keep unsuccessful variations too.</p>}
        {notebook.experiments.map(e=><article key={e.id} className="space-y-3 rounded-xl border border-app-border p-4"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{e.hypothesis}</h3><span className="text-xs text-app-muted">v{e.version} · {e.frozen?'Frozen':'Exploratory'} · {when(e.created_at)}</span></div><p className="whitespace-pre-wrap text-sm text-app-muted">{e.notes}</p><p className="text-xs text-app-muted">{e.tested_variations} recorded run(s) at registration · {e.status.replaceAll('_',' ')} · digest {e.version_digest.slice(0,16)}</p><div className="flex gap-2"><Button size="sm" variant="secondary" onClick={()=>loadExperiment(e)}>Reopen experiment</Button><Button size="sm" variant="ghost" onClick={()=>{loadExperiment(e);setForward(null);setSaved('Loaded as a new variation; saving will create a new immutable version.');}}>Fork variation</Button>{e.frozen&&<Button size="sm" variant="secondary" onClick={()=>loadExperiment(e,true)}>Observe forward</Button>}</div></article>)}
      </Card>
      <Card className="space-y-4"><h2 className="text-lg font-semibold">Research bookmarks</h2>{!notebook.bookmarks.length&&<p className="text-sm text-app-muted">Bookmark a replay moment to keep its timestamp, contract, depth scope and hypothesis.</p>}{notebook.bookmarks.map(b=><article key={b.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-app-border p-4"><div><p className="text-sm">{b.note}</p><p className="mt-1 text-xs text-app-muted">{when(b.timestamp)} · {b.contract_id} · scope {b.scope} · {b.feature_version}</p></div><Button size="sm" variant="secondary" onClick={()=>reopen(b.job_id,'window',b.timestamp)}>Replay bookmark</Button></article>)}</Card>
      <Card className="space-y-4"><h2 className="text-lg font-semibold">Every tested run</h2><p className="text-xs text-app-muted">{notebook.jobs_total} total runs. Latest 200 shown. A selected winner among many variations is not independent confirmation.</p>
        <div className="max-h-[32rem] overflow-auto">{notebook.jobs.map(j=><div key={j.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-app-border py-3"><div><p className="text-sm">{j.kind==='study'?'Study variation':'Observed reconstruction'} · {j.state}</p><p className="mt-1 text-xs text-app-muted">{when(j.created_at)} · {j.request.windows.length} window(s) · scope {j.request.scope}</p><p className="mt-1 text-xs text-app-muted">{j.request.study.conditions.map(c=>`${c.feature} ${c.operator==='lte'?'≤':'≥'} ${c.threshold}`).join(` ${j.request.study.combine} `)}</p></div><Button size="sm" variant="ghost" onClick={()=>reopen(j.id,j.kind)}>Open run</Button></div>)}</div>
      </Card>
      <p className="text-xs text-app-muted">Local research storage: {fmt(notebook.storage.bytes/1024**2)} / {fmt(notebook.storage.budget_bytes/1024**2,0)} MiB. {notebook.storage.blocked_reason?`Writes refused: ${notebook.storage.blocked_reason}.`:'No automatic deletion.'} Files are isolated by authenticated user and owned account. Include them in the filesystem backup.</p>
    </>}
    <p className="max-w-5xl text-xs leading-relaxed text-app-muted">Observed resting quantity is not executed volume, queue position or hidden liquidity. Receipt time and provider time are separate. Missing data stays missing. “Insufficient data”, invalid observations, unresolved execution and no detected edge are different outcomes. This workspace never sends orders or changes trading configuration.</p>
  </div>;
}
