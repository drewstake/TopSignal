import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";

import { Card, CardTitle } from "../../components/ui/Card";
import { streamProjectXMarketDepth } from "../../lib/api";
import {
  normalizeOrderBookContractId,
  OrderBookStore,
  type OrderBookConnectionState,
  type OrderBookLevelView,
  type OrderBookSide,
} from "./orderBook";
import { connectOrderBookPanelStream, type MarketDepthStreamFactory } from "./orderBookPanelStream";
import { usePageVisibility } from "./usePageVisibility";

const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 8,
});
const sizeFormatter = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

export interface OrderBookPanelProps {
  contractId: string | null | undefined;
  symbol?: string | null;
  demoMode?: boolean;
  streamFactory?: MarketDepthStreamFactory;
}

export function OrderBookPanel({
  contractId,
  symbol,
  demoMode = false,
  streamFactory = streamProjectXMarketDepth,
}: OrderBookPanelProps) {
  const pageVisible = usePageVisibility();
  const normalizedContractId = normalizeOrderBookContractId(contractId);
  const store = useMemo(() => new OrderBookStore(normalizedContractId, 1), [normalizedContractId]);

  useEffect(() => {
    if (demoMode || !pageVisible || !normalizedContractId) {
      return undefined;
    }
    const close = connectOrderBookPanelStream({ contractId: normalizedContractId, store, streamFactory });
    return () => {
      close();
      // A new subscription must establish a fresh snapshot before accepting deltas.
      store.setConnectionState({ contract_id: normalizedContractId, state: "disconnected" });
    };
  }, [demoMode, pageVisible, normalizedContractId, store, streamFactory]);

  const displaySymbol = symbol?.trim();
  const displayMarket =
    displaySymbol && normalizedContractId
      ? `${displaySymbol} · ${normalizedContractId}`
      : displaySymbol || normalizedContractId || "No contract selected";

  return (
    <Card aria-label="Order book" className="!p-3 md:!p-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <header className="flex items-center gap-2" title={`${displayMarket} · Level 1 best bid and ask`}>
          <CardTitle className="md:!text-sm">Order Book</CardTitle>
          {displaySymbol ? <span className="text-xs text-app-muted">{displaySymbol.split(".").at(-1)}</span> : null}
          {demoMode ? (
            <span className="text-[10px] text-app-muted">
              Demo · stream off
            </span>
          ) : <OrderBookConnectionStatus store={store} />}
        </header>
        {demoMode ? (
          <p className="text-xs text-app-muted" role="note">Live market depth is paused in Demo Mode</p>
        ) : (
        <div
          className="grid w-full min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 font-mono text-xs sm:w-auto sm:flex-1"
          aria-label={`${displayMarket} best bid and ask`}
        >
          <div data-order-book-side="bids">
            <OrderBookLevelRow store={store} side="bid" index={0} />
          </div>
          <OrderBookSpread store={store} />
          <div data-order-book-side="asks">
            <OrderBookLevelRow store={store} side="ask" index={0} />
          </div>
        </div>
        )}
      </div>
      {!demoMode ? <OrderBookStateOverlay store={store} /> : null}
    </Card>
  );
}

function OrderBookConnectionStatus({ store }: { store: OrderBookStore }) {
  const meta = useSyncExternalStore(store.subscribeMeta, store.getMetaSnapshot, store.getMetaSnapshot);
  const label = connectionLabel(meta.connection);
  const tone = connectionTone(meta.connection);
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[10px] ${tone}`}
      title={meta.message ? `${label}: ${meta.message}` : label}
      aria-live="polite"
      aria-atomic="true"
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${meta.connection === "connected" ? "animate-pulse bg-app-positive" : "bg-current opacity-70"}`}
      />
      <span className={meta.connection === "connected" ? "sr-only" : undefined}>{label}</span>
    </span>
  );
}

const OrderBookLevelRow = memo(function OrderBookLevelRow({
  store,
  side,
  index,
}: {
  store: OrderBookStore;
  side: OrderBookSide;
  index: number;
}) {
  const subscribe = useCallback(
    (listener: () => void) =>
      side === "ask" ? store.subscribeAskSlot(index, listener) : store.subscribeBidSlot(index, listener),
    [index, side, store],
  );
  const getSnapshot = useCallback(
    () => (side === "ask" ? store.getAskSlotSnapshot(index) : store.getBidSlotSnapshot(index)),
    [index, side, store],
  );
  const level = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return <OrderBookLevel side={side} level={level} />;
});

