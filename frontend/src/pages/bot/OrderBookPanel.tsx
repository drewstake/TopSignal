import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type RefObject,
} from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Select } from "../../components/ui/Select";
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
  const normalizedContractId = normalizeOrderBookContractId(contractId);
  const store = useMemo(() => new OrderBookStore(normalizedContractId), [normalizedContractId]);
  const [visibleLevelCount, setVisibleLevelCount] = useState<OrderBookLevelCount>(20);
  const ladderRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    store.setVisibleLevelCount(visibleLevelCount);
  }, [store, visibleLevelCount]);

  useEffect(() => {
    if (demoMode || !normalizedContractId) {
      return undefined;
    }
    return connectOrderBookPanelStream({ contractId: normalizedContractId, store, streamFactory });
  }, [demoMode, normalizedContractId, store, streamFactory]);

  const rowIndexes = useMemo(
    () => Array.from({ length: visibleLevelCount }, (_, index) => index),
    [visibleLevelCount],
  );
  const displaySymbol = symbol?.trim();
  const displayMarket =
    displaySymbol && normalizedContractId
      ? `${displaySymbol} · ${normalizedContractId}`
      : displaySymbol || normalizedContractId || "No contract selected";

  return (
    <Card aria-label="Order book">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>Order Book</CardTitle>
            <CardDescription className="truncate" title={normalizedContractId ?? undefined}>
              {displayMarket} · aggregate size by price
            </CardDescription>
          </div>
          {demoMode ? (
            <span className="inline-flex min-h-7 items-center rounded-md border border-app-accent/35 bg-app-accent/10 px-2 text-[11px] font-semibold text-app-accent">
              Demo · stream off
            </span>
          ) : <OrderBookConnectionStatus store={store} />}
        </div>
        {!demoMode ? <p className="text-xs text-app-muted">
          Available levels depend on your data subscription. Level 1 shows best bid/ask; Level 2 supplies market depth.
        </p> : null}
        {!demoMode ? <label className="flex items-center justify-between gap-3 text-xs text-app-muted">
          <span>Levels / side</span>
          <Select
            className="h-11 w-24 py-0 text-xs sm:h-9"
            aria-label="Visible order book levels per side"
            value={visibleLevelCount}
            onChange={(event) => {
              const next = Number.parseInt(event.target.value, 10);
              if (next === 10 || next === 20 || next === 50) {
                store.setVisibleLevelCount(next);
                setVisibleLevelCount(next);
              }
            }}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
          </Select>
        </label> : null}
      </CardHeader>
      <CardContent className="relative">
        {demoMode ? (
          <div className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed border-app-accent/35 bg-app-accent/5 px-6 text-center" role="note">
            <p className="text-sm font-semibold text-app-text">Live market depth is paused in Demo Mode</p>
            <p className="mt-2 max-w-lg text-xs leading-5 text-app-muted">
              TopSignal does not open a ProjectX depth stream while sample data is active. Return to live mode to view bid and ask liquidity.
            </p>
          </div>
        ) : (
        <>
        <div
          ref={ladderRef}
          className="relative h-[430px] overflow-y-auto rounded-xl border border-app-border bg-app-bg/35 font-mono text-xs [scrollbar-color:rgb(var(--theme-border-strong))_transparent]"
          aria-label={`${displayMarket} market depth`}
        >
          <OrderBookSectionHeader side="ask" />
          <div className="flex flex-col-reverse" data-order-book-side="asks">
            {rowIndexes.map((index) => (
              <OrderBookLevelRow key={`ask:${index}`} store={store} side="ask" index={index} />
            ))}
          </div>
          <OrderBookSpread store={store} ladderRef={ladderRef} layoutToken={visibleLevelCount} />
          <OrderBookSectionHeader side="bid" />
          <div data-order-book-side="bids">
            {rowIndexes.map((index) => (
              <OrderBookLevelRow key={`bid:${index}`} store={store} side="bid" index={index} />
            ))}
          </div>
        </div>
        <OrderBookStateOverlay store={store} />
        </>
        )}
      </CardContent>
    </Card>
  );
}

function OrderBookConnectionStatus({ store }: { store: OrderBookStore }) {
  const meta = useSyncExternalStore(store.subscribeMeta, store.getMetaSnapshot, store.getMetaSnapshot);
  const label = connectionLabel(meta.connection);
  const tone = connectionTone(meta.connection);
  return (
    <span
      className={`inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-[11px] ${tone}`}
      title={meta.message ?? undefined}
      aria-live="polite"
      aria-atomic="true"
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${meta.connection === "connected" ? "animate-pulse bg-app-positive" : "bg-current opacity-70"}`}
      />
      {label}
    </span>
  );
}

function OrderBookSectionHeader({ side }: { side: OrderBookSide }) {
  return (
    <div
      className="sticky top-0 z-20 grid grid-cols-[minmax(0,1fr)_5rem] border-b border-app-border bg-app-surface/95 px-3 py-1.5 text-[10px] uppercase tracking-wide text-app-muted backdrop-blur"
      aria-hidden="true"
    >
      <span>{side === "ask" ? "Ask price" : "Bid price"}</span>
      <span className="text-right">Size</span>
    </div>
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
      className={`relative grid h-7 grid-cols-[minmax(0,1fr)_5rem] items-center overflow-hidden border-b px-3 tabular-nums ${
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
      <span className={`relative z-10 ${level ? sideTone : "text-app-muted-strong"}`}>
        {level ? priceFormatter.format(level.price) : "—"}
      </span>
      <span className="relative z-10 text-right text-app-text-soft">
        {level ? sizeFormatter.format(level.size) : "—"}
      </span>
    </div>
  );
}

function OrderBookSpread({
  store,
  ladderRef,
  layoutToken,
}: {
  store: OrderBookStore;
  ladderRef: RefObject<HTMLDivElement | null>;
  layoutToken: OrderBookLevelCount;
}) {
  const spread = useSyncExternalStore(store.subscribeSpread, store.getSpreadSnapshot, store.getSpreadSnapshot);
  const spreadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ladder = ladderRef.current;
    const marker = spreadRef.current;
    if (!ladder || !marker || spread.bestAsk === null || spread.bestBid === null) {
      return;
    }
    ladder.scrollTop = Math.max(0, marker.offsetTop - (ladder.clientHeight - marker.offsetHeight) / 2);
  }, [ladderRef, layoutToken, spread.bestAsk, spread.bestBid]);

  return (
    <div
      ref={spreadRef}
      className="sticky z-20 flex h-9 items-center justify-center gap-2 border-y border-app-accent/30 bg-app-accent/10 px-3 font-sans text-[11px] text-app-accent backdrop-blur"
      aria-live="polite"
      aria-label={spread.spread === null ? "Spread unavailable" : `Spread ${priceFormatter.format(spread.spread)}`}
    >
      <span className="uppercase tracking-wide text-app-muted">Spread</span>
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
      className={`absolute inset-x-3 top-1/2 z-30 -translate-y-1/2 rounded-lg border px-3 py-2 text-center font-sans text-xs backdrop-blur ${
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
