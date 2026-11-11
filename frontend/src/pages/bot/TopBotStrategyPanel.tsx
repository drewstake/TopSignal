import { useEffect, useState } from "react";
import { botsApi } from "../../lib/api";
import type { BotActivity, BotConfig } from "../../lib/types";
import { usePageVisibility } from "./usePageVisibility";
import { TopBotAuditTrail } from "./TopBotAuditTrail";
import { TopBotComparison } from "./TopBotComparison";
import { TopBotExecutionEvidence, TopBotRecordingStatus, type ExecutionEvidence, type ExcursionCoverage, type RecordingEvidence } from "./TopBotExecutionEvidence";

type RunnerTrade = {
  entry_id: number; side: string; stats_status: string; entry_at?: string; exit_at?: string;
  net_pnl_usd?: number; fees_usd?: number; hold_seconds?: number; mfe_points?: number; mae_points?: number;
  giveback_points?: number; plus_10?: number; plus_25?: number; minus_10?: number;
  confirmed_stop?: number; management_status?: string; exit_reason?: string;
  execution?: ExecutionEvidence; coverage?: ExcursionCoverage;
  strategy_class?: string; classification_basis?: string; strategy_version_id?: string;
};
export type TopBotStatistics = {
  depth?: { ready: boolean; reason: string; bid_total?: number; ask_total?: number; imbalance?: number; persistence_seconds?: number; evidence?: RecordingEvidence } | null;
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
  const fixedSession = bot?.strategy_params?.fixed_bracket_session;
  const persistentFixed = bot?.strategy_params?.exit_mode === "fixed";
  const fixed = persistentFixed || Boolean(fixedSession && activity?.profit_guard?.trading_day === fixedSession);
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
    <p className="topbot-muted">1 MNQ · {fixed ? `Fixed stop 25 pts · Take-profit 25 pts · No trailing${persistentFixed ? "" : ` · Session ${fixedSession} only`}` : "Initial stop 25 pts · Trail 25 pts after +25 · No fixed trade target"} · All trading sessions, including overnight · Daily loss limit ${bot?.max_daily_loss ?? 500} · {bot?.strategy_params?.daily_entry_limit_enabled === false ? "Daily entry-count limit disabled" : `Up to ${bot?.max_trades_per_day ?? 4} entries/day`}</p>
    <p className="topbot-muted">Daily profit target +$500 · Daily loss stop −$500 · Either threshold closes the TopBot position and pauses entries until the next trading session. Includes unrealized P&amp;L. Resets at 18:00 New York.</p>
    {fixed && <p className="topbot-muted">{persistentFixed ? "Fixed exits stay selected across sessions and restarts. Open trades keep their saved exit settings." : "Applies to new entries. Trailing exits resume for new entries next session; open trades keep their saved exit settings."}</p>}
    <p className="topbot-muted">Entries pause at the daily flatten cutoff (normally 16:08 New York, earlier on early closes) until the next session opens.</p>
    {stats?.depth && <p className="topbot-muted">Entry check: {stats.depth.ready
      ? `${stats.depth.bid_total} bid / ${stats.depth.ask_total} ask · ${((stats.depth.imbalance ?? 0) * 100).toFixed(1)}% imbalance · ${(stats.depth.persistence_seconds ?? 0).toFixed(0)}s sustained`
      : stats.depth.reason}</p>}
    <p className="topbot-footnote">Stopping entries keeps protection and position management active. {fixed ? "The fixed stop and target stay at the broker if the worker disconnects." : "The broker stop stays in place if the worker disconnects; trailing resumes with the worker."}</p>
    {actionError && <p role="alert" className="topbot-risk-list">{actionError}</p>}
    {error && <p aria-live="polite" className="topbot-muted">{error}</p>}
    <dl className="topbot-levels topbot-stat-summary">
      <div><dt>Closed / wins / losses</dt><dd>{stats ? `${stats.closed_trades} / ${stats.wins} / ${stats.losses}` : "—"}</dd></div>
      <div><dt>Net after fees</dt><dd>{stats ? `$${stats.net_pnl_usd.toFixed(2)}` : "—"}</dd></div>
      <div><dt>Winner hold · avg / median</dt><dd>{duration(stats?.winner_hold_mean_seconds)} / {duration(stats?.winner_hold_median_seconds)}</dd></div>
      <div><dt>Loser hold · avg / median</dt><dd>{duration(stats?.loser_hold_mean_seconds)} / {duration(stats?.loser_hold_median_seconds)}</dd></div>
    </dl>
    {stats?.items.length ? <div className="topbot-stat-table"><table><thead><tr>
      <th>Entry (New York)</th><th>Strategy</th><th>Side</th><th>Net / fees</th><th>Hold</th><th>Best / worst (pts)</th>
      <th>Giveback (pts)</th><th>Time to +10 / +25 / −10</th><th>Exit / status</th><th>Evidence</th>
    </tr></thead><tbody>{stats.items.map(row => <tr key={row.entry_id}>
      <td>{row.entry_at ? new Date(row.entry_at).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "Awaiting fill"}</td>
      <td title={row.strategy_version_id}>{row.strategy_class ?? "unknown"}<small> · {row.classification_basis?.replaceAll("_", " ") ?? "unsupported"}</small></td>
      <td>{row.side}</td><td>{number(row.net_pnl_usd)} / {number(row.fees_usd)}</td><td>{duration(row.hold_seconds)}</td>
      <td>{number(row.mfe_points)} / {number(row.mae_points)}</td><td>{number(row.giveback_points)}</td>
      <td>{duration(row.plus_10)} / {duration(row.plus_25)} / {duration(row.minus_10)}</td>
      <td>{(row.exit_reason ?? row.management_status ?? row.stats_status).replaceAll("_", " ")}
        {row.confirmed_stop != null && !row.exit_at && <small> · Stop {row.confirmed_stop}</small>}</td>
      <td><TopBotExecutionEvidence execution={row.execution} coverage={row.coverage} /></td>
    </tr>)}</tbody></table></div> : <p className="topbot-muted">No strategy fills recorded yet. Holding times and P&amp;L populate from broker executions.</p>}
    <p className="topbot-footnote">{stats?.scope ?? "Most recent 500 strategy entries on this account"}. Excursions and milestone times use observed executable quotes and may miss moves during data gaps. A dash means not observed, not zero. Holding-time statistics do not delay stops.</p>
    <TopBotAuditTrail key={accountId} accountId={accountId} demoMode={demoMode} />
    <TopBotRecordingStatus evidence={stats?.depth?.evidence} />
    {accountId != null && !demoMode && <TopBotComparison key={accountId} accountId={accountId} />}
  </section>;
}
