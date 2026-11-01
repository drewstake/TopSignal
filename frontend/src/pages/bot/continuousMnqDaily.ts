import type { ProjectXMarketCandle } from "../../lib/types";
import type { ChartWindow } from "../backtesting/historyChartData";
import { isConfirmedClosedCandle } from "./botMarketContext";

export type ContinuousDailyBar = {
  date: string; open: number; high: number; low: number; close: number; volume: number;
  contract: string; source: "Databento" | "ProjectX";
};

export function sameMnqDelivery(rawSymbol: string, contractId: string) {
  const raw = rawSymbol.match(/^MNQ([FGHJKMNQUVXZ])(\d{1,4})$/i);
  const current = contractId.match(/\.MNQ\.([FGHJKMNQUVXZ])(\d{2,4})$/i);
  if (!raw || !current || raw[1].toUpperCase() !== current[1].toUpperCase()) return false;
  return Number(raw[2]) === Number(current[2]) % (10 ** raw[2].length);
}

/** Both feeds are displayed by trading date, not their different bucket-start timestamps.
 * Archive sessions end on their trading date; ProjectX daily timestamps label that date in UTC.
 * Never replace archive sessions or append another delivery under a continuous-history label.
 */
export function mergeContinuousDaily(history: ChartWindow, tail: ProjectXMarketCandle[], contractId: string, now = Date.now()): ContinuousDailyBar[] {
  if (history.timeframe !== "1d" || history.aggregate_size !== 1) throw new Error("Expected individual daily candles");
  const rows: ContinuousDailyBar[] = history.candles.map(row => ({
    date: new Date(row.end_time * 1000).toISOString().slice(0, 10),
    open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
    contract: row.contract, source: "Databento",
  }));
  const last = rows.at(-1);
  if (!last || !sameMnqDelivery(last.contract, contractId)) return rows;
  const byDate = new Map(rows.map(row => [row.date, row]));
  for (const row of tail) {
    if (row.contract_id !== contractId || row.unit !== "day" || row.unit_number !== 1
      || !isConfirmedClosedCandle(row, now)) continue;
    const date = new Date(row.timestamp).toISOString().slice(0, 10);
    if (date <= last.date) continue;
    byDate.set(date, { date, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
      contract: contractId.split(".").slice(-2).join(""), source: "ProjectX" });
  }
  return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
}
