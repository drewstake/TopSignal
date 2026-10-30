import { useCallback, useEffect, useRef, useState } from 'react';
import { requestJson, requestBlob } from '../../lib/api';
import type { Job, Notebook, ResearchRequest } from './types';

export function useResearch(accountId: number) {
  const base = `/api/accounts/${accountId}/microstructure`;
  const [job, setJob] = useState<Job | null>(null);
  const [notebook, setNotebook] = useState<Notebook | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const controllers = useRef(new Set<AbortController>());
  const alive = useRef(true);
  const revision = useRef(0);
  useEffect(() => { alive.current = true; return () => {
    alive.current = false; revision.current++; controllers.current.forEach(c => c.abort());
  }; }, [accountId]);
  const request = useCallback(async <T,>(path: string, body?: unknown): Promise<T> => {
    const controller = new AbortController(); controllers.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const result = await requestJson<T>(`${base}${path}`, { method: body === undefined ? 'GET' : 'POST', body, signal: controller.signal });
      if (!alive.current || controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      return result;
    } finally { clearTimeout(timeout); controllers.current.delete(controller); }
  }, [base]);
  const report = useCallback((e: unknown) => {
    if (alive.current && !(e instanceof DOMException && e.name === 'AbortError')) setError(e instanceof Error ? e.message : 'Research request failed.');
  }, []);
  const refresh = useCallback(() => { void request<Notebook>('/notebook').then(setNotebook).catch(report); }, [request, report]);
  const download = async (path: string) => {
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      const blob = await requestBlob(`${base}${path}`, { signal: controller.signal });
      if (!alive.current || controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      return blob;
    } finally { controllers.current.delete(controller); }
  };
  useEffect(refresh, [refresh]);
  const open = useCallback(async (id: string) => {
    const current = ++revision.current;
    setError(null); setBusy(true); setJob(null);
    try { const loaded = await request<Job>(`/jobs/${id}?result=true`); if (current === revision.current) setJob(loaded); }
    catch (e) { if (current === revision.current) report(e); }
    finally { if (alive.current && current === revision.current) setBusy(false); }
  }, [request, report]);
  const submit = async (body: ResearchRequest) => {
    const current = ++revision.current;
    setError(null); setBusy(true); setJob(null);
    try { const created = await request<Job>('/jobs', body); if (current === revision.current) { setJob(created); refresh(); } }
    catch (e) { if (current === revision.current) report(e); }
    finally { if (alive.current && current === revision.current) setBusy(false); }
  };
  const running = !!job && ['queued', 'running', 'cancelling'].includes(job.state);
  useEffect(() => {
    if (!running || !job) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const current = revision.current;
    const poll = async () => {
      try {
        const next = await request<Job>(`/jobs/${job.id}?result=true`);
        if (!active || current !== revision.current) return;
        setJob(next);
        if (['queued', 'running', 'cancelling'].includes(next.state)) timer = setTimeout(poll, 1000);
        else refresh();
      } catch (e) { if (active && current === revision.current) { report(e); timer = setTimeout(poll, 3000); } }
    };
    timer = setTimeout(poll, 500);
    return () => { active = false; clearTimeout(timer); };
  }, [job?.id, running, request, report, refresh]); // A status update must not restart the polling lane.
  const cancel = async () => {
    if (!job) return;
    const current = revision.current;
    try { const value = await request<Job>(`/jobs/${job.id}/cancel`, {}); if (alive.current && current === revision.current) setJob(value); }
    catch (e) { report(e); }
  };
  return { base, job, notebook, error, setError, busy: busy || running, request, submit, open, cancel, refresh, report, download };
}
