import type { AccountSummary, AccountTrade, JournalEntry } from "../../lib/types";
import { getDisplayTradeSymbol } from "../../lib/tradeSymbol";
import { stripJournalImageMarkdown } from "./journalImages";

const JOURNAL_COPY_DIVIDER = "=".repeat(50);
const MAX_SYMBOL_COUNT = 4;

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const integerFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

const percentFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
});

const ratioFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

export interface JournalCopyTradeStats {
  netPnl?: number;
  tradeCount?: number;
  winRate?: number;
  profitFactor?: number;
  expectancy?: number;
  bestTrade?: number;
  worstTrade?: number;
  symbols?: string[];
}

interface BuildJournalCopyTradeStatsInput {
  entry: JournalEntry;
  summary?: AccountSummary | null;
  trades?: AccountTrade[];
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function formatSignedCurrency(value: number) {
  if (value > 0) {
    return `+${currencyFormatter.format(value)}`;
  }
  if (value < 0) {
    return `-${currencyFormatter.format(Math.abs(value))}`;
  }
  return currencyFormatter.format(0);
}

function formatPercent(value: number) {
  return `${percentFormatter.format(value)}%`;
}

function formatRatio(value: number) {
  return ratioFormatter.format(value);
}

function getAccountTradeNetPnl(trade: AccountTrade) {
  if (!isFiniteNumber(trade.pnl)) {
    return null;
  }
  const fees = isFiniteNumber(trade.fees) ? trade.fees : 0;
  return trade.pnl - fees;
}

function getTradeOutcomeRange(trades: AccountTrade[]) {
  const netValues = trades
    .map((trade) => getAccountTradeNetPnl(trade))
    .filter((value): value is number => value !== null);

  if (netValues.length === 0) {
    return {
      bestTrade: undefined,
      worstTrade: undefined,
    };
  }

  return {
    bestTrade: Math.max(...netValues),
    worstTrade: Math.min(...netValues),
  };
}

function getMainSymbolsTraded(trades: AccountTrade[]) {
  const symbolStats = new Map<string, { tradeCount: number; absNetPnl: number }>();

  for (const trade of trades) {
    const symbol = getDisplayTradeSymbol(trade.symbol, trade.contract_id);
    if (!symbol) {
      continue;
    }

    const current = symbolStats.get(symbol) ?? { tradeCount: 0, absNetPnl: 0 };
    const netPnl = getAccountTradeNetPnl(trade) ?? 0;
    symbolStats.set(symbol, {
      tradeCount: current.tradeCount + 1,
      absNetPnl: current.absNetPnl + Math.abs(netPnl),
    });
  }

  return Array.from(symbolStats.entries())
    .sort((left, right) => {
      const [, leftValue] = left;
      const [, rightValue] = right;
      if (rightValue.tradeCount !== leftValue.tradeCount) {
        return rightValue.tradeCount - leftValue.tradeCount;
      }
      if (rightValue.absNetPnl !== leftValue.absNetPnl) {
        return rightValue.absNetPnl - leftValue.absNetPnl;
      }
      return left[0].localeCompare(right[0]);
    })
    .slice(0, MAX_SYMBOL_COUNT)
    .map(([symbol]) => symbol);
}

function getSnapshotNetPnl(entry: JournalEntry) {
  const snapshot = entry.stats_json;
  if (!snapshot) {
    return undefined;
  }

  const realized = snapshot.net_realized_pnl ?? snapshot.net;
  return isFiniteNumber(realized) ? realized : undefined;
}

function getSnapshotTradeCount(entry: JournalEntry) {
  const tradeCount = entry.stats_json?.trade_count;
  return isFiniteNumber(tradeCount) && tradeCount > 0 ? Math.round(tradeCount) : undefined;
}

function getSnapshotWinRate(entry: JournalEntry) {
  const winRate = entry.stats_json?.win_rate;
  return isFiniteNumber(winRate) ? winRate : undefined;
}

function getSnapshotBestTrade(entry: JournalEntry) {
  const bestTrade = entry.stats_json?.largest_win;
  return isFiniteNumber(bestTrade) && bestTrade !== 0 ? bestTrade : undefined;
}

function getSnapshotWorstTrade(entry: JournalEntry) {
  const worstTrade = entry.stats_json?.largest_loss;
  return isFiniteNumber(worstTrade) && worstTrade !== 0 ? worstTrade : undefined;
}

function pushField(lines: string[], label: string, value: string | null | undefined) {
  if (!value) {
    return;
  }
  lines.push(`${label}: ${value}`);
}

function buildTradeStatsLines(stats: JournalCopyTradeStats | null | undefined) {
  if (!stats) {
    return [];
  }

  const lines: string[] = [];
  if (isFiniteNumber(stats.netPnl)) {
    lines.push(`Net PnL: ${formatSignedCurrency(stats.netPnl)}`);
  }
  if (isFiniteNumber(stats.tradeCount)) {
    lines.push(`Trades: ${integerFormatter.format(stats.tradeCount)}`);
  }
  if (isFiniteNumber(stats.winRate)) {
    lines.push(`Win Rate: ${formatPercent(stats.winRate)}`);
  }
  if (isFiniteNumber(stats.profitFactor)) {
    lines.push(`Profit Factor: ${formatRatio(stats.profitFactor)}`);
  }
  if (isFiniteNumber(stats.expectancy)) {
    lines.push(`Expectancy: ${formatSignedCurrency(stats.expectancy)}`);
  }
  if (isFiniteNumber(stats.bestTrade)) {
    lines.push(`Best Trade: ${formatSignedCurrency(stats.bestTrade)}`);
  }
  if (isFiniteNumber(stats.worstTrade)) {
    lines.push(`Worst Trade: ${formatSignedCurrency(stats.worstTrade)}`);
  }
  if (stats.symbols && stats.symbols.length > 0) {
    lines.push(`Symbols: ${stats.symbols.join(", ")}`);
  }
  return lines;
}

function formatJournalEntry(entry: JournalEntry, tradeStats: JournalCopyTradeStats | null | undefined) {
  const lines = [JOURNAL_COPY_DIVIDER, "Journal Entry"];
  const title = entry.title.trim();
  const tags = entry.tags.filter(Boolean);
  const notes = stripJournalImageMarkdown(entry.body).trim();
  const tradeStatsLines = buildTradeStatsLines(tradeStats);

  pushField(lines, "Date", entry.entry_date);
  pushField(lines, "Title", title || undefined);
  pushField(lines, "Mood", entry.mood || undefined);
  pushField(lines, "Tags", tags.length > 0 ? tags.join(", ") : undefined);

  if (tradeStatsLines.length > 0) {
    lines.push("", "Trade Stats:", ...tradeStatsLines);
  }

  if (notes) {
    lines.push("", "Notes:", notes);
  }

  lines.push(JOURNAL_COPY_DIVIDER);
  return lines.join("\n");
}

export function getCopyEntry(entries: JournalEntry[], selectedId: number | null) {
  if (entries.length === 0) {
    return null;
  }
  if (selectedId === null) {
    return entries[0];
  }
  return entries.find((entry) => entry.id === selectedId) ?? entries[0];
}

export function buildJournalCopyTradeStats({
  entry,
  summary,
  trades = [],
}: BuildJournalCopyTradeStatsInput): JournalCopyTradeStats | null {
  const resolvedSummary = summary && isFiniteNumber(summary.trade_count) && summary.trade_count > 0 ? summary : null;
  const hasCompleteTradeRows = resolvedSummary ? trades.length >= resolvedSummary.trade_count : trades.length > 0;
  const tradeRange = hasCompleteTradeRows ? getTradeOutcomeRange(trades) : { bestTrade: undefined, worstTrade: undefined };
  const symbols = hasCompleteTradeRows ? getMainSymbolsTraded(trades) : [];
  const netPnl = resolvedSummary ? resolvedSummary.net_pnl : getSnapshotNetPnl(entry);
  const tradeCount = resolvedSummary ? resolvedSummary.trade_count : getSnapshotTradeCount(entry);
  const winRate = resolvedSummary ? resolvedSummary.win_rate : getSnapshotWinRate(entry);
  const expectancy = resolvedSummary ? resolvedSummary.expectancy_per_trade : undefined;
  const profitFactor =
    resolvedSummary && resolvedSummary.loss_count > 0 ? resolvedSummary.profit_factor : undefined;
  const bestTrade = isFiniteNumber(tradeRange.bestTrade) ? tradeRange.bestTrade : getSnapshotBestTrade(entry);
  const worstTrade = isFiniteNumber(tradeRange.worstTrade) ? tradeRange.worstTrade : getSnapshotWorstTrade(entry);

  const stats: JournalCopyTradeStats = {};
  if (isFiniteNumber(netPnl)) {
    stats.netPnl = netPnl;
  }
  if (isFiniteNumber(tradeCount)) {
    stats.tradeCount = tradeCount;
  }
  if (isFiniteNumber(winRate)) {
    stats.winRate = winRate;
  }
  if (isFiniteNumber(profitFactor)) {
    stats.profitFactor = profitFactor;
  }
  if (isFiniteNumber(expectancy)) {
    stats.expectancy = expectancy;
  }
  if (isFiniteNumber(bestTrade)) {
    stats.bestTrade = bestTrade;
  }
  if (isFiniteNumber(worstTrade)) {
    stats.worstTrade = worstTrade;
  }
  if (symbols.length > 0) {
    stats.symbols = symbols;
  }

  return Object.keys(stats).length > 0 ? stats : null;
}

export function buildJournalCopyText(
  entries: JournalEntry[],
  tradeStatsByDate: Map<string, JournalCopyTradeStats | null> = new Map(),
) {
  return entries
    .map((entry) => formatJournalEntry(entry, tradeStatsByDate.get(entry.entry_date)))
    .join("\n\n");
}
