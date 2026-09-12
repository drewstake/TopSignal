import { useEffect, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { botsApi, getAuthenticatedCacheScope } from "../../lib/api";
import { BotSignalChart } from "./BotSignalChart";
import { BOT_CHART_INITIAL_BARS, type BotChartMarket } from "./botChartData";
import type { BotMarketSnapshot } from "./botMarketContext";

type ProjectXSignalChartProps = {
  enabled: boolean;
  onMarketData?: (snapshot: BotMarketSnapshot | null) => void;
  onMarketResolved?: (market: BotChartMarket | null) => void;
};

/** Market-data access is independent of account selection and bot execution. */
export function ProjectXSignalChart(props: ProjectXSignalChartProps) {
  const [retry, setRetry] = useState(0);
  return <ProjectXChartConnection key={`${props.enabled}:${retry}`} {...props}
    onRetry={() => setRetry(value => value + 1)} />;
}

function ProjectXChartConnection({ enabled, onMarketData, onMarketResolved, onRetry }: ProjectXSignalChartProps & {
  onRetry: () => void;
}) {
  const [connection, setConnection] = useState<{ market: BotChartMarket; scope: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    onMarketResolved?.(enabled ? connection?.market ?? null : null);
    return () => onMarketResolved?.(null);
  }, [connection, enabled, onMarketResolved]);

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      controller.abort();
      setError("ProjectX contract lookup timed out. Check your connection and try again.");
    }, 30_000);
    void Promise.all([
      getAuthenticatedCacheScope(),
      botsApi.searchContracts({ searchText: "MNQ", live: false }, { signal: controller.signal }),
    ]).then(([scope, contracts]) => {
      if (controller.signal.aborted) return;
      const contract = contracts.find(row => row.active_contract === true && (
        row.symbol_id?.split(".").at(-1)?.toUpperCase() === "MNQ"
        || /^MNQ(?:[FGHJKMNQUVXZ]\d+)?$/i.test(row.name)
      ));
      if (!contracts.length) {
        throw new Error("ProjectX returned no MNQ contracts for the configured connection.");
      }
      if (!contract) {
        throw new Error("ProjectX returned contracts, but no active MNQ contract is available.");
      }
      setConnection({
        scope,
        market: {
          contract_id: contract.id,
          symbol: contract.symbol_id ?? "MNQ",
          timeframe_unit: "minute",
          timeframe_unit_number: 5,
          lookback_bars: BOT_CHART_INITIAL_BARS,
        },
      });
    }).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Could not connect to ProjectX market data.");
      }
    }).finally(() => window.clearTimeout(timeout));
    return () => {
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [enabled]);

  if (enabled && connection) {
    return <BotSignalChart bot={null} market={connection.market} authenticatedCacheScope={connection.scope}
      activity={null} lastEvaluation={null} refreshToken={0} onMarketData={onMarketData} />;
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader className="mb-0 flex flex-wrap items-start justify-between gap-3 border-b border-app-border/70 pb-4">
        <div className="space-y-1">
          <CardTitle>Signal Chart</CardTitle>
          <CardDescription>MNQ · 5 minute · ProjectX</CardDescription>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full border border-app-border/80 px-2.5 py-1 text-[11px] font-medium text-app-muted">
          <span className={`h-1.5 w-1.5 rounded-full ${enabled && !error ? "animate-pulse bg-app-accent" : "bg-app-muted"}`} />
          {enabled ? error ? "Connection issue" : "Connecting" : "Disconnected"}
        </span>
      </CardHeader>
      <CardContent className="flex min-h-[270px] flex-col items-center justify-center space-y-0 px-1 py-7 text-center sm:px-6">
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-xl border border-app-border/80 bg-app-bg/50 text-app-muted">
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
            <path d="M4 4v16h16" />
            <path d="m7 14 4-4 4 3 5-7" />
          </svg>
        </div>
        <h3 className="text-sm font-semibold text-app-text">
          {enabled ? error ? "Market data is unavailable" : "Connecting your market feed" : "Your market workspace is ready"}
        </h3>
        {enabled ? error ? <>
          <p className="mt-2 max-w-lg text-xs leading-5 text-app-negative-text" role="alert">{error}</p>
          <p className="mt-2 max-w-lg text-xs leading-5 text-app-muted">Verify that the configured ProjectX username and API key belong to the intended TopstepX login and that it has market-data access. Then retry the connection.</p>
          <Button className="mt-5" variant="secondary" onClick={onRetry}>Retry chart connection</Button>
        </> : <>
          <p className="mt-2 text-xs leading-5 text-app-muted" role="status">Connecting to ProjectX market data…</p>
          <p className="mt-1 text-xs text-app-muted">No trading account required.</p>
        </> : <p className="mt-2 max-w-sm text-xs leading-5 text-app-muted">Market data is disconnected. Turn off Demo mode to connect to ProjectX.</p>}
      </CardContent>
    </Card>
  );
}
