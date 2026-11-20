import { useEffect } from "react";
import { streamProjectXMarketDepth } from "../../lib/api";
import type { OrderBookStore } from "./orderBook";
import { connectOrderBookPanelStream, type MarketDepthStreamFactory } from "./orderBookPanelStream";
import { useStreamVisibility } from "./useStreamVisibility";

export function useOrderBookStream(contractId: string | null, store: OrderBookStore, streamFactory: MarketDepthStreamFactory = streamProjectXMarketDepth) {
  const pageVisible = useStreamVisibility();
  useEffect(() => {
    if (!pageVisible || !contractId) {
      return undefined;
    }
    const close = connectOrderBookPanelStream({ contractId, store, streamFactory });
    return () => {
      close();
      store.cancelPendingPresentation();
      // A new subscription must establish a fresh snapshot before accepting deltas.
      store.setConnectionState({ contract_id: contractId, state: "disconnected" });
    };
  }, [pageVisible, contractId, store, streamFactory]);
}
