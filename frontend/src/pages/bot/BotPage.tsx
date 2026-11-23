import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import type { AppShellOutletContext } from "../../app/AppShell";
import { useDemoMode } from "../../lib/demoMode";
import { getBotProviderAccountId } from "./botAccountIsolation";
import { Button } from "../../components/ui/Button";
import { BotLogsPanel } from "./BotLogsPanel";
import { BotSignalChart } from "./BotSignalChart";
import { ProjectXSignalChart } from "./ProjectXSignalChart";
import { OrderBookPanel } from "./OrderBookPanel";
import { OrderBookStore } from "./orderBook";
import { TopBotBiasStrip } from "./TopBotBiasStrip";
import { SessionOrderFlowPanel } from "./SessionOrderFlowPanel";
import { TopBotActivity, TopBotEvaluation, TopBotThinking } from "./TopBotEvaluation";
import { TopBotPosition } from "./TopBotPosition";
import { BotRunControl, TopBotStrategyPanel } from "./TopBotStrategyPanel";
import { TopBotEdgePanel } from "./TopBotEdgePanel";
import { BlockedTradesPanel } from "./BlockedTradesPanel";
import { OrderflowSettings } from "./OrderflowSettings";
import { useImbalanceReport } from "./imbalanceReport";
import { TopBotLiveImbalance } from "./TopBotLiveImbalance";
import { useTopBotMonitor } from "./useTopBotMonitor";
import { usePageVisibility } from "./usePageVisibility";
import { latestBotRun } from "./botRunPresentation";
import { decisionIsStale, latestDecision, timeLabel } from "./topBotPresentation";
import type { BotMarketSnapshot } from "./botMarketContext";
import type { BotChartMarket } from "./botChartData";
import "./TopBotDashboard.css";

const SignalChart = memo(BotSignalChart);
const MarketChart = memo(ProjectXSignalChart);
const Depth = memo(OrderBookPanel);
const dotColors = { green: "bg-emerald-400", yellow: "bg-amber-400", red: "bg-red-400" };

export function BotPage() {
  const { accounts, accountsLoading, accountsError, selectedAccountId } = useOutletContext<AppShellOutletContext>();
  const { enabled: demoMode } = useDemoMode();
  const accountId = getBotProviderAccountId(accounts.find(account => account.id === selectedAccountId) ?? null);
  const [searchParams, setSearchParams] = useSearchParams();
  const logsSelected = searchParams.get("view") === "logs";
  return <div className="space-y-4">
    <div className="page-bar">{!logsSelected && <h1 className="text-2xl font-semibold">TopBot</h1>}
    <nav aria-label="Bot views" className="flex gap-2">
      {(["overview", "logs"] as const).map(view => <Button key={view}
        variant={logsSelected === (view === "logs") ? "secondary" : "ghost"}
        aria-current={logsSelected === (view === "logs") ? "page" : undefined}
        onClick={() => {
          const next = new URLSearchParams(searchParams);
          if (view === "logs") next.set("view", "logs");
          else next.delete("view");
          setSearchParams(next);
        }}>{view === "logs" ? "Logs" : "Overview"}</Button>)}
    </nav></div>
    {logsSelected
      ? <BotLogsPanel accountId={accountsLoading || accountsError ? null : accountId} demoMode={demoMode} />
      : <TopBotDashboard key={`${accountId}:${demoMode}:${accountsLoading}:${Boolean(accountsError)}`}
          accountId={accountId} pending={accountsLoading} unavailable={Boolean(accountsError)} demoMode={demoMode} />}
  </div>;
}

