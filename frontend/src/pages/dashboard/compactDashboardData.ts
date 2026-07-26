import { getTradingDayBoundaryIso, getTradingDayRange, tradingDayKey } from "../../lib/tradingDay";
import type { AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../lib/types";

export interface CompactChartPoint {
  date: string;
  dailyPnl: number;
  cumulativePnl: number;
}

/** Each account only needs enough rows to contribute to the global latest-seven list. */
export const COMPACT_RECENT_TRADES_LIMIT = 7;

export type CompactRangePreset = "1D" | "1W" | "1M" | "6M" | "ALL" | "CUSTOM";

export interface CompactCustomDateRange {
  startDate: string;
  endDate: string;
}

export interface CompactDashboardScope {
  start?: string;
  end?: string;
  allTime: boolean;
  selectedDate: string | null;
  startDate?: string;
  endDate?: string;
  key: string;
}

export interface CompactDashboardScopes {
  /** Scope shared by KPI, chart/score input, and Recent Trades. */
  analysisScope: CompactDashboardScope;
  /** Preset/custom context retained by the calendar while a day is selected. */
  calendarContextScope: CompactDashboardScope;
}

interface BuildCompactDashboardScopeInput {
  range: CompactRangePreset;
  customRange: CompactCustomDateRange | null;
  currentTradingDay: string;
  selectedDate: string | null;
  asOf: Date;
}

function compactScopeKey(scope: Omit<CompactDashboardScope, "key">) {
  return [
    scope.allTime ? "all" : "bounded",
    scope.start ?? "",
    scope.end ?? "",
    scope.selectedDate ?? "",
  ].join("|");
}

function boundedScope(start: string, end: string, selectedDate: string | null): CompactDashboardScope {
  const value = {
    start,
    end,
    allTime: false,
    selectedDate,
    startDate: tradingDayKey(start),
    endDate: tradingDayKey(end),
  };
  return { ...value, key: compactScopeKey(value) };
}

function allTimeScope(): CompactDashboardScope {
  const value = {
    allTime: true,
    selectedDate: null,
  };
  return { ...value, key: compactScopeKey(value) };
}

function subtractIsoMonths(value: string, months: number) {
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) {
    return null;
  }
  const monthIndex = year * 12 + (month - 1) - months;
  const targetYear = Math.floor(monthIndex / 12);
  const targetMonth = ((monthIndex % 12) + 12) % 12;
  const targetLastDay = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
  const targetDay = Math.min(day, targetLastDay);
  return `${targetYear}-${String(targetMonth + 1).padStart(2, "0")}-${String(targetDay).padStart(2, "0")}`;
}

/** Build one immutable futures-trading-day-aligned scope. */
export function buildCompactDashboardScope({
  range,
  customRange,
  currentTradingDay,
  selectedDate,
  asOf,
}: BuildCompactDashboardScopeInput): CompactDashboardScope {
  if (selectedDate) {
    const selectedRange = getTradingDayRange(selectedDate);
    if (selectedRange) {
      return boundedScope(selectedRange.start, selectedRange.end, selectedDate);
    }
  }

  if (range === "ALL") {
    return allTimeScope();
  }

  if (range === "CUSTOM") {
    if (!customRange) {
      return allTimeScope();
    }
    const start = getTradingDayBoundaryIso(customRange.startDate, false);
    const end = getTradingDayBoundaryIso(customRange.endDate, true);
    return start && end ? boundedScope(start, end, null) : allTimeScope();
  }

  const end = asOf.toISOString();
  let startDate = currentTradingDay;
  if (range === "1W") {
    startDate = tradingDayKey(new Date(asOf.getTime() - 7 * 24 * 60 * 60 * 1000));
  } else if (range === "1M") {
    startDate = `${currentTradingDay.slice(0, 7)}-01`;
  } else if (range === "6M") {
    startDate = subtractIsoMonths(currentTradingDay, 6) ?? currentTradingDay;
  }

  const start = getTradingDayBoundaryIso(startDate, false);
  return start ? boundedScope(start, end, null) : allTimeScope();
}

