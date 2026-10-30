// @vitest-environment jsdom
// These transport and component fixtures are synthetic, not research evidence.
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { useResearch } from './useResearch';
import { ResearchView } from './ResearchPage';
import { StudyForm } from './StudyForm';
import { requestJson } from '../../lib/api';
import { atOrBefore, defaultStudy, type Job, type ResearchRequest } from './types';
vi.mock('../../lib/api',()=>({requestJson:vi.fn(),requestBlob:vi.fn()}));

const body:ResearchRequest={kind:'study',windows:[{start:'2026-10-30T14:00:00Z',end:'2026-10-30T14:03:00Z'}],scope:'10',tick_distances:[1,5,10,20],study:defaultStudy};
const fixture:Job={id:'a'.repeat(32),state:'complete',kind:'study',created_at:'2026-10-30T14:05:00Z',progress:1,message:'Complete',request:body};
const notebook={jobs:[],experiments:[],bookmarks:[],jobs_total:0,storage:{bytes:0,budget_bytes:1000000,blocked_reason:null}};
afterEach(()=>{cleanup();vi.clearAllMocks();});

it('replay never selects a future observation to fill earlier time',()=>{
  const p=[{time:1},{time:2},{time:10}];
  expect(atOrBefore(p,.5)).toBeUndefined();expect(atOrBefore(p,1.9)).toBe(p[0]);expect(atOrBefore(p,8)).toBe(p[1]);expect(atOrBefore(p,10)).toBe(p[2]);
});

function Harness({account=1}:{account?:number}){
  const r=useResearch(account);
  return <><button onClick={()=>void r.open('first')}>First</button><button onClick={()=>void r.open('second')}>Second</button>
    <button onClick={()=>void r.submit(body)}>Run</button><button onClick={()=>void r.cancel()}>Cancel</button>
    <p>{r.job?.message}</p><p>{r.error}</p></>;
}

it('ignores late results when a different run has been selected',async()=>{
  let resolve:(v:Job)=>void=()=>{};
  vi.mocked(requestJson).mockImplementation((path)=>path.endsWith('/notebook')?Promise.resolve(notebook):path.includes('first')?new Promise(r=>{resolve=r as typeof resolve;}):Promise.resolve({...fixture,message:'Second result'}));
  render(<Harness/>);fireEvent.click(screen.getByText('First'));fireEvent.click(screen.getByText('Second'));
  expect(await screen.findByText('Second result')).toBeTruthy();
  await act(async()=>resolve({...fixture,message:'Stale first result'}));
  expect(screen.queryByText('Stale first result')).toBeNull();
});

it('aborts pending requests and never displays a prior account response',async()=>{
  let resolve:(v:Job)=>void=()=>{};let signal:AbortSignal|undefined;
  vi.mocked(requestJson).mockImplementation((path,options)=>path.endsWith('/notebook')?Promise.resolve(notebook):new Promise(r=>{resolve=r as typeof resolve;signal=options?.signal;}));
  const view=render(<Harness key="one"/>);fireEvent.click(screen.getByText('First'));
  view.rerender(<Harness key="two" account={2}/>);
  expect(signal?.aborted).toBe(true);
  await act(async()=>resolve({...fixture,message:'Other account data'}));
  expect(screen.queryByText('Other account data')).toBeNull();
});

it('cancels a running server job through its account-scoped endpoint',async()=>{
  vi.mocked(requestJson).mockImplementation((path)=>Promise.resolve(path.endsWith('/notebook')?notebook:path.endsWith('/cancel')?{...fixture,state:'cancelled',message:'Cancelled'}:{...fixture,state:'running',message:'Working'}));
  render(<Harness/>);fireEvent.click(screen.getByText('Run'));await screen.findByText('Working');fireEvent.click(screen.getByText('Cancel'));
  await screen.findByText('Cancelled');
  expect(requestJson).toHaveBeenCalledWith(`/api/accounts/1/microstructure/jobs/${fixture.id}/cancel`,expect.objectContaining({method:'POST'}));
});

it('rejects oversized UI windows before submitting a job',async()=>{
  vi.mocked(requestJson).mockImplementation(path=>Promise.resolve(path.endsWith('/notebook')?notebook:{earliest:'2026-10-30T12:26:00Z',latest:'2026-10-30T18:00:00Z',available_days:['2026-10-30'],segments:20,source:'Synthetic test source'}));
  render(<MemoryRouter initialEntries={['/microstructure']}><ResearchView accountId={123}/></MemoryRouter>);
  await screen.findByText('Synthetic test source');
  fireEvent.change(screen.getByLabelText('Research start'),{target:{value:'2026-10-30T10:00:00'}});
  fireEvent.change(screen.getByLabelText('Research end'),{target:{value:'2026-10-30T11:00:00'}});
  fireEvent.click(screen.getByText('Load replay & flow'));
  await screen.findByRole('alert');
  expect(vi.mocked(requestJson).mock.calls.some(([path])=>path.endsWith('/jobs'))).toBe(false);
});

it('edits a causal combination and exposes conservative execution assumptions',async()=>{
  const change=vi.fn();render(<StudyForm value={defaultStudy} onChange={change} disabled={false}/>);
  fireEvent.click(screen.getByText('Activity rises as bids thin'));
  expect(change).toHaveBeenCalledWith(expect.objectContaining({combine:'all',conditions:expect.arrayContaining([expect.objectContaining({feature:'volume_rate'})])}));
  expect(screen.getByText(/Spread is included once/)).toBeTruthy();
  expect(screen.getByLabelText('Unsampled stop/target ordering')).toHaveProperty('value','stop_first');
});

it('persists research request details and sends no source selector or identity',async()=>{
  vi.mocked(requestJson).mockImplementation(path=>Promise.resolve(path.endsWith('/notebook')?notebook:fixture));
  render(<Harness/>);fireEvent.click(screen.getByText('Run'));
  await waitFor(()=>expect(requestJson).toHaveBeenCalledWith('/api/accounts/1/microstructure/jobs',expect.objectContaining({body})));
  expect(JSON.stringify(body)).not.toMatch(/owner_hash|ssh_target|identity_file/);
});
