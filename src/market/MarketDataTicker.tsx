import { useEffect, useMemo, useRef, useState } from "react";
import {
  MarketDataService,
  type DepthSnapshot,
  type QuoteUpdate,
} from "./TopstepXMarketData";

const numberFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatNumber(value: number | null) {
  if (value === null) return "--";
  return numberFormatter.format(value);
}

function formatVolume(value: number | null) {
  if (value === null) return "--";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return `${Math.round(value)}`;
}

type Status = "idle" | "connecting" | "live" | "error";

type StatusState = {
  mode: Status;
  message: string | null;
};

const initialQuote: QuoteUpdate = {
  last: null,
  bestBid: null,
  bestAsk: null,
  spread: null,
  volume: null,
  ts: null,
};

const initialDepth: DepthSnapshot = { bids: [], asks: [] };

type MarketDataTickerProps = {
  symbol?: string;
  label?: string;
  onQuote?: (quote: QuoteUpdate) => void;
};

export function MarketDataTicker({ symbol = "MNQ", label, onQuote }: MarketDataTickerProps) {
  const [quote, setQuote] = useState<QuoteUpdate>(initialQuote);
  const [status, setStatus] = useState<StatusState>({ mode: "connecting", message: null });
  const [depth, setDepth] = useState<DepthSnapshot>(initialDepth);
  const serviceRef = useRef<ReturnType<typeof MarketDataService.init> | null>(null);

  const statusLabel = useMemo(() => {
    if (status.mode === "live") return "Live";
    if (status.mode === "connecting") return "Connecting";
    if (status.mode === "error") return "Unavailable";
    return "Idle";
  }, [status.mode]);

  useEffect(() => {
    setQuote(initialQuote);
    setDepth(initialDepth);
    setStatus({ mode: "connecting", message: null });

    const md = MarketDataService.init({ symbol, levels: 10, throttleMs: 150 });
    serviceRef.current?.stop();
    serviceRef.current = md;

    const unsubscribeQuote = md.onQuote((next) => {
      setQuote(next);
      onQuote?.(next);
    });

    const unsubscribeDepth = md.onDepth((snapshot) => {
      setDepth(snapshot);
    });

    const unsubscribeStatus = md.onStatus((connected) => {
      setStatus((prev) => ({
        mode: connected ? "live" : prev.mode === "error" ? "error" : "connecting",
        message: prev.mode === "error" ? prev.message : null,
      }));
    });

    md
      .start()
      .then(() => {
        setStatus({ mode: md.isConnected() ? "live" : "connecting", message: null });
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : "Failed to start market data.";
        console.error("Market data failed to start", err);
        setStatus({ mode: "error", message });
      });

    return () => {
      unsubscribeQuote();
      unsubscribeDepth();
      unsubscribeStatus();
      void md.stop();
      serviceRef.current = null;
    };
  }, [symbol, onQuote]);

  const bestBid = formatNumber(quote.bestBid);
  const bestAsk = formatNumber(quote.bestAsk);
  const last = formatNumber(quote.last);
  const spread = formatNumber(quote.spread ?? null);
  const volume = formatVolume(quote.volume);
  const timestamp = quote.ts ? new Date(quote.ts).toISOString() : "--";
  const displaySymbol = (label || symbol).toUpperCase();

  return (
    <div className="rounded-2xl border border-emerald-900/50 bg-emerald-950/40 p-4 text-sm text-emerald-100">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-emerald-300">{displaySymbol} Market Data</div>
          <div className="mt-1 text-2xl font-semibold text-emerald-100">{last}</div>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-emerald-800 bg-emerald-900/50 px-3 py-1 text-xs font-semibold text-emerald-200">
          <span
            className={
              "inline-block h-2 w-2 rounded-full " +
              (status.mode === "live"
                ? "bg-emerald-400"
                : status.mode === "error"
                ? "bg-amber-400"
                : "bg-emerald-300/70")
            }
          ></span>
          <span>{statusLabel}</span>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-xs text-emerald-200 sm:grid-cols-4">
        <Stat label="Best Bid" value={bestBid} />
        <Stat label="Best Ask" value={bestAsk} />
        <Stat label="Spread" value={spread} />
        <Stat label="Volume" value={volume} />
      </div>

      <div className="mt-2 text-[11px] text-emerald-300/80">Updated: {timestamp}</div>
      <OrderBook depth={depth} />
      {status.mode === "error" && status.message ? (
        <div className="mt-2 rounded-lg border border-amber-700/60 bg-amber-900/30 px-3 py-2 text-xs text-amber-100">
          {status.message}
        </div>
      ) : null}
    </div>
  );
}

