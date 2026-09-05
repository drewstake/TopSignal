import type { BotBacktestTrade } from "../../lib/types";

export type TradeOutcome = "winner" | "loser" | "breakeven";
export type TradeSideFilter = "all" | "long" | "short";
export type BreakdownDimension = "hour" | "weekday" | "year" | "exit" | "duration";

export interface AnalyzedTrade extends BotBacktestTrade {
  outcome: TradeOutcome;
  holdMinutes: number | null;
  entryHour: string;
  entryWeekday: string;
  entryYear: string;
  points: number;
}

export interface TradeSummary {
  label: string;
  count: number;
  longs: number;
  shorts: number;
  winners: number;
  losers: number;
  breakevens: number;
  winRate: number | null;
  grossPnl: number;
  commission: number;
  netPnl: number;
  profitFactor: number | null;
  expectancy: number | null;
  averageWin: number | null;
  averageLoss: number | null;
  largestWin: number | null;
  largestLoss: number | null;
  averageHold: number | null;
  medianHold: number | null;
  p90Hold: number | null;
  timedCount: number;
  averageBars: number | null;
  averageMae: number | null;
  averageMfe: number | null;
  feeErasedWinners: number;
}

const easternParts = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hourCycle: "h23", hour: "2-digit",
  weekday: "long", year: "numeric",
});
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "Unknown"];
const DURATIONS = ["Under 5m", "5–<15m", "15–<30m", "30–<60m", "1–<2h", "2h+", "Unknown"];

/** Elapsed wall time from recorded fills, not bars × timeframe (which misses session gaps). */
export function holdingMinutes(trade: BotBacktestTrade): number | null {
  const start = Date.parse(trade.entry_timestamp);
  const end = Date.parse(trade.exit_timestamp);
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? (end - start) / 60_000 : null;
}

export function analyzeTrades(trades: BotBacktestTrade[]): AnalyzedTrade[] {
  return trades.map((trade) => {
    const start = new Date(trade.entry_timestamp);
    const parts = Number.isNaN(start.getTime()) ? [] : easternParts.formatToParts(start);
    const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
    const hour = part("hour");
    return {
      ...trade,
      outcome: trade.net_pnl > 0 ? "winner" : trade.net_pnl < 0 ? "loser" : "breakeven",
      holdMinutes: holdingMinutes(trade),
      entryHour: hour ? `${hour}:00–${hour}:59` : "Unknown",
      entryWeekday: part("weekday") ?? "Unknown",
      entryYear: part("year") ?? "Unknown",
      points: (trade.exit_price - trade.entry_price) * (trade.side === "long" ? 1 : -1),
    };
  });
}