/**
 * Capture one `asOf` for a Compact load. KPI/trade analysis narrows to a
 * selected day, while the calendar keeps the active preset/custom context so
 * another day remains reachable without clearing the current selection first.
 */
export function buildCompactDashboardScopes(
  input: BuildCompactDashboardScopeInput,
): CompactDashboardScopes {
  const calendarContextScope = buildCompactDashboardScope({ ...input, selectedDate: null });
  const analysisScope = input.selectedDate
    ? buildCompactDashboardScope(input)
    : calendarContextScope;
  return { analysisScope, calendarContextScope };
}

export function buildCompactAccountRequestPlan(
  scopes: CompactDashboardScopes,
  options: { forceRefresh?: boolean } = {},
) {
  const { analysisScope, calendarContextScope } = scopes;
  const forceRefresh = options.forceRefresh === true;
  return {
    summary: {
      start: analysisScope.start,
      end: analysisScope.end,
      refresh: forceRefresh || analysisScope.selectedDate !== null,
    },
    calendar: {
      start: calendarContextScope.start,
      end: calendarContextScope.end,
      all_time: calendarContextScope.allTime,
      refresh: forceRefresh,
    },
    trades: {
      limit: COMPACT_RECENT_TRADES_LIMIT,
      start: analysisScope.start,
      end: analysisScope.end,
      refresh: forceRefresh || analysisScope.selectedDate !== null,
      includeLifecycle: false,
    },
  } as const;
}

/** Publish a cache-busting reload only after copied-account refresh has settled. */
export async function refreshCompactCopyThenInvalidate(
  refresh: () => Promise<unknown>,
  invalidate: () => void,
) {
  await refresh();
  invalidate();
}

export interface CompactAccountDataset {
  summary: AccountSummary;
  days: readonly AccountPnlCalendarDay[];
  trades: readonly AccountTrade[];
}

function finite(value: number) {
  return Number.isFinite(value) ? value : 0;
}

function round(value: number, digits = 2) {
  const scale = 10 ** digits;
  return Math.round((value + Number.EPSILON) * scale) / scale;
}

export function combineCompactCalendarDays(
  datasets: readonly Pick<CompactAccountDataset, "days">[],
): AccountPnlCalendarDay[] {
  const byDate = new Map<string, AccountPnlCalendarDay>();
  datasets.forEach(({ days }) => {
    days.forEach((day) => {
      const current = byDate.get(day.date) ?? {
        date: day.date,
        trade_count: 0,
        win_count: 0,
        loss_count: 0,
        breakeven_count: 0,
        gross_pnl: 0,
        non_commission_fees: 0,
        commissions: 0,
        fees: 0,
        net_pnl: 0,
      };
      current.trade_count += day.trade_count;
      current.win_count = (current.win_count ?? 0) + (day.win_count ?? 0);
      current.loss_count = (current.loss_count ?? 0) + (day.loss_count ?? 0);
      current.breakeven_count = (current.breakeven_count ?? 0) + (day.breakeven_count ?? 0);
      current.gross_pnl += finite(day.gross_pnl);
      current.non_commission_fees = (current.non_commission_fees ?? 0) + finite(day.non_commission_fees ?? 0);
      current.commissions = (current.commissions ?? 0) + finite(day.commissions ?? 0);
      current.fees += finite(day.fees);
      current.net_pnl += finite(day.net_pnl);
      byDate.set(day.date, current);
    });
  });

  return [...byDate.values()]
    .sort((left, right) => left.date.localeCompare(right.date))
    .map((day) => ({
      ...day,
      gross_pnl: round(day.gross_pnl),
      non_commission_fees: round(day.non_commission_fees ?? 0),
      commissions: round(day.commissions ?? 0),
      fees: round(day.fees),
      net_pnl: round(day.net_pnl),
    }));
}

