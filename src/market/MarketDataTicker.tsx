import { useEffect, useMemo, useRef, useState } from "react";
import { MarketDataService, type QuoteUpdate } from "./TopstepXMarketData";

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

export function MarketDataTicker() {
  const [quote, setQuote] = useState<QuoteUpdate>(initialQuote);
  const [status, setStatus] = useState<StatusState>({ mode: "connecting", message: null });
  const serviceRef = useRef<ReturnType<typeof MarketDataService.init> | null>(null);

  const statusLabel = useMemo(() => {
    if (status.mode === "live") return "Live";
    if (status.mode === "connecting") return "Connecting";
    if (status.mode === "error") return "Unavailable";
    return "Idle";
  }, [status.mode]);

  useEffect(() => {
    const md = MarketDataService.init({ symbol: "MNQ", levels: 10, throttleMs: 150 });
    serviceRef.current = md;

    const unsubscribeQuote = md.onQuote((next) => {
      setQuote(next);
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
      unsubscribeStatus();
      void md.stop();
      serviceRef.current = null;
    };
  }, []);

  const bestBid = formatNumber(quote.bestBid);
  const bestAsk = formatNumber(quote.bestAsk);
  const last = formatNumber(quote.last);
  const spread = formatNumber(quote.spread ?? null);
  const volume = formatVolume(quote.volume);
  const timestamp = quote.ts ? new Date(quote.ts).toISOString() : "--";

  return (
    <div className="rounded-2xl border border-emerald-900/50 bg-emerald-950/40 p-4 text-sm text-emerald-100">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-emerald-300">MNQ Market Data</div>
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
      {status.mode === "error" && status.message ? (
        <div className="mt-2 rounded-lg border border-amber-700/60 bg-amber-900/30 px-3 py-2 text-xs text-amber-100">
          {status.message}
        </div>
      ) : null}
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
