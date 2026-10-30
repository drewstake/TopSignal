import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import { Card, CardTitle } from "../../components/ui/Card";
import { streamProjectXMarketDepth } from "../../lib/api";
import {
  normalizeOrderBookContractId,
  OrderBookStore,
  type OrderBookConnectionState,
  type OrderBookLevelCount,
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
  const store = useMemo(() => new OrderBookStore(normalizedContractId, "all"), [normalizedContractId]);

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
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <header className="flex flex-wrap items-center gap-2" title={`${displayMarket} · Level 2 market depth`}>
          <CardTitle className="md:!text-sm">Order Book</CardTitle>
          {displaySymbol ? <span className="text-xs text-app-muted">{displaySymbol.split(".").at(-1)}</span> : null}
          {demoMode ? (
            <span className="text-[10px] text-app-muted">
              Demo · stream off
            </span>
          ) : <OrderBookConnectionStatus store={store} />}
        </header>
        {!demoMode ? <OrderBookSpread store={store} /> : null}
      </div>
      {demoMode ? (
        <p className="mt-2 text-xs text-app-muted" role="note">Live market depth is paused in Demo Mode</p>
      ) : <OrderBookDepth key={normalizedContractId} store={store} displayMarket={displayMarket} />}
      {!demoMode ? <OrderBookStateOverlay store={store} /> : null}
    </Card>
  );
}

function OrderBookDepth({ store, displayMarket }: { store: OrderBookStore; displayMarket: string }) {
  const [levelCount, setLevelCount] = useState<OrderBookLevelCount>("all");
  const depth = useSyncExternalStore(store.subscribeDepth, store.getDepthSnapshot, store.getDepthSnapshot);
  const countLabel = (visible: number, total: number) =>
    visible < total ? `${visible} of ${total}` : String(total);

  return (
    <>
      <div className="my-3 flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
        <span>Depth of market · Size in contracts</span>
        <label className="flex items-center gap-2">
          Levels per side
          <select
            aria-label="Visible order book levels per side"
            className="rounded-md border border-app-border bg-app-bg px-2 py-1 text-xs text-app-text focus-visible:outline focus-visible:outline-2 focus-visible:outline-app-accent"
            value={levelCount}
            onChange={(event) => {
              const value = event.target.value;
              const next = value === "all" ? "all" : Number(value);
              if (next !== "all" && next !== 10 && next !== 20 && next !== 50) return;
              store.setVisibleLevelCount(next);
              setLevelCount(next);
            }}
          >
            <option value="all">All</option>
            <option value="10">10</option>
            <option value="20">20</option>
            <option value="50">50</option>
          </select>
        </label>
      </div>
      <div className="grid grid-cols-2 gap-3 border-b border-app-border pb-2 text-xs">
        <div className="flex items-center justify-between gap-2 text-app-positive">
          <span>Bids <span className="text-app-muted">({countLabel(depth.visibleBidCount, depth.bidCount)})</span></span>
          <span className="text-[10px] text-app-muted">Price</span>
        </div>
        <div className="flex items-center justify-between gap-2 text-app-negative">
          <span className="text-[10px] text-app-muted">Price</span>
          <span>Asks <span className="text-app-muted">({countLabel(depth.visibleAskCount, depth.askCount)})</span></span>
        </div>
      </div>
      <div
        className="mt-2 grid max-h-80 grid-cols-2 gap-3 overflow-y-auto overscroll-contain rounded-md font-mono text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-app-accent"
        role="region"
        aria-label={`${displayMarket} market depth ladder`}
        tabIndex={0}
      >
        <div data-order-book-side="bids" className="min-w-0 space-y-1">
          {Array.from({ length: Math.max(1, depth.visibleBidCount) }, (_, index) => (
            <OrderBookLevelRow key={index} store={store} side="bid" index={index} />
          ))}
        </div>
        <div data-order-book-side="asks" className="min-w-0 space-y-1">
          {Array.from({ length: Math.max(1, depth.visibleAskCount) }, (_, index) => (
            <OrderBookLevelRow key={index} store={store} side="ask" index={index} />
          ))}
        </div>
      </div>
      <p className="mt-2 text-[11px] text-app-muted">
        {levelCount === "all" ? "All received price levels" : `Nearest ${levelCount} price levels per side`}
        {" · Best prices at the top"}
      </p>
    </>
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
      className={`relative flex min-h-7 items-center justify-between gap-1 overflow-hidden rounded-md border px-2 py-1 tabular-nums ${
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
      <span className={`relative z-10 ${side === "bid" ? "order-2" : "order-1"} ${level ? sideTone : "text-app-muted-strong"}`}>
        {level ? priceFormatter.format(level.price) : "—"}
      </span>
      <span className={`relative z-10 font-semibold ${side === "bid" ? "order-1" : "order-2"} ${sideTone}`} title="Size (contracts)">
        {level ? sizeFormatter.format(level.size) : "—"}
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
  if (connection === "reconnecting") {
    return hasDepth ? "Reconnecting market depth… Showing last received depth." : "Reconnecting market depth…";
  }
  if (connection === "disconnected") {
    return hasDepth ? "Market depth disconnected. Showing last received depth." : message || "Market depth disconnected.";
  }
  if (!hasSnapshot) {
    return "Waiting for the initial depth snapshot…";
  }
  if (!hasDepth) {
    return "No market depth levels are currently available.";
  }
  return null;
}