export function computeCompactCalendarMaxDrawdown(days: readonly AccountPnlCalendarDay[]) {
  let equity = 0;
  let peak = 0;
  let maxDrawdown = 0;
  [...days]
    .sort((left, right) => left.date.localeCompare(right.date))
    .forEach((day) => {
      equity += finite(day.net_pnl);
      peak = Math.max(peak, equity);
      maxDrawdown = Math.max(maxDrawdown, peak - equity);
    });
  return round(maxDrawdown);
}

function weightedAverage(
  summaries: readonly AccountSummary[],
  value: (summary: AccountSummary) => number,
  weight: (summary: AccountSummary) => number,
) {
  const totalWeight = summaries.reduce((total, summary) => total + Math.max(0, finite(weight(summary))), 0);
  if (totalWeight <= 0) {
    return 0;
  }
  return summaries.reduce(
    (total, summary) => total + finite(value(summary)) * Math.max(0, finite(weight(summary))),
    0,
  ) / totalWeight;
}

/**
 * Combine summary endpoints without depending on calendar or trade requests.
 * `days`, when supplied, makes portfolio day counts and drawdown exact; callers
 * that only have summaries still get accurate trade-derived KPI fields.
 */
export function combineCompactSummaries(
  summaries: readonly AccountSummary[],
  days: readonly AccountPnlCalendarDay[] = [],
): AccountSummary | null {
  if (summaries.length === 0) {
    return null;
  }
  if (summaries.length === 1) {
    return summaries[0];
  }

  const first = summaries[0];
  const sumField = (read: (summary: AccountSummary) => number) =>
    summaries.reduce((total, summary) => total + finite(read(summary)), 0);
  const tradeCount = sumField((summary) => summary.trade_count);
  const winCount = sumField((summary) => summary.win_count);
  const lossCount = sumField((summary) => summary.loss_count);
  const breakevenCount = sumField((summary) => summary.breakeven_count);
  const netWinningPnl = summaries.reduce(
    (total, summary) => total + finite(summary.avg_win) * Math.max(0, summary.win_count),
    0,
  );
  const netLosingPnl = summaries.reduce(
    (total, summary) => total + finite(summary.avg_loss) * Math.max(0, summary.loss_count),
    0,
  );
  const hasCalendarContext = days.length > 0;
  const activeDays = hasCalendarContext
    ? days.length
    : Math.max(0, ...summaries.map((summary) => finite(summary.active_days)));
  const greenDays = hasCalendarContext
    ? days.filter((day) => day.net_pnl > 0).length
    : Math.max(0, ...summaries.map((summary) => finite(summary.green_days)));
  const redDays = hasCalendarContext
    ? days.filter((day) => day.net_pnl < 0).length
    : Math.max(0, ...summaries.map((summary) => finite(summary.red_days)));
  const flatDays = hasCalendarContext
    ? activeDays - greenDays - redDays
    : Math.max(0, ...summaries.map((summary) => finite(summary.flat_days)));
  const sizingTradeCount = sumField((summary) => summary.tradeCountUsedForSizingStats);

  return {
    ...first,
    realized_pnl: round(sumField((value) => value.realized_pnl)),
    gross_pnl: round(sumField((value) => value.gross_pnl)),
    fees: round(sumField((value) => value.fees)),
    net_pnl: round(sumField((value) => value.net_pnl)),
    win_rate: tradeCount > 0 ? round((winCount / tradeCount) * 100) : 0,
    win_count: winCount,
    loss_count: lossCount,
    breakeven_count: breakevenCount,
    profit_factor: netLosingPnl < 0 ? round(netWinningPnl / Math.abs(netLosingPnl), 4) : 0,
    avg_win: winCount > 0 ? round(netWinningPnl / winCount) : 0,
    avg_loss: lossCount > 0 ? round(netLosingPnl / lossCount) : 0,
    avg_win_duration_minutes: round(weightedAverage(summaries, (value) => value.avg_win_duration_minutes, (value) => value.win_count)),
    avg_loss_duration_minutes: round(weightedAverage(summaries, (value) => value.avg_loss_duration_minutes, (value) => value.loss_count)),
    expectancy_per_trade: tradeCount > 0 ? round(sumField((value) => value.net_pnl) / tradeCount) : 0,
    tail_risk_5pct: round(weightedAverage(summaries, (value) => value.tail_risk_5pct, (value) => value.trade_count)),
    max_drawdown: hasCalendarContext
      ? computeCompactCalendarMaxDrawdown(days)
      : round(sumField((value) => Math.abs(value.max_drawdown))),
    average_drawdown: round(weightedAverage(summaries, (value) => value.average_drawdown, (value) => value.trade_count)),
    risk_drawdown_score: round(weightedAverage(summaries, (value) => value.risk_drawdown_score, (value) => value.trade_count)),
    max_drawdown_length_hours: round(Math.max(...summaries.map((summary) => finite(summary.max_drawdown_length_hours)))),
    recovery_time_hours: round(Math.max(...summaries.map((summary) => finite(summary.recovery_time_hours)))),
    average_recovery_length_hours: round(weightedAverage(summaries, (value) => value.average_recovery_length_hours, (value) => value.trade_count)),
    trade_count: tradeCount,
    half_turn_count: sumField((value) => value.half_turn_count),
    execution_count: sumField((value) => value.execution_count),
    day_win_rate: activeDays > 0 ? round((greenDays / activeDays) * 100) : 0,
    green_days: greenDays,
    red_days: redDays,
    flat_days: flatDays,
    avg_trades_per_day: activeDays > 0 ? round(tradeCount / activeDays) : 0,
    active_days: activeDays,
    efficiency_per_hour: round(sumField((value) => value.efficiency_per_hour)),
    profit_per_day: activeDays > 0 ? round(sumField((value) => value.net_pnl) / activeDays) : 0,
    averagePositionSize: round(weightedAverage(summaries, (value) => value.averagePositionSize, (value) => value.tradeCountUsedForSizingStats)),
    medianPositionSize: round(weightedAverage(summaries, (value) => value.medianPositionSize, (value) => value.tradeCountUsedForSizingStats)),
    tradeCountUsedForSizingStats: sizingTradeCount,
    avgPointGain: null,
    avgPointLoss: null,
  };
}