function TopBotDashboard({ accountId, pending, unavailable, demoMode }: {
  accountId: number | null; pending: boolean; unavailable: boolean; demoMode: boolean;
}) {
  const [refreshToken, setRefreshToken] = useState(0);
  const monitor = useTopBotMonitor(accountId, pending, unavailable, demoMode, refreshToken);
  const reportAccount = demoMode || pending || unavailable ? null : accountId;
  const edge = useImbalanceReport(reportAccount);
  const [resolvedMarket, setResolvedMarket] = useState<BotChartMarket | null>(null);
  const [snapshot, setSnapshot] = useState<BotMarketSnapshot | null>(null);
  const [now, setNow] = useState(Date.now);
  const visible = usePageVisibility();
  useEffect(() => {
    if (!visible) return;
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, [visible]);
  const onMarketResolved = useCallback((market: BotChartMarket | null) => {
    setResolvedMarket(market); setSnapshot(null);
  }, []);
  const market = monitor.bot ?? resolvedMarket;
  const depthContract = demoMode || pending || unavailable ? null : market?.contract_id;
  // Reset buffered depth when the authenticated account scope changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const depthStore = useMemo(() => new OrderBookStore(depthContract, "all", 100), [depthContract, monitor.scope]);
  const matchingSnapshot = market && snapshot?.contractKey.split(":")[0] === market.contract_id ? snapshot : null;
  const strategyActivity = monitor.bot?.strategy_params?.revision === "custom_unconfigured" ? null : monitor.activity;
  const strategyUnconfigured = monitor.status.label === "Inactive · strategy not configured";
  const run = latestBotRun(monitor.activity);
  const decision = latestDecision(strategyActivity);
  const stale = decision && strategyActivity && (monitor.error || decisionIsStale(decision, strategyActivity, now));
  const mode = run?.status === "running" ? (run.dry_run ? "dry_run" : "live") : monitor.bot?.execution_mode;
  let modeLabel = "Unknown";
  if (demoMode) modeLabel = "Demo";
  else if (mode === "live") modeLabel = "Live · practice";
  else if (mode === "dry_run") modeLabel = "Dry run";
  else if (!monitor.bot && monitor.updatedAt && !monitor.error) modeLabel = "Monitor only";

  const guard = monitor.activity?.profit_guard;
  const risk = monitor.activity?.risk_events.filter(row => row.severity !== "info" && row.bot_run_id === run?.id)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0];
  let waiting = "No decision recorded.";
  if (strategyUnconfigured) waiting = "Waiting for entry, exit and risk rules.";
  else if (monitor.error) waiting = monitor.updatedAt ? "Showing the last confirmed account state." : "Waiting for an account status update.";
  else if (monitor.status.color === "green") waiting = "Waiting for the first recorded evaluation.";
  const onChanged = () => setRefreshToken(value => value + 1);
  return <div className="topbot-dashboard" aria-label="Bot workspace">
    <header className="glance-panel">
      <dl className="glance-metrics">
        <div><dt>Account</dt><dd>{accountId ?? "None selected"}</dd></div>
        <div><dt>{monitor.error && monitor.bot ? "Last known mode" : "Execution mode"}</dt><dd>{modeLabel}</dd></div>
        <div><dt>Bot status</dt><dd role="status" title={monitor.status.label}>
          <span aria-hidden="true" className={`mr-2 inline-block h-2 w-2 rounded-full ${dotColors[monitor.status.color]}`} />
          {monitor.status.color === "green" ? "Running" : monitor.status.label}
        </dd></div>
        <TopBotPosition key={market?.contract_id} accountId={reportAccount} contractId={market?.contract_id} demoMode={demoMode} />
      </dl>
      <div className="glance-action">
        <p className="min-w-0 flex-1 break-words text-sm">{decision ? <>
          <span className={stale ? "text-app-warning" : "text-app-muted"}>{stale ? "Stale decision" : "Last decision"} · {timeLabel(decision.created_at)}</span><br/>
          <strong>{decision.action === "HOLD" ? "Hold" : decision.action}</strong> · {decision.reason}
        </> : waiting}</p>
        <BotRunControl accountId={reportAccount} bot={monitor.bot} activity={monitor.activity} demoMode={demoMode} onChanged={onChanged} />
      </div>
      {guard && (guard.trigger || guard.error || !guard.available) && <p role="alert" className="glance-warning">Daily trading guard · {guard.message || guard.error || guard.trigger?.replaceAll("_", " ") || "Unavailable"}</p>}
      {risk && <p className="glance-warning">Last risk warning · {timeLabel(risk.created_at)} · {risk.message}</p>}
    </header>
    <div className="bot-live-workspace">
      <section className="topbot-chart-shell" aria-label="Live market chart">
        {monitor.bot ? <SignalChart key={monitor.bot.id} monitorOnly bot={monitor.bot} authenticatedCacheScope={monitor.scope}
          activity={monitor.activity} lastEvaluation={null} refreshToken={0} onMarketData={setSnapshot} />
          : <MarketChart monitorOnly enabled={!demoMode} onMarketData={setSnapshot} onMarketResolved={onMarketResolved} />}
      </section>
      <TopBotActivity activity={monitor.activity} />
    </div>
    <div className="bot-inspection">
      <section aria-labelledby="bot-order-flow">
        <h2 id="bot-order-flow">Order flow</h2>
        <aside className="topbot-book-shell"><Depth key={`${monitor.scope}:${depthContract}`} contractId={depthContract} symbol={market?.symbol} demoMode={demoMode} sharedStore={depthStore} /></aside>
        <SessionOrderFlowPanel accountId={accountId} contractId={market?.contract_id ?? null} scope={monitor.scope}
          enabled={!demoMode && !pending && !unavailable} />
        <TopBotBiasStrip key={`${market?.contract_id ?? "pending"}:${demoMode}`} market={market} demoMode={demoMode} />
        <TopBotThinking snapshot={matchingSnapshot} now={now}
          liveImbalance={monitor.bot?.strategy_params?.revision === "dom_contrarian_v1" && <TopBotLiveImbalance
            key={`${accountId}:${monitor.bot.id}:${monitor.bot.contract_id}`} contractId={monitor.bot.contract_id}
            threshold={Number(monitor.bot.strategy_params.imbalance_threshold) || .1}
            requiredSeconds={Number(monitor.bot.strategy_params.persistence_seconds) || 30}
            following={monitor.bot.strategy_params.imbalance_direction === "following"}
            monitor={monitor}
            enabled={!demoMode && !pending && !unavailable && accountId != null} />} />
      </section>
      <section aria-labelledby="bot-strategy-settings">
        <h2 id="bot-strategy-settings">Strategy settings</h2>
        {reportAccount != null && monitor.bot?.account_id === reportAccount && monitor.bot.strategy_params?.revision === "dom_contrarian_v1" &&
          <OrderflowSettings key={monitor.bot.id} bot={monitor.bot} onChanged={onChanged} />}
        <TopBotStrategyPanel accountId={accountId} bot={monitor.bot} versionLabels={edge.data?.version_labels} demoMode={demoMode || pending || unavailable} />
      </section>
      <section aria-labelledby="bot-evaluation-history">
        <h2 id="bot-evaluation-history">Evaluation history</h2>
        <TopBotEvaluation activity={strategyActivity} now={now} failed={Boolean(monitor.error)} strategyUnconfigured={strategyUnconfigured} />
        {reportAccount != null && <TopBotEdgePanel state={edge} now={now} />}
        {reportAccount != null && monitor.bot?.account_id === reportAccount && monitor.bot.strategy_params?.revision === "dom_contrarian_v1" &&
          <BlockedTradesPanel key={monitor.bot.id} account={reportAccount} config={monitor.bot.id} />}
      </section>
    </div>
  </div>;
}
