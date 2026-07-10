import type { EvaluationOverlayModel } from "./botEvaluationOverlay";

const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
});

export function BotEvaluationOverlayStatus({ model }: { model: EvaluationOverlayModel | null }) {
  if (!model) {
    return null;
  }

  const evaluatedAt = formatTimestamp(model.evaluatedAt);
  const candleTimestamp = formatTimestamp(model.timestamp);
  const freshnessLabel =
    model.staleness.status === "stale"
      ? `Stale${model.staleness.barsBehind === null ? "" : ` · ${model.staleness.barsBehind} bars behind`}`
      : model.staleness.status === "fresh"
        ? "Current"
        : "Freshness unknown";
  const freshnessClassName =
    model.staleness.status === "stale"
      ? "font-semibold text-app-warning"
      : model.staleness.status === "unknown"
        ? "font-semibold text-app-muted-strong"
        : "text-app-muted";
  return (
    <div
      className="pointer-events-none absolute right-3 top-20 z-20 max-w-[calc(100%-1.5rem)] rounded-md border border-app-border/85 bg-app-bg/90 px-2.5 py-2 text-[11px] shadow-lg shadow-app-bg/30 backdrop-blur"
      role="note"
      aria-label="Latest actionable evaluation overlay"
    >
      <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1">
        <span className={model.action === "BUY" ? "font-bold text-app-positive" : "font-bold text-app-negative"}>
          Latest {model.action}
        </span>
        <span className={freshnessClassName}>{freshnessLabel}</span>
        <span className="text-app-muted">
          {evaluatedAt ? `Evaluated ${evaluatedAt} ET` : "Evaluation time unavailable"}
        </span>
        <span className="text-app-muted">
          {candleTimestamp ? `Candle ${candleTimestamp} ET` : "Candle time unavailable"}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center justify-end gap-x-3 gap-y-1 tabular-nums">
        {model.geometry.entry ? <LevelLabel label="Entry" price={model.geometry.entry.price} className="text-app-accent" /> : null}
        {model.geometry.stop ? <LevelLabel label="Stop" price={model.geometry.stop.price} className="text-app-negative" /> : null}
        {model.geometry.target ? <LevelLabel label="Target" price={model.geometry.target.price} className="text-app-positive" /> : null}
        {model.riskRewardRatio !== null ? (
          <span className="font-semibold text-app-text-soft">R:R 1:{model.riskRewardRatio.toFixed(2)}</span>
        ) : null}
      </div>
    </div>
  );
}

function formatTimestamp(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const timestamp = new Date(value);
  return Number.isFinite(timestamp.getTime()) ? timestampFormatter.format(timestamp) : null;
}

function LevelLabel({ label, price, className }: { label: string; price: number; className: string }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-app-muted">{label}</span>
      <span className={`font-semibold ${className}`}>{priceFormatter.format(price)}</span>
    </span>
  );
}
