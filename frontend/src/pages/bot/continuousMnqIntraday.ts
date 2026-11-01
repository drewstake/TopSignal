import { botsApi, requestJson, type CandleQuery } from "../../lib/api";
import type { ProjectXMarketCandle } from "../../lib/types";
import type { ChartWindow, HistoricalCandle } from "../backtesting/historyChartData";
import { sameMnqDelivery } from "./continuousMnqDaily";

const metadata = new Map<string, { checked: number; history: ChartWindow }>();

export function continuousIntradayFrame(query: CandleQuery) {
  const minutes = query.unit === "hour" ? (query.unitNumber ?? 1) * 60 : query.unitNumber ?? 1;
  if (![1, 5, 15, 60, 240].includes(minutes) || !["minute", "hour"].includes(query.unit ?? "")) {
    throw new Error("Unsupported continuous intraday timeframe");
  }
  return { timeframe: (minutes === 240 ? "4h" : minutes === 1 ? "1m" : "5m") as "4h" | "1m" | "5m",
    seconds: minutes * 60, ratio: minutes === 240 || minutes === 1 ? 1 : minutes / 5 };
}

/** Exact clock buckets only: never treat an overview/downsample as a candle. */
export function aggregateContinuousIntraday(rows: HistoricalCandle[], seconds: number, ratio: number): HistoricalCandle[] {
  const sorted = [...new Map(rows.map(row => [row.time, row])).values()].sort((a, b) => a.time - b.time);
  if (sorted.some(row => row.count !== 1)) throw new Error("Expected individual historical candles");
  if (ratio === 1) return sorted;
  const buckets = new Map<number, HistoricalCandle[]>();
  for (const row of sorted) {
    const time = Math.floor(row.time / seconds) * seconds;
    const group = buckets.get(time) ?? [];
    group.push(row);
    buckets.set(time, group);
  }
  return [...buckets].map(([time, group]) => {
    if (new Set(group.map(row => row.contract)).size !== 1) throw new Error("Contract rollover inside a candle; refresh the continuous archive");
    return { time, end_time: time + seconds, open: group[0].open, high: Math.max(...group.map(row => row.high)),
      low: Math.min(...group.map(row => row.low)), close: group.at(-1)!.close,
      volume: group.reduce((sum, row) => sum + row.volume, 0), contract: group[0].contract, count: 1 };
  });
}

/** Read local archive windows; only the recent, same-delivery suffix goes to ProjectX. */
export async function loadContinuousMnqIntraday(scope: string, query: CandleQuery, signal?: AbortSignal): Promise<ProjectXMarketCandle[]> {
  const frame = continuousIntradayFrame(query);
  const key = `${scope}:${frame.timeframe}`;
  let info = metadata.get(key);
  if (!info || Date.now() - info.checked > 60_000) {
    const history = await requestJson<ChartWindow>("/api/market-data/databento/candles", {
      query: { timeframe: frame.timeframe, limit: 1 }, signal,
    });
    info = { checked: Date.now(), history };
    if (metadata.size >= 32) metadata.delete(metadata.keys().next().value!);
    metadata.set(key, info);
  }
  const coverageEnd = Date.parse(info.history.coverage_end) / 1000;
  if (!Number.isFinite(coverageEnd)) throw new Error("Continuous archive coverage is unavailable");
  const limit = Math.min(2000, Math.max(1, query.limit ?? 300));
  const end = query.end ? Date.parse(query.end) / 1000 : Date.now() / 1000;
  const start = query.start ? Date.parse(query.start) / 1000 : end - limit * frame.seconds * 3;
  // ProjectX queries bound candle START times; the archive endpoint also bounds
  // candle CLOSE times. Read the whole final bucket so pagination's end just
  // before the first loaded candle does not drop its immediate predecessor.
  const sourceEnd = frame.ratio > 1 ? (Math.floor(end / frame.seconds) + 1) * frame.seconds : end + frame.seconds;
  const archiveEnd = Math.min(sourceEnd, coverageEnd, Date.now() / 1000);
  // Include the full source bucket at the left boundary, then filter by candle start.
  const archiveStart = frame.ratio > 1 ? Math.floor(start / frame.seconds) * frame.seconds : start;
  let cursor = archiveEnd;
  const raw: HistoricalCandle[] = [];
  while (archiveStart < cursor) {
    const history = await requestJson<ChartWindow>("/api/market-data/databento/candles", {
      query: { timeframe: frame.timeframe, start: new Date(archiveStart * 1000).toISOString(),
        end: new Date(cursor * 1000).toISOString(), limit: Math.min(10000, limit * frame.ratio + frame.ratio) }, signal,
    });
    if (history.aggregate_size !== 1 || history.timeframe !== frame.timeframe) throw new Error("Expected exact continuous candles");
    raw.push(...history.candles);
    const first = history.candles[0]?.time;
    if (!history.truncated || raw.length >= limit * frame.ratio + frame.ratio || first === undefined || first >= cursor) break;
    cursor = first;
  }
  const fetchedAt = new Date().toISOString();
  const rows: ProjectXMarketCandle[] = aggregateContinuousIntraday(raw, frame.seconds, frame.ratio)
    .filter(row => row.time >= start && row.time <= end && row.end_time <= archiveEnd)
    .map(row => ({ id: null, contract_id: row.contract, symbol: query.symbol ?? "F.US.MNQ", live: false,
      unit: query.unit!, unit_number: query.unitNumber!, timestamp: new Date(row.time * 1000).toISOString(),
      open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
      is_partial: false, fetched_at: fetchedAt }));
  if (end > coverageEnd) {
    const delivery = info.history.candles.at(-1)?.contract;
    if (!delivery || !sameMnqDelivery(delivery, query.contractId) || Date.now() / 1000 - coverageEnd > 14 * 86400) {
      throw new Error("Continuous archive needs an update before recent candles can be joined to this contract.");
    }
    const tailStart = Math.max(start, coverageEnd);
    const tail = await botsApi.getCandles({ ...query, start: new Date(tailStart * 1000).toISOString(), limit }, { signal });
    rows.push(...tail.filter(row => row.contract_id === query.contractId && row.unit === query.unit
      && row.unit_number === query.unitNumber && Date.parse(row.timestamp) / 1000 >= tailStart));
  }
  return rows.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)).slice(-limit);
}