function OrderBookLevel({ side, level }: { side: OrderBookSide; level: OrderBookLevelView | null }) {
  const sideTone = side === "ask" ? "text-app-negative" : "text-app-positive";
  const bestTone =
    side === "ask"
      ? "border-app-negative/45 bg-app-negative/10"
      : "border-app-positive/45 bg-app-positive/10";
  return (
    <div
      className={`relative flex min-h-8 flex-wrap items-center justify-center gap-x-2 gap-y-0.5 overflow-hidden rounded-md border px-2 py-1 tabular-nums ${
        level?.isBest ? bestTone : "border-app-border/35"
      }`}
      data-price={level?.price}
      data-size={level?.size}
      aria-label={
        level
          ? `${level.isBest ? "Best " : ""}${side} ${priceFormatter.format(level.price)}, aggregate size ${sizeFormatter.format(level.size)}`
          : undefined
      }
    >
      {level ? (
        <span
          className={`pointer-events-none absolute inset-y-0 ${side === "ask" ? "right-0 bg-app-negative/15" : "left-0 bg-app-positive/15"}`}
          style={{ width: `${Math.max(0, Math.min(100, level.depthPercent))}%` }}
          data-depth-percent={level.depthPercent.toFixed(4)}
          aria-hidden="true"
        />
      ) : null}
      <span className={`relative z-10 font-sans text-[10px] uppercase ${sideTone}`}>{side}</span>
      <span className={`relative z-10 font-semibold ${level ? sideTone : "text-app-muted-strong"}`}>
        {level ? priceFormatter.format(level.price) : "—"}
      </span>
      <span className="relative z-10 text-[10px] text-app-muted" title="Size (contracts)">
        ×{level ? sizeFormatter.format(level.size) : "—"}
      </span>
    </div>
  );
}

function OrderBookSpread({
  store,
}: {
  store: OrderBookStore;
}) {
  const spread = useSyncExternalStore(store.subscribeSpread, store.getSpreadSnapshot, store.getSpreadSnapshot);

  return (
    <div
      className="flex flex-col items-center justify-center px-1 font-sans text-[10px] leading-tight text-app-muted sm:flex-row sm:gap-1.5"
      aria-live="polite"
      aria-label={spread.spread === null ? "Spread unavailable" : `Spread ${priceFormatter.format(spread.spread)}`}
    >
      <span>Spread</span>
      <span className="font-mono font-semibold tabular-nums">
        {spread.spread === null ? "—" : priceFormatter.format(spread.spread)}
      </span>
    </div>
  );
}

function OrderBookStateOverlay({ store }: { store: OrderBookStore }) {
  const meta = useSyncExternalStore(store.subscribeMeta, store.getMetaSnapshot, store.getMetaSnapshot);
  const message = overlayMessage(meta.connection, meta.hasSnapshot, meta.hasDepth, meta.message);
  if (!message) {
    return null;
  }
  const isUnavailable = meta.connection === "unavailable";
  return (
    <div
      className={`mt-2 rounded-md border px-2 py-1 text-center font-sans text-xs ${
        isUnavailable
          ? "border-app-negative/35 bg-app-bg/90 text-app-negative"
          : "border-app-border bg-app-bg/90 text-app-muted"
      }`}
      role={isUnavailable ? "alert" : "status"}
      aria-busy={meta.connection === "loading" || meta.connection === "reconnecting" || undefined}
    >
      {message}
    </div>
  );
}

function connectionLabel(connection: OrderBookConnectionState): string {
  switch (connection) {
    case "connected":
      return "Connected";
    case "disconnected":
      return "Disconnected";
    case "reconnecting":
      return "Reconnecting";
    case "market_closed":
      return "Market closed";
    case "unavailable":
      return "Unavailable";
    default:
      return "Loading";
  }
}

function connectionTone(connection: OrderBookConnectionState): string {
  switch (connection) {
    case "connected":
      return "border-app-positive/35 bg-app-positive/10 text-app-positive";
    case "disconnected":
      return "border-app-negative/35 bg-app-negative/10 text-app-negative";
    case "reconnecting":
      return "border-app-warning/35 bg-app-warning/10 text-app-warning";
    case "unavailable":
    case "market_closed":
      return "border-app-border bg-app-bg/55 text-app-muted";
    default:
      return "border-app-accent/35 bg-app-accent/10 text-app-accent";
  }
}

function overlayMessage(
  connection: OrderBookConnectionState,
  hasSnapshot: boolean,
  hasDepth: boolean,
  message: string | null,
): string | null {
  if (connection === "market_closed") {
    return message || "Market closed. Order book updates resume automatically when the trading session opens.";
  }
  if (connection === "unavailable") {
    return message || "Market depth is unavailable.";
  }
  if (connection === "loading") {
    return "Loading order book…";
  }
  if (connection === "reconnecting" && !hasDepth) {
    return "Reconnecting market depth…";
  }
  if (connection === "disconnected" && !hasDepth) {
    return message || "Market depth disconnected.";
  }
  if (!hasSnapshot) {
    return "Waiting for the initial depth snapshot…";
  }
  if (!hasDepth) {
    return "No market depth levels are currently available.";
  }
  return null;
}
