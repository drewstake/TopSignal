import { useEffect, useState } from "react";
import { botsApi, getAuthenticatedCacheScope } from "../../lib/api";
import { BOT_CHART_INITIAL_BARS, type BotChartMarket } from "./botChartData";

export function useProjectXChartConnection(enabled: boolean, retry = 0) {
  const [connection, setConnection] = useState<{ market: BotChartMarket; scope: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      setError(null);
      setConnection({
        scope,
        market: {
          contract_id: contract.id,
          symbol: contract.symbol_id ?? "MNQ",
          tick_size: contract.tick_size,
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
  }, [enabled, retry]);

  return { connection, error };
}