function mean(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function quantile(sorted: number[], fraction: number): number | null {
  if (!sorted.length) return null;
  const index = (sorted.length - 1) * fraction;
  const lower = Math.floor(index);
  return sorted[lower] + (sorted[Math.ceil(index)] - sorted[lower]) * (index - lower);
}

export function summarizeTrades(trades: AnalyzedTrade[], label: string): TradeSummary {
  const winners = trades.filter((trade) => trade.outcome === "winner").map((trade) => trade.net_pnl);
  const losers = trades.filter((trade) => trade.outcome === "loser").map((trade) => trade.net_pnl);
  const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
  const netPnl = sum(trades.map((trade) => trade.net_pnl));
  const losses = -sum(losers);
  const durations = trades.map((trade) => trade.holdMinutes).filter((value): value is number => value !== null).sort((a, b) => a - b);
  return {
    label, count: trades.length,
    longs: trades.filter((trade) => trade.side === "long").length,
    shorts: trades.filter((trade) => trade.side === "short").length,
    winners: winners.length, losers: losers.length,
    breakevens: trades.length - winners.length - losers.length,
    winRate: trades.length ? winners.length / trades.length * 100 : null,
    grossPnl: sum(trades.map((trade) => trade.gross_pnl)),
    commission: sum(trades.map((trade) => trade.commission)),
    netPnl,
    profitFactor: losses > 0 ? sum(winners) / losses : null,
    expectancy: trades.length ? netPnl / trades.length : null,
    averageWin: mean(winners), averageLoss: mean(losers),
    largestWin: winners.length ? winners.reduce((a, b) => Math.max(a, b)) : null,
    largestLoss: losers.length ? losers.reduce((a, b) => Math.min(a, b)) : null,
    averageHold: mean(durations), medianHold: quantile(durations, .5), p90Hold: quantile(durations, .9),
    timedCount: durations.length,
    averageBars: mean(trades.map((trade) => trade.bars_held)),
    averageMae: mean(trades.map((trade) => Math.abs(trade.mae))),
    averageMfe: mean(trades.map((trade) => Math.abs(trade.mfe))),
    feeErasedWinners: trades.filter((trade) => trade.gross_pnl > 0 && trade.net_pnl <= 0).length,
  };
}

function durationBucket(minutes: number | null): string {
  if (minutes === null) return "Unknown";
  if (minutes < 5) return DURATIONS[0];
  if (minutes < 15) return DURATIONS[1];
  if (minutes < 30) return DURATIONS[2];
  if (minutes < 60) return DURATIONS[3];
  if (minutes < 120) return DURATIONS[4];
  return DURATIONS[5];
}

export function groupTrades(trades: AnalyzedTrade[], dimension: BreakdownDimension): TradeSummary[] {
  const groups = new Map<string, AnalyzedTrade[]>();
  for (const trade of trades) {
    const key = dimension === "hour" ? trade.entryHour
      : dimension === "weekday" ? trade.entryWeekday
      : dimension === "year" ? trade.entryYear
      : dimension === "exit" ? trade.exit_reason.replaceAll("_", " ")
      : durationBucket(trade.holdMinutes);
    const group = groups.get(key) ?? [];
    group.push(trade);
    groups.set(key, group);
  }
  const rows = [...groups].map(([label, group]) => summarizeTrades(group, label));
  return rows.sort((a, b) => {
    if (dimension === "weekday") return WEEKDAYS.indexOf(a.label) - WEEKDAYS.indexOf(b.label);
    if (dimension === "duration") return DURATIONS.indexOf(a.label) - DURATIONS.indexOf(b.label);
    if (dimension === "exit") return b.count - a.count || a.label.localeCompare(b.label);
    return a.label.localeCompare(b.label);
  });
}

export function buildTradeAnalysis(trades: AnalyzedTrade[]) {
  return {
    overall: summarizeTrades(trades, "All trades"),
    directions: (["long", "short"] as const).map((side) => summarizeTrades(trades.filter((trade) => trade.side === side), side === "long" ? "Longs" : "Shorts")),
    outcomes: (["winner", "loser", "breakeven"] as const).map((outcome) => summarizeTrades(trades.filter((trade) => trade.outcome === outcome), outcome === "winner" ? "Winners" : outcome === "loser" ? "Losers" : "Breakeven")),
    byHour: groupTrades(trades, "hour"),
    byWeekday: groupTrades(trades, "weekday"),
    byYear: groupTrades(trades, "year"),
    byExit: groupTrades(trades, "exit"),
    byDuration: groupTrades(trades, "duration"),
  };
}

export function formatHold(minutes: number | null): string {
  if (minutes === null || !Number.isFinite(minutes)) return "—";
  if (minutes < 60) return `${Number(minutes.toFixed(1))}m`;
  if (minutes < 1440) return `${Number((minutes / 60).toFixed(1))}h`;
  return `${Number((minutes / 1440).toFixed(1))}d`;
}

/** Export every selected trade, independent of ledger pagination and display rounding. */
export function tradesToCsv(trades: AnalyzedTrade[]): string {
  const header = ["id", "side", "outcome_net", "quantity", "signal_utc", "entry_utc", "exit_utc", "entry_hour_et", "entry_weekday_et", "entry_year_et", "entry_price", "exit_price", "price_move_points", "exit_reason", "hold_minutes_approx", "bars_held", "gross_pnl", "commission", "net_pnl", "mae_dollars", "mfe_dollars"];
  const escape = (value: string | number | null) => {
    let cell = value === null ? "" : String(value);
    if (typeof value === "string" && /^[=+@\t\r-]/.test(cell)) cell = `'${cell}`;
    return `"${cell.replaceAll('"', '""')}"`;
  };
  const rows = trades.map((t) => [t.id, t.side, t.outcome, t.quantity, t.signal_timestamp, t.entry_timestamp, t.exit_timestamp, t.entryHour, t.entryWeekday, t.entryYear, t.entry_price, t.exit_price, t.points, t.exit_reason, t.holdMinutes, t.bars_held, t.gross_pnl, t.commission, t.net_pnl, Math.abs(t.mae), Math.abs(t.mfe)]);
  return [header, ...rows].map((row) => row.map(escape).join(",")).join("\r\n");
}
