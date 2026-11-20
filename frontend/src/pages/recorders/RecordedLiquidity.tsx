import { useMemo, useState } from "react";
import { Button } from "../../components/ui/Button";
import { ServerClock } from "../bot/heatmapClock";
import { HistoryView } from "../bot/history/BotLiquidityHistory";
import { LiquidityHeatmapPanel } from "../bot/LiquidityHeatmapPanel";
import { normalizeOrderBookContractId, OrderBookStore } from "../bot/orderBook";
import { useOrderBookStream } from "../bot/useOrderBookStream";
import { useProjectXChartConnection } from "../bot/useProjectXChartConnection";

export function RecordedLiquidity({ accountId, recordedContract }: { accountId: number; recordedContract?: string }) {
  const [clock] = useState(() => new ServerClock());
  const [retry, setRetry] = useState(0);
  const studyContract = normalizeOrderBookContractId(recordedContract);
  const { connection, error } = useProjectXChartConnection(!studyContract, retry);
  const market = connection?.market;
  const contract = studyContract ?? market?.contract_id ?? null;
  const store = useMemo(() => new OrderBookStore(contract, "all", 100), [contract]);
  useOrderBookStream(contract, store);

  return <>
    {!contract && error && <p role="status">{error} <Button size="sm" variant="ghost"
      onClick={() => setRetry(value => value + 1)}>Retry live contract</Button></p>}
    <LiquidityHeatmapPanel store={store} contractId={contract} accountId={accountId} clock={clock}
      symbol={market?.contract_id === contract ? market.symbol : undefined}
      tickSize={market?.contract_id === contract ? market.tick_size : undefined} />
    <section aria-label="Recorded liquidity history" className="space-y-4">
      <HistoryView accountId={accountId} compact liveStore={store} clock={clock} />
    </section>
  </>;
}
