import { useEffect, useState } from "react";
import { ApiError, requestJson } from "../../lib/api";
import { usePageVisibility } from "./usePageVisibility";

export type Actual = { id: number; side: string; mode: string; version: string; session: string; entry: string;
  exit: string | null; net: number | null; costs: number | null; fill: number | null; submit: number | null; qualify: number | null };
export type Inclusion = { id: number; mode: string; version: string; date: string; outcome: string; status: string; reasons: string[] };
type Horizon = { seconds: number; valid: number; all_mean: number | null; pairs: number; signal: number | null;
  baseline: number | null; difference: number | null };
type Episode = { id: number; episode: string; at: string; side: number; bid: number | null; ask: number | null;
  control: number | null; disposition: string; reason: string | null; rule: string;
  moves: Record<string,{ points: number | null; reason: string | null }> };
export type SignalStudy = { source: string; total: number; matched: number; filled: number; horizons: Horizon[];
  recent: Episode[]; gaps: number; through: string | null };
export type EdgeReport = { account_id: number; config_id: number; contract: string; started: string; as_of: string;
  actual: Actual[]; truncated: boolean; coverage: Inclusion[]; studies: SignalStudy[] };
export type ReportState = { account: number | null; data?: EdgeReport; message?: string };

export function useImbalanceReport(account: number | null) {
  const [state, setState] = useState<ReportState>({ account: null });
  const visible = usePageVisibility();
  useEffect(() => {
    if (account === null || !visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const path = "/api/accounts/" + account + "/topbot/edge";
      const url = import.meta.env.VITE_TOPBOT_REPORT_SAME_ORIGIN === "true" ? new URL(path, window.location.origin).href : path;
      try {
        const report = await requestJson<EdgeReport>(url, { signal: controller.signal });
        if (report.account_id !== account) throw new Error("Account mismatch");
        if (!controller.signal.aborted) setState({ account, data: report });
      } catch (error) {
        if (!controller.signal.aborted) setState(old => ({ account, data: old.account === account ? old.data : undefined,
          message: error instanceof ApiError && error.status === 404 ? "No study report for this account." : "Study refresh unavailable." }));
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 20_000);
      }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [account, visible]);
  return state.account === account ? state : { account };
}

export function performance(actual: Actual[]) {
  const closed = actual.filter((t): t is Actual & { net: number; exit: string } =>
    typeof t.net === "number" && Number.isFinite(t.net) && !!t.exit).sort((a,b) => Date.parse(a.exit)-Date.parse(b.exit) || a.id-b.id);
  let equity = 0, peak = 0, drawdown = 0, win = 0, loss = 0, positive = 0, negative = 0;
  let costs = 0, knownCosts = 0;
  for (const t of closed) {
    equity += t.net;
    peak = Math.max(equity, peak);
    drawdown = Math.max(drawdown, peak-equity);
    if (t.net > 0) { win++; positive += t.net; }
    if (t.net < 0) { loss++; negative -= t.net; }
    if (t.costs != null) { costs += t.costs; knownCosts++; }
  }
  const averageWin = win ? positive / win : null, averageLoss = loss ? negative / loss : null;
  const breakeven = averageWin != null && averageLoss != null ? averageLoss / (averageWin + averageLoss) : null;
  return { count: closed.length, sessions: new Set(closed.map(t => t.session)).size, win, loss, flats: closed.length-win-loss,
    rate: win+loss ? win/(win+loss) : null, breakeven, net: closed.length ? equity : null,
    gross: closed.length && knownCosts === closed.length ? equity+costs : null,
    costs: closed.length && knownCosts === closed.length ? costs : null,
    mean: closed.length ? equity/closed.length : null, drawdown: closed.length ? drawdown : null };
}
