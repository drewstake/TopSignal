import { hasPerformanceSample } from "../../../utils/sustainability";
import { formatProfitFactor } from "../../../utils/profitFactor";
import { Icon, type IconName } from "../../../components/ui/Icon";
import { Skeleton } from "../../../components/ui/Skeleton";
import type { AccountSummary } from "../../../lib/types";
import { formatInteger, formatNumber, formatPercent, formatPnl } from "../../../utils/formatters";

interface OverviewProps {
  summary: AccountSummary;
  loading: boolean;
  error: string | null;
  hasAccount: boolean;
  maxDrawdown: number;
  copyAdjusted?: boolean;
}

export function DashboardOverview({ summary, loading, error, hasAccount, maxDrawdown, copyAdjusted = false }: OverviewProps) {
  const hasTrades = hasAccount && summary.trade_count > 0 && !error;
  const metrics: { label: string; value: string; detail: string; icon: IconName; tone?: string }[] = [
    { label: copyAdjusted ? "Copy Trade Net" : "Net P&L", value: formatPnl(summary.net_pnl), detail: copyAdjusted ? "Combined leader + follower result" : "Realized profit after fees", icon: "chart", tone: hasTrades ? summary.net_pnl > 0 ? "positive" : summary.net_pnl < 0 ? "negative" : undefined : undefined },
    { label: copyAdjusted ? "Leader win rate" : "Win rate", value: hasTrades ? formatPercent(summary.win_rate, 1) : "—", detail: hasTrades ? `${formatInteger(summary.win_count)} wins · ${formatInteger(summary.loss_count)} losses` : "No closed trades", icon: "target" },
    { label: copyAdjusted ? "Leader profit factor" : "Profit factor", value: hasTrades ? formatProfitFactor(summary) : "—", detail: "Winning net P&L / absolute losing net P&L", icon: "pulse" },
    { label: copyAdjusted ? "Leader max drawdown" : "Max drawdown", value: hasTrades ? formatPnl(-Math.abs(maxDrawdown)) : "—", detail: "Largest peak-to-trough decline", icon: "shield" },
  ];

  return <>
    {!hasAccount && !loading ? <div className="dashboard-welcome">
      <span className="dashboard-welcome-icon"><Icon name="link" /></span>
      <div><h2>No account selected</h2><p>Choose an account in Active Account, or enable Live Account (CSV) to import trade history.</p></div>
    </div> : null}
    <div className="dashboard-overview" aria-label="Performance overview">
      {metrics.map((metric, index) => <section className={`overview-stat ${index === 0 ? "overview-stat-featured" : ""}`} key={metric.label}>
        <div className="overview-stat-heading"><h2>{metric.label}</h2><Icon name={metric.icon} /></div>
        {loading ? <Skeleton className="my-3 h-10 w-28" /> : <p className={`overview-stat-value ${metric.tone ? `metric-${metric.tone}` : ""}`}>{error ? "—" : metric.value}</p>}
        <p className="overview-stat-caption">{error ? "Performance unavailable" : metric.detail}</p>
      </section>)}
    </div>
  </>;
}

export function TradingProfile({ summary, score, loading, error, copyAdjusted = false }: { summary: AccountSummary; score: number; loading: boolean; error: string | null; copyAdjusted?: boolean }) {
  const hasTrades = summary.trade_count > 0 && !error;
  const total = summary.win_count + summary.loss_count + summary.breakeven_count;
  const outcomes = [
    { label: "Wins", count: summary.win_count, tone: "positive" },
    { label: "Losses", count: summary.loss_count, tone: "negative" },
    { label: "Breakeven", count: summary.breakeven_count, tone: "neutral" },
  ];
  return <section className="trading-profile">
    <div className="section-heading"><h2>{copyAdjusted ? "Leader trading snapshot" : "Trading snapshot"}</h2><Icon name="pulse" /></div>
    <div className="profile-trade-count"><strong>{loading ? "…" : error ? "—" : formatInteger(summary.trade_count)}</strong><span>closed trades</span></div>
    <div className="profile-outcome-bar" aria-hidden="true">{hasTrades && outcomes.map((outcome) => <span key={outcome.label} className={`outcome-${outcome.tone}`} style={{ width: `${total ? outcome.count / total * 100 : 0}%` }} />)}</div>
    <div className="profile-outcomes">{outcomes.map((outcome) => <div key={outcome.label}><span><i className={`outcome-${outcome.tone}`} />{outcome.label}</span><strong>{loading || error ? "—" : outcome.count}</strong></div>)}</div>
    <dl className="profile-stat-list"><div><dt>Active days</dt><dd>{loading || error ? "—" : summary.active_days}</dd></div><div><dt>Average trades / day</dt><dd>{hasTrades ? formatNumber(summary.avg_trades_per_day, 1) : "—"}</dd></div><div><dt>Sustainability score</dt><dd>{hasTrades && hasPerformanceSample(summary.trade_count, summary.active_days) ? `${formatInteger(score)} / 100` : "Awaiting history"}</dd></div></dl>
  </section>;
}
