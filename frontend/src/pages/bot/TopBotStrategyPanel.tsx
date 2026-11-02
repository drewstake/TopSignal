import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import type { BotActivity, BotConfig } from "../../lib/types";
import { usePageVisibility } from "./usePageVisibility";
import { TopBotAuditTrail } from "./TopBotAuditTrail";

type RunnerTrade = {
  entry_id: number; side: string; stats_status: string; entry_at?: string; exit_at?: string;
  net_pnl_usd?: number; fees_usd?: number; hold_seconds?: number; mfe_points?: number; mae_points?: number;
  giveback_points?: number; plus_10?: number; plus_25?: number; minus_10?: number;
  confirmed_stop?: number; management_status?: string; exit_reason?: string;
};
export type TopBotStatistics = {
  depth?: { ready: boolean; reason: string; bid_total?: number; ask_total?: number; imbalance?: number; persistence_seconds?: number } | null;
  scope: string; items: RunnerTrade[]; closed_trades: number; wins: number; losses: number; net_pnl_usd: number;
  winner_hold_mean_seconds: number | null; winner_hold_median_seconds: number | null;
  loser_hold_mean_seconds: number | null; loser_hold_median_seconds: number | null;
};
const duration = (value: number | null | undefined) => value == null ? "—" : `${(value / 60).toFixed(1)}m`;
const number = (value: number | undefined) => value == null ? "—" : value.toFixed(2);

export function TopBotStrategyPanel({ accountId, bot, activity, demoMode, onChanged }: {
  accountId: number | null; bot: BotConfig | null; activity: BotActivity | null;
  demoMode: boolean; onChanged: () => void;
}) {
  const [stats, setStats] = useState<TopBotStatistics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const visible = usePageVisibility();
  const running = Boolean(bot?.enabled && activity?.runs.some(r => r.status === "running"));
  useEffect(() => {
    if (accountId == null || demoMode || !visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await botsApi.getTopBotStatistics(accountId!, { signal: controller.signal });
        if (!controller.signal.aborted) { setStats(result); setError(null); }
      } catch {
        if (!controller.signal.aborted) setError("Statistics unavailable. Showing the last received results.");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 15_000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [accountId, demoMode, visible]);
  async function act() {
    if (accountId == null || busy || demoMode) return;
    setBusy(true); setActionError(null);
    try {
      if (running && bot) await botsApi.stop(bot.id);
      else await botsApi.startTopBot(accountId, false);
      onChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Practice action failed");
    } finally { setBusy(false); }
  }
  return <section className="topbot-panel topbot-strategy" aria-label="Contrarian strategy and trade statistics">
    <div className="topbot-strategy-heading"><div><h2>Contrarian DOM <span className="topbot-muted">/ Practice API</span></h2>
      <p className="topbot-muted">Up to 100 available levels per side · 10% imbalance for 30s · Bid-heavy → short · Ask-heavy → long</p>
    </div><button className="topbot-run-button" disabled={busy || accountId == null || demoMode}
      onClick={() => void act()}>{busy ? "Updating…" : running ? "Stop new entries" : "Start practice bot"}</button></div>
    <p className="topbot-muted">1 MNQ · Initial stop 25 pts · Trail 25 pts after +25 · No fixed target · All trading sessions, including overnight · Daily loss limit $250 · Up to 4 entries/day</p>
    <p className="topbot-muted">Entries pause at the daily flatten cutoff (normally 16:08 New York, earlier on early closes) until the next session opens.</p>
    {stats?.depth && <p className="topbot-muted">Entry check: {stats.depth.ready
      ? `${stats.depth.bid_total} bid / ${stats.depth.ask_total} ask · ${((stats.depth.imbalance ?? 0) * 100).toFixed(1)}% imbalance · ${(stats.depth.persistence_seconds ?? 0).toFixed(0)}s sustained`
      : stats.depth.reason}</p>}
    <p className="topbot-footnote">Stopping entries keeps protection and runner management active. The broker stop stays in place if the worker disconnects; trailing resumes with the worker.</p>
    {actionError && <p role="alert" className="topbot-risk-list">{actionError}</p>}
    {error && <p aria-live="polite" className="topbot-muted">{error}</p>}
    <dl className="topbot-levels topbot-stat-summary">
      <div><dt>Closed / wins / losses</dt><dd>{stats ? `${stats.closed_trades} / ${stats.wins} / ${stats.losses}` : "—"}</dd></div>
      <div><dt>Net after fees</dt><dd>{stats ? `$${stats.net_pnl_usd.toFixed(2)}` : "—"}</dd></div>
      <div><dt>Winner hold · avg / median</dt><dd>{duration(stats?.winner_hold_mean_seconds)} / {duration(stats?.winner_hold_median_seconds)}</dd></div>
      <div><dt>Loser hold · avg / median</dt><dd>{duration(stats?.loser_hold_mean_seconds)} / {duration(stats?.loser_hold_median_seconds)}</dd></div>
    </dl>
    {stats?.items.length ? <div className="topbot-stat-table"><table><thead><tr>
      <th>Entry (New York)</th><th>Side</th><th>Net / fees</th><th>Hold</th><th>Best / worst (pts)</th>
      <th>Giveback (pts)</th><th>Time to +10 / +25 / −10</th><th>Exit / status</th>
    </tr></thead><tbody>{stats.items.map(row => <tr key={row.entry_id}>
      <td>{row.entry_at ? new Date(row.entry_at).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "Awaiting fill"}</td>
      <td>{row.side}</td><td>{number(row.net_pnl_usd)} / {number(row.fees_usd)}</td><td>{duration(row.hold_seconds)}</td>
      <td>{number(row.mfe_points)} / {number(row.mae_points)}</td><td>{number(row.giveback_points)}</td>
      <td>{duration(row.plus_10)} / {duration(row.plus_25)} / {duration(row.minus_10)}</td>
      <td>{(row.exit_reason ?? row.management_status ?? row.stats_status).replaceAll("_", " ")}
        {row.confirmed_stop != null && !row.exit_at && <small> · Stop {row.confirmed_stop}</small>}</td>
    </tr>)}</tbody></table></div> : <p className="topbot-muted">No strategy fills recorded yet. Holding times and P&amp;L populate from broker executions.</p>}
    <p className="topbot-footnote">{stats?.scope ?? "Most recent 500 strategy entries on this account"}. Excursions and milestone times use observed executable quotes and may miss moves during data gaps. A dash means not observed, not zero. Holding-time statistics do not delay stops.</p>
    <TopBotAuditTrail key={accountId} accountId={accountId} demoMode={demoMode} />
  </section>;
}
