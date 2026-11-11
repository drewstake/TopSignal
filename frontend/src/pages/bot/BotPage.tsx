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
import { LiquidityHeatmapPanel } from "./LiquidityHeatmapPanel";
import { TopBotBiasStrip } from "./TopBotBiasStrip";
import { SessionOrderFlowPanel } from "./SessionOrderFlowPanel";
import { TopBotActivity, TopBotEvaluation, TopBotThinking } from "./TopBotEvaluation";
import { TopBotPosition } from "./TopBotPosition";
import { TopBotStrategyPanel } from "./TopBotStrategyPanel";
import { TopBotTradeRecorder } from "./TopBotTradeRecorder";
import { TopBotLiveImbalance } from "./TopBotLiveImbalance";
import { HistoryView } from "./history/BotLiquidityHistory";
import { useTopBotMonitor } from "./useTopBotMonitor";
import { usePageVisibility } from "./usePageVisibility";
import { timeLabel } from "./topBotPresentation";
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
    </nav>
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
  return <div className="topbot-dashboard" aria-label="Bot workspace">
    <header className="topbot-header">
      <div><div className="topbot-brand"><h1>TopBot</h1>
        <span role="status" aria-live="polite" title={monitor.status.label} className="inline-flex p-1">
          <span aria-hidden="true" className={`h-3 w-3 rounded-full ${dotColors[monitor.status.color]}`} />
          <span className="sr-only">{monitor.status.label}</span>
        </span><span className="topbot-mode">{demoMode ? "DEMO" : monitor.bot ? monitor.bot.execution_mode === "live" ? "LIVE EXECUTION" : "DRY RUN" : "MONITOR ONLY"}</span>
      </div><p className="topbot-subtitle">Market in view. Every decision in context.</p></div>
      <div className="topbot-header-meta"><strong>{market?.symbol?.split(".").at(-1) ?? "MNQ"}<span> / FUTURES</span></strong>
        <span>{monitor.status.label}</span><small>{monitor.updatedAt ? `Bot checked ${timeLabel(monitor.updatedAt)}` : "Waiting for bot status"} · polls every 15s</small>
      </div>
    </header>
    {!demoMode && !pending && !unavailable && accountId != null && <TopBotTradeRecorder accountId={accountId} />}
    <SessionOrderFlowPanel accountId={accountId} contractId={market?.contract_id ?? null} scope={monitor.scope}
      enabled={!demoMode && !pending && !unavailable} />
    <TopBotBiasStrip key={`${market?.contract_id ?? "pending"}:${demoMode}`} market={market} demoMode={demoMode} />
    <div className="topbot-market-grid">
      <section className="topbot-chart-shell" aria-label="Live market chart">
        {monitor.bot ? <SignalChart key={monitor.bot.id} monitorOnly bot={monitor.bot} authenticatedCacheScope={monitor.scope}
          activity={monitor.activity} lastEvaluation={null} refreshToken={0} onMarketData={setSnapshot} />
          : <MarketChart monitorOnly enabled={!demoMode} onMarketData={setSnapshot} onMarketResolved={onMarketResolved} />}
        <TopBotPosition key={`${accountId}:${market?.contract_id}`} accountId={accountId} contractId={market?.contract_id} demoMode={demoMode} />
      </section>
      <aside className="topbot-book-shell"><Depth key={`${monitor.scope}:${depthContract}`} contractId={depthContract} symbol={market?.symbol} demoMode={demoMode} sharedStore={depthStore} /></aside>
    </div>
    <LiquidityHeatmapPanel key={`${accountId}:${monitor.scope}:${depthContract}`} store={depthStore} contractId={depthContract}
      symbol={market?.symbol} tickSize={resolvedMarket?.contract_id === depthContract ? resolvedMarket?.tick_size : null} demoMode={demoMode}
      accountId={demoMode || pending || unavailable ? null : accountId} />
      <TopBotThinking activity={strategyActivity} status={monitor.status} snapshot={matchingSnapshot} now={now} error={monitor.error} strategyUnconfigured={strategyUnconfigured}
        liquidityHistory={!demoMode && !pending && !unavailable && accountId != null && (
          <div className="mt-4 space-y-4" aria-label="Recorded liquidity history">
            <HistoryView key={`${accountId}:${monitor.scope}:${depthContract}`} accountId={accountId} compact liveStore={depthStore} />
          </div>
        )}
        liveImbalance={monitor.bot?.strategy_params?.revision === "dom_contrarian_v1" && <TopBotLiveImbalance
          key={`${accountId}:${monitor.bot.id}:${monitor.bot.contract_id}`} contractId={monitor.bot.contract_id}
          threshold={Number(monitor.bot.strategy_params.imbalance_threshold) || .1}
          requiredSeconds={Number(monitor.bot.strategy_params.persistence_seconds) || 30}
          monitor={monitor}
          enabled={!demoMode && !pending && !unavailable && accountId != null} />} />
    <TopBotActivity activity={monitor.activity} />
    <TopBotEvaluation activity={strategyActivity} now={now} failed={Boolean(monitor.error)} strategyUnconfigured={strategyUnconfigured} />
    <TopBotStrategyPanel key={`${accountId}:${demoMode}`} accountId={accountId} bot={monitor.bot} activity={monitor.activity}
      demoMode={demoMode || pending || unavailable} onChanged={() => setRefreshToken(value => value + 1)} />
    <footer className="topbot-footer"><span>TOPBOT / OBSERVATION WORKSPACE</span><span>Live market streams · recorded bot decisions · times in New York</span></footer>
  </div>;
}
