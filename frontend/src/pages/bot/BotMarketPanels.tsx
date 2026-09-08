import { lazy, memo, Suspense, useCallback, useState } from "react";

import { Skeleton } from "../../components/ui/Skeleton";
import type { BotActivity, BotConfig, BotEvaluation } from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";
import { OrderBookPanel } from "./OrderBookPanel";
import { ProjectXSignalChart } from "./ProjectXSignalChart";
import type { BotMarketSnapshot } from "./botMarketContext";
import type { BotChartMarket } from "./botChartData";

const BotAnalysisPanel = lazy(() =>
  import("./BotAnalysisPanel").then((module) => ({ default: module.BotAnalysisPanel })),
);
const MemoizedSignalChart = memo(BotSignalChart);
const MemoizedOrderBook = memo(OrderBookPanel);
const MemoizedProjectXSignalChart = memo(ProjectXSignalChart);

interface BotMarketPanelsProps {
  bot: BotConfig | null;
  authenticatedCacheScope: string | null;
  activity: BotActivity | null;
  evaluation: BotEvaluation | null;
  refreshToken: number;
  demoMode: boolean;
  evaluating: boolean;
  onEvaluate?: () => void;
}

// Keep streamed snapshots local: they only feed the analysis panel, and must
// not rerender account controls, activity tables, or historical replay results.
export function BotMarketPanels({
  bot, authenticatedCacheScope, activity, evaluation, refreshToken, demoMode, evaluating, onEvaluate,
}: BotMarketPanelsProps) {
  const [marketSnapshot, setMarketSnapshot] = useState<BotMarketSnapshot | null>(null);
  const [resolvedMarket, setResolvedMarket] = useState<BotChartMarket | null>(null);
  const handleMarketResolved = useCallback((market: BotChartMarket | null) => {
    setResolvedMarket(market);
    setMarketSnapshot(null);
  }, []);
  const market = bot ?? (demoMode ? null : resolvedMarket);

  return (
    <div className="order-1 min-w-0 space-y-5">
      {bot ? <MemoizedSignalChart
        bot={bot}
        authenticatedCacheScope={authenticatedCacheScope}
        activity={activity}
        lastEvaluation={evaluation}
        refreshToken={refreshToken}
        demoMode={demoMode}
        onMarketData={setMarketSnapshot}
      /> : <MemoizedProjectXSignalChart enabled={!demoMode} onMarketData={setMarketSnapshot} onMarketResolved={handleMarketResolved} />}
      <MemoizedOrderBook
        key={market?.contract_id ?? "no-contract"}
        contractId={market?.contract_id}
        symbol={market?.symbol}
        demoMode={demoMode}
      />
      <Suspense fallback={<Skeleton className="h-[360px]" />}>
        <BotAnalysisPanel
          bot={bot}
          evaluation={evaluation}
          marketSnapshot={marketSnapshot}
          market={market}
          loading={evaluating}
          onEvaluate={onEvaluate}
        />
      </Suspense>
    </div>
  );
}
