import type { MarketContext } from "./botMarketContext";

const price = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function regimeMeaning(regime: string, direction: string): string {
  const directional = direction === "bullish" || direction === "bearish";
  if (regime === "chop") return directional
    ? `Choppy conditions with a ${direction} tilt. Direction describes the drift; choppiness means it is not a clean sustained trend. A level crossing needs follow-through.`
    : "Choppy conditions: directional readings are weak or mixed. A brief move beyond a level may fail to persist.";
  if (regime === "quiet") return "Quiet conditions: recent ranges are contracting. Smaller ranges do not establish a breakout direction.";
  if (regime === "volatile") return "Volatile conditions: recent ranges are expanding. Wider swings make a single level crossing less informative.";
  if (regime === "range") return "Range conditions: these indicators do not establish a sustained direction. Watch whether closes hold beyond the range.";
  if (regime === "trend") return "Trending conditions: the directional indicators show sustained movement. This describes recent candles, not a forecast.";
  return "Market regime unavailable: more complete history is needed to distinguish directional movement from noise.";
}

export function levelDistance(level: number | null, current: number | null, atr: number | null): string {
  if (level == null || current == null || !Number.isFinite(level) || !Number.isFinite(current)) return "Distance unavailable";
  const delta = Math.abs(level - current);
  if (delta === 0) return "At the last closed price";
  const percent = current !== 0 ? delta / Math.abs(current) * 100 : null;
  const atrUnits = atr != null && atr > 0 && Number.isFinite(atr) ? delta / atr : null;
  return `${delta < 0.01 ? "<0.01" : price.format(delta)} pts ${level > current ? "above" : "below"}${percent != null ? ` · ${percent < 0.01 ? "<0.01" : percent.toFixed(2)}%` : ""}${atrUnits != null ? ` · ${atrUnits < 0.1 ? "<0.1" : atrUnits.toFixed(1)} ATR` : ""}`;
}

/** An explanation of observed features, never an entry decision or probability. */
export function buildMarketExplanation(context: MarketContext) {
  const direction = context.trend?.direction ?? null;
  const timeframe = context.provenance.timeframe;
  const supporting: string[] = [];
  const conflicting: string[] = [];
  const changes: string[] = [];
  const headline = direction === "bullish" ? `${timeframe} trend leans bullish`
    : direction === "bearish" ? `${timeframe} trend leans bearish`
      : direction === "neutral" ? `No clear ${timeframe} direction`
        : `Not enough history for a ${timeframe} trend`;

  if (direction) supporting.push(direction === "neutral"
    ? "The moving-average spread and slope do not establish a clear direction."
    : `The moving-average spread and slope give a ${direction} reading.`);

  if (context.vwap !== null && context.vwapLocation !== "unavailable") {
    const statement = `The last completed candle closed ${context.vwapLocation === "at" ? "near" : context.vwapLocation} VWAP (${price.format(context.vwap)}).`;
    const against = (direction === "bullish" && context.vwapLocation === "below")
      || (direction === "bearish" && context.vwapLocation === "above")
      || (direction === "neutral" && context.vwapLocation !== "at");
    (against ? conflicting : supporting).push(statement);
  }

  const higher = context.trends.slice(1);
  if (higher.length > 0) {
    const agreement = higher.filter(trend => direction && direction !== "neutral" && trend.direction === direction);
    const opposition = higher.filter(trend => direction && direction !== "neutral" && trend.direction !== "neutral" && trend.direction !== direction);
    if (agreement.length) supporting.push(`${agreement.map(trend => trend.label).join(" and ")} trend${agreement.length > 1 ? "s" : ""} agree with this direction.`);
    if (opposition.length) conflicting.push(`${opposition.map(trend => `${trend.label} is ${trend.direction}`).join("; ")}, opposing the selected timeframe.`);
    const flat = higher.filter(trend => trend.direction === "neutral");
    if (direction !== "neutral" && flat.length) conflicting.push(`${flat.map(trend => trend.label).join(" and ")} has no clear direction; it does not confirm the selected timeframe.`);
    if (direction === "neutral") {
      (higher.some(trend => trend.direction !== "neutral") ? conflicting : supporting)
        .push(`Higher-timeframe context: ${higher.map(trend => `${trend.label} ${trend.direction}`).join(", ")}.`);
    }
  }

  if (context.marketRegime === "chop" && direction !== "neutral") conflicting.push("The directional tilt sits within choppy conditions; it is not a clean sustained trend.");
  if (context.relativeVolume !== null && context.relativeVolume < 0.7) {
    conflicting.push(`Volume is ${context.relativeVolume.toFixed(2)}× its recent average; participation is subdued.`);
  }
  if (context.provenance.detectedGapCount > 0) conflicting.unshift("Missing candles limit the reliability of this read and its levels.");
  if (context.provenance.isStale) conflicting.unshift("These completed candles are stale; the interpretation may no longer describe the current market.");
  if (context.trend === null) conflicting.unshift("More completed candles are needed to establish trend direction.");

  if (context.nearestResistance !== null) changes.push(`A completed candle above ${price.format(context.nearestResistance)} would cross the current swing resistance.`);
  if (context.nearestSupport !== null) changes.push(`A completed candle below ${price.format(context.nearestSupport)} would cross the current swing support.`);
  if (context.vwap !== null && (direction === "bullish" || direction === "bearish")) {
    changes.push(`A close ${direction === "bullish" ? "below" : "above"} VWAP would ${
      (direction === "bullish" && context.vwapLocation === "below") || (direction === "bearish" && context.vwapLocation === "above")
        ? "keep price in conflict with" : "put price in conflict with"
    } the ${direction} trend.`);
  }
  if (changes.length) changes.push("A level crossing is an alert, not a confirmed structure change. Look for subsequent closed candles holding beyond it and a higher low / higher high (bullish), or lower high / lower low (bearish). Confirmation is not measured by this panel.");

  return {
    headline: context.provenance.isStale ? `${headline} · stale data` : headline,
    summary: `${regimeMeaning(context.marketRegime, direction ?? "unknown")} ${context.relativeVolume !== null ? "" : "Candle volume comparison unavailable."}`.trim(),
    supporting,
    conflicting,
    changes,
  };
}