function DepthRow({ side, price, size }: { side: "bid" | "ask"; price: number; size: number }) {
  const formattedPrice = formatNumber(price);
  const formattedSize = formatVolume(size);
  const isBid = side === "bid";
  return (
    <div className="grid grid-cols-2 items-center text-xs font-medium">
      <div
        className={
          "flex items-center gap-2 rounded-lg px-2 py-1 " +
          (isBid ? "bg-emerald-900/40 text-emerald-100" : "bg-emerald-950/40 text-emerald-50")
        }
      >
        <span className="inline-flex h-2 w-2 rounded-full bg-emerald-500" aria-hidden />
        <span>{formattedPrice}</span>
      </div>
      <div className="text-right text-emerald-200">{formattedSize}</div>
    </div>
  );
}

function OrderBook({ depth }: { depth: DepthSnapshot }) {
  const maxRows = 8;
  const bids = depth.bids.slice(0, maxRows);
  const asks = depth.asks.slice(0, maxRows);

  const hasData = bids.length > 0 || asks.length > 0;

  const { bidPct, askPct, label } = useMemo(() => {
    const topBids = depth.bids.slice(0, 10).reduce((sum, level) => sum + level.size, 0);
    const topAsks = depth.asks.slice(0, 10).reduce((sum, level) => sum + level.size, 0);
    const total = topBids + topAsks;

    if (total <= 0) {
      return { bidPct: null, askPct: null, label: "--" };
    }

    const bidPct = Math.round((topBids / total) * 100);
    const askPct = 100 - bidPct;
    const label = `Top-10 imbalance: ${bidPct}% bid / ${askPct}% ask`;

    return { bidPct, askPct, label };
  }, [depth]);

  return (
    <div className="mt-4 rounded-xl border border-emerald-900/60 bg-emerald-900/30 p-3">
      <div className="mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-emerald-300/90">
        <span>Order Book</span>
        <div className="flex flex-col items-end gap-1 text-[11px] text-emerald-200/80 sm:flex-row sm:items-center sm:gap-2">
          <span className="text-emerald-200/80">Top {maxRows} levels</span>
          <span className="rounded-full border border-emerald-800/70 bg-emerald-900/50 px-2 py-0.5 font-medium text-emerald-100">
            {label}
          </span>
        </div>
      </div>
      {bidPct !== null && askPct !== null ? (
        <div className="mb-3">
          <div className="flex items-center justify-between text-[11px] text-emerald-200/80">
            <span>Bid {bidPct}%</span>
            <span>Ask {askPct}%</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-emerald-900/60">
            <div
              className="h-full bg-emerald-500"
              style={{ width: `${bidPct}%` }}
              aria-label={`Bid depth ${bidPct}%`}
            />
          </div>
        </div>
      ) : null}
      {hasData ? (
        <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
          <div className="space-y-1">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-200/80">Bids</div>
            <div className="space-y-1">
              {bids.map((b) => (
                <DepthRow key={`bid-${b.price}`} side="bid" price={b.price} size={b.size} />
              ))}
            </div>
          </div>
          <div className="space-y-1">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-emerald-200/80">Asks</div>
            <div className="space-y-1">
              {asks.map((a) => (
                <DepthRow key={`ask-${a.price}`} side="ask" price={a.price} size={a.size} />
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="text-xs text-emerald-200/80">Waiting for order book data…</div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-emerald-900/40 bg-emerald-900/30 p-3">
      <div className="text-[11px] uppercase tracking-wide text-emerald-300/80">{label}</div>
      <div className="mt-1 text-lg font-semibold text-emerald-100">{value}</div>
    </div>
  );
}

export default MarketDataTicker;
