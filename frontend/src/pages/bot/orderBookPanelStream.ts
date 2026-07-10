import type { MarketDepthStreamCallbacks, MarketDepthStreamQuery } from "../../lib/api";
import { OrderBookStore } from "./orderBook";

export type MarketDepthStreamFactory = (
  query: MarketDepthStreamQuery,
  callbacks: MarketDepthStreamCallbacks,
) => () => void;

/**
 * Owns one panel stream generation. Store-detected gaps invalidate the current
 * generation and reopen custom factories so recovery always starts with a new
 * authoritative snapshot; late callbacks from the old generation are ignored.
 */
export function connectOrderBookPanelStream({
  contractId,
  store,
  streamFactory,
}: {
  contractId: string;
  store: OrderBookStore;
  streamFactory: MarketDepthStreamFactory;
}): () => void {
  let stopped = false;
  let generation = 0;
  let closeCurrent: (() => void) | undefined;
  let restartQueued = false;

  const open = () => {
    if (stopped) {
      return;
    }
    const streamGeneration = ++generation;
    const isCurrent = () => !stopped && generation === streamGeneration;
    const requestRestart = () => {
      if (!isCurrent() || restartQueued) {
        return;
      }
      restartQueued = true;
      queueMicrotask(() => {
        restartQueued = false;
        if (!isCurrent()) {
          return;
        }
        // Invalidate callbacks before close: a custom close handler may itself
        // synchronously emit a final event.
        generation += 1;
        const closePrevious = closeCurrent;
        closeCurrent = undefined;
        closePrevious?.();
        open();
      });
    };
    const callbacks: MarketDepthStreamCallbacks = {
      onState: (state) => {
        if (isCurrent()) {
          store.setConnectionState(state);
        }
      },
      onSnapshot: (snapshot) => {
        if (isCurrent()) {
          store.applySnapshot(snapshot);
        }
      },
      onUpdate: (update) => {
        if (isCurrent() && store.applyUpdateResult(update) === "gap") {
          requestRestart();
        }
      },
    };

    try {
      const close = streamFactory({ contractId }, callbacks);
      if (isCurrent()) {
        closeCurrent = close;
      } else {
        close();
      }
    } catch (error) {
      if (isCurrent()) {
        store.setConnectionState({
          contract_id: contractId,
          state: "unavailable",
          message: error instanceof Error ? error.message : "Market depth is unavailable.",
        });
      }
    }
  };

  open();
  return () => {
    stopped = true;
    generation += 1;
    const close = closeCurrent;
    closeCurrent = undefined;
    close?.();
  };
}
