import type { HistogramData, UTCTimestamp } from "lightweight-charts";

import type { ProjectXMarketCandle } from "../../lib/types";
import { toUtcTimestamp } from "./botChartData";

const UP_VOLUME_COLOR = "rgba(52,211,153,0.55)";
const DOWN_VOLUME_COLOR = "rgba(251,113,133,0.55)";

/**
 * Build the volume pane directly from ProjectX bars. Duplicate timestamps use
 * the same authority rule as the candle feed: a closed bar cannot be replaced
 * by a partial bar.
 */
export function buildVolumeData(candles: ProjectXMarketCandle[]): HistogramData<UTCTimestamp>[] {
  const byTime = new Map<number, { candle: ProjectXMarketCandle; time: UTCTimestamp }>();

  for (const candle of candles) {
    const time = toUtcTimestamp(candle.timestamp);
    if (
      time === null ||
      !Number.isFinite(candle.open) ||
      !Number.isFinite(candle.close) ||
      !Number.isFinite(candle.volume) ||
      candle.volume < 0
    ) {
      continue;
    }

    const timestamp = Number(time);
    const existing = byTime.get(timestamp);
    if (existing && !existing.candle.is_partial && candle.is_partial) {
      continue;
    }
    byTime.set(timestamp, { candle, time });
  }

  return Array.from(byTime.values())
    .sort((left, right) => Number(left.time) - Number(right.time))
    .map(({ candle, time }) => ({
      time,
      value: candle.volume,
      color: candle.close >= candle.open ? UP_VOLUME_COLOR : DOWN_VOLUME_COLOR,
    }));
}
