import { lazy, memo, Suspense, useState } from "react";

import { Skeleton } from "../../components/ui/Skeleton";
import type { BotActivity, BotConfig, BotEvaluation } from "../../lib/types";
import { BotSignalChart } from "./BotSignalChart";
import { OrderBookPanel } from "./OrderBookPanel";
import type { BotMarketSnapshot } from "./botMarketContext";

const BotAnalysisPanel = lazy(() =>
  import("./BotAnalysisPanel").then((module) => ({ default: module.BotAnalysisPanel })),
);
const MemoizedSignalChart = memo(BotSignalChart);
const MemoizedOrderBook = memo(OrderBookPanel);

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

  return (
    <div className="order-1 min-w-0 space-y-5">
      <MemoizedSignalChart
        bot={bot}
        authenticatedCacheScope={authenticatedCacheScope}
        activity={activity}
        lastEvaluation={evaluation}
        refreshToken={refreshToken}
        demoMode={demoMode}
        onMarketData={setMarketSnapshot}
      />
      <MemoizedOrderBook
        key={bot?.contract_id ?? "no-contract"}
        contractId={bot?.contract_id}
        symbol={bot?.symbol}
        demoMode={demoMode}
      />
      <Suspense fallback={<Skeleton className="h-[360px]" />}>
        <BotAnalysisPanel
          bot={bot}
          evaluation={evaluation}
          marketSnapshot={marketSnapshot}
          loading={evaluating}
          onEvaluate={onEvaluate}
        />
      </Suspense>
    </div>
  );
}
