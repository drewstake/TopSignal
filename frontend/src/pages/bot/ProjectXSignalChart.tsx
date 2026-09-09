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
    <Card>
      <CardHeader>
        <CardTitle>Signal Chart</CardTitle>
        <CardDescription>MNQ · ProjectX market data · No trading account required</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {enabled ? error ? <>
          <p className="text-sm text-app-negative-text" role="alert">{error}</p>
          <p className="text-sm leading-6 text-app-muted">Verify that the configured ProjectX username and API key belong to the intended TopstepX login and that it has market-data access. Then retry the connection.</p>
          <Button variant="secondary" onClick={onRetry}>Retry chart connection</Button>
        </> : <p className="text-sm text-app-muted" role="status">Connecting to ProjectX market data…</p>
          : <p className="text-sm text-app-muted">Market data is disconnected. Turn off Demo mode to connect to ProjectX.</p>}
      </CardContent>
    </Card>
  );
}
