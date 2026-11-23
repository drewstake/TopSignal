import { useEffect, useState } from "react";
import { requestJson } from "../../lib/api";
import { Button } from "../../components/ui/Button";
import { usePageVisibility } from "./usePageVisibility";

type Trade = { id: number; at: string; reason: string; side: string; quantity: number; status: string;
  entry: number | null; tp: number | null; sl: number | null; exit_price: number | null; exit_at: string | null;
  detail: string | null; net: number | null };
export type BlockedReport = { account_id: number; config_id: number; trades: Trade[]; wins: number; denominator: number;
  counts: Record<"TP" | "SL" | "other" | "open" | "unresolved", number>; next_before: number | null; reader_at: string | null };
const timestamp = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York",
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const reasons: Record<string,string> = { daily_profit_target_reached: "Daily profit stop", daily_loss_limit_reached: "Daily loss stop",
  proposed_stop_risk_exceeds_daily_loss_budget: "Stop risk exceeds remaining daily loss budget" };

export function BlockedTradesPanel({ account, config }: { account: number; config: number }) {
  const [pages, setPages] = useState<number[]>([]);
  const before = pages.at(-1);
  const [report, setReport] = useState<BlockedReport | null>(null);
  const [failed, setFailed] = useState(false);
  const visible = usePageVisibility();
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const path = `/api/accounts/${account}/topbot/${config}/blocked-trades` + (before ? "?before="+before : "");
        const data = await requestJson<BlockedReport>(path, { signal: controller.signal });
        if (data.account_id !== account || data.config_id !== config) throw new Error("Scope changed");
        if (!controller.signal.aborted) { setReport(data); setFailed(false); }
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 15_000);
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [account, config, before, visible]);
  const changePage = (next: number[]) => { setReport(null); setFailed(false); setPages(next); };
  return <section className="topbot-panel" aria-labelledby="blocked-trades-title">
    <h3 id="blocked-trades-title" className="text-base font-semibold">Simulated while blocked</h3>
    {failed && <p role="status">Simulated trade report unavailable.</p>}
    {report ? <>
      <dl className="topbot-levels topbot-stat-summary">
        {([["TP","TP hits"],["SL","SL hits"],["other","Other exits"],["open","Open trades"],["unresolved","Unresolved"]] as const)
          .map(([key,label]) => <div key={key}><dt>{label}</dt><dd>{report.counts[key]}</dd></div>)}
        <div><dt>Win rate</dt><dd>{report.denominator ? (100*report.wins/report.denominator).toFixed(1)+"%" : "—"}</dd></div>
      </dl>
      <p className="topbot-footnote">{report.wins} profitable / {report.denominator} resolved simulated trades after fees.</p>
      {(!report.reader_at || Date.now()-Date.parse(report.reader_at)>90_000) && <p role="status">Waiting for the replay worker.</p>}
      {report.trades.length ? <div className="topbot-stat-table"><table>
        <thead><tr>{["Block reason","Entry (ET)","Direction","Size","Entry price","TP","Initial SL","Outcome","Exit (ET)","Exit price","Simulated net P&L"]
          .map(title => <th key={title}>{title}</th>)}</tr></thead>
        <tbody>{report.trades.map(t => <tr key={t.id}><td>{reasons[t.reason] ?? t.reason}</td>
          <td>{timestamp.format(new Date(t.at))}</td><td>{t.side === "BUY" ? "Long" : "Short"}</td><td>{t.quantity}</td>
          <td>{t.entry ?? "—"}</td><td>{t.tp ?? "—"}</td><td>{t.sl ?? "—"}</td>
          <td>{t.status === "unresolved" ? "Unresolved · "+t.detail : t.status === "other" ? t.detail : t.status === "open" ? "Open" : t.status}</td>
          <td>{t.exit_at ? timestamp.format(new Date(t.exit_at)) : "—"}</td><td>{t.exit_price ?? "—"}</td>
          <td>{t.net == null ? "—" : money.format(t.net)}</td></tr>)}</tbody></table></div>
        : <p>No simulated trades recorded for this strategy yet.</p>}
      {pages.length > 0 && <Button variant="ghost" onClick={() => changePage(pages.slice(0,-1))}>Newer trades</Button>}
      {report.next_before != null && <Button variant="ghost" onClick={() => changePage([...pages,report.next_before!])}>Older trades</Button>}
    </> : !failed && <p>Loading simulated trades…</p>}
  </section>;
}
