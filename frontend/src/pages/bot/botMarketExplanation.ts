import type { MarketContext } from "./botMarketContext";

const price = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

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
    if (direction === "neutral") {
      (higher.some(trend => trend.direction !== "neutral") ? conflicting : supporting)
        .push(`Higher-timeframe context: ${higher.map(trend => `${trend.label} ${trend.direction}`).join(", ")}.`);
    }
  }

  if (context.relativeVolume !== null && context.relativeVolume < 0.8) {
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

  const regime = context.marketRegime === "trend" ? "Trending conditions"
    : context.marketRegime === "range" ? "Range conditions"
      : context.marketRegime === "quiet" ? "Quiet conditions"
        : context.marketRegime === "volatile" ? "Volatile conditions"
          : context.marketRegime === "chop" ? "Choppy conditions" : "Market regime unavailable";
  return {
    headline: context.provenance.isStale ? `${headline} · stale data` : headline,
    summary: `${regime}${context.relativeVolume !== null ? ` · volume ${context.relativeVolume.toFixed(2)}× its recent average` : " · volume comparison unavailable"}.`,
    supporting: supporting.slice(0, 3),
    conflicting: conflicting.slice(0, 3),
    changes,
  };
}