export function combineCompactTrades(
  tradeGroups: readonly (readonly AccountTrade[])[],
  limit = Number.POSITIVE_INFINITY,
): AccountTrade[] {
  return tradeGroups
    .flatMap((trades) => [...trades])
    .sort((left, right) => {
      const leftTime = Date.parse(left.exit_time ?? left.timestamp);
      const rightTime = Date.parse(right.exit_time ?? right.timestamp);
      return rightTime - leftTime || right.account_id - left.account_id || right.id - left.id;
    })
    .slice(0, Math.max(0, limit));
}

export function combineCompactAccountDatasets(
  datasets: readonly CompactAccountDataset[],
): CompactAccountDataset | null {
  if (datasets.length === 0) {
    return null;
  }
  if (datasets.length === 1) {
    return {
      summary: datasets[0].summary,
      days: [...datasets[0].days].sort((left, right) => left.date.localeCompare(right.date)),
      trades: [...datasets[0].trades].sort((left, right) => {
        const leftTime = Date.parse(left.exit_time ?? left.timestamp);
        const rightTime = Date.parse(right.exit_time ?? right.timestamp);
        return rightTime - leftTime || right.id - left.id;
      }),
    };
  }

  const days = combineCompactCalendarDays(datasets);
  const summary = combineCompactSummaries(datasets.map((dataset) => dataset.summary), days);
  const trades = combineCompactTrades(datasets.map((dataset) => dataset.trades));
  return summary ? { summary, days, trades } : null;
}

export function buildCompactChartPoints(
  days: readonly AccountPnlCalendarDay[],
  limit: number,
): CompactChartPoint[] {
  let cumulativePnl = 0;
  const points = [...days]
    .sort((left, right) => left.date.localeCompare(right.date))
    .map((day) => {
      const dailyPnl = Number.isFinite(day.net_pnl) ? day.net_pnl : 0;
      cumulativePnl += dailyPnl;
      return {
        date: day.date,
        dailyPnl,
        cumulativePnl,
      };
    });

  return points.slice(-Math.max(1, limit));
}
