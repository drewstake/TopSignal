import type {
  AccountInfo,
  AccountLastTradeInfo,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountSummaryWithPointBases,
  AccountTrade,
  AuthMe,
  BehaviorMetrics,
  BotActivity,
  BotConfig,
  BotConfigListResponse,
  BotDecision,
  BotOrderAttempt,
  BotRiskEvent,
  BotRun,
  DayPnlPoint,
  ExpenseCategory,
  ExpenseListResponse,
  ExpenseRange,
  ExpenseRecord,
  ExpenseTotals,
  HourPnlPoint,
  JournalDaysResponse,
  JournalEntriesResponse,
  JournalEntry,
  PayoutListResponse,
  PayoutRecord,
  PayoutTotals,
  ProjectXContract,
  ProjectXCredentialsStatus,
  ProjectXMarketCandle,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
} from "./types";

type DemoQueryValue = string | number | boolean | null | undefined;

interface DemoApiResponse<T> {
  handled: true;
  data: T;
}

interface DemoTradeSpec {
  day: string;
  time: string;
  symbol: "MNQ" | "MES" | "MGC";
  side: "LONG" | "SHORT";
  size: number;
  entry: number;
  points: number;
  durationMinutes: number;
  mfe: number;
  mae: number;
}

const DEMO_USER_ID = "00000000-0000-4000-9000-000000000001";
const PRIMARY_ACCOUNT_ID = 910001;
const FOLLOWER_ACCOUNT_ID = 910002;
const SWING_ACCOUNT_ID = 910003;
const PRACTICE_ACCOUNT_ID = 910004;
const MISSING_ACCOUNT_ID = 910099;

const DEMO_ACCOUNTS: AccountInfo[] = [
  {
    id: PRIMARY_ACCOUNT_ID,
    name: "50KTC-DEMO-Primary",
    provider_name: "50KTC-DEMO-Primary",
    custom_display_name: null,
    balance: 52874.65,
    status: "active",
    account_state: "ACTIVE",
    is_main: true,
    can_trade: true,
    is_visible: true,
    last_trade_at: "2026-06-30T15:42:00.000Z",
  },
  {
    id: FOLLOWER_ACCOUNT_ID,
    name: "50KTC-DEMO-Follower-A",
    provider_name: "50KTC-DEMO-Follower-A",
    custom_display_name: null,
    balance: 51620.2,
    status: "active",
    account_state: "ACTIVE",
    is_main: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: "2026-06-30T15:40:00.000Z",
  },
  {
    id: SWING_ACCOUNT_ID,
    name: "100KTC-DEMO-Swing",
    provider_name: "100KTC-DEMO-Swing",
    custom_display_name: null,
    balance: 101980.75,
    status: "locked_out",
    account_state: "LOCKED_OUT",
    is_main: false,
    can_trade: false,
    is_visible: true,
    last_trade_at: "2026-06-27T18:35:00.000Z",
  },
  {
    id: PRACTICE_ACCOUNT_ID,
    name: "Practice-DEMO-Charting",
    provider_name: "Practice-DEMO-Charting",
    custom_display_name: null,
    balance: 50000,
    status: "active",
    account_state: "ACTIVE",
    is_main: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: "2026-06-24T14:18:00.000Z",
  },
  {
    id: MISSING_ACCOUNT_ID,
    name: "50KTC-DEMO-Archived",
    provider_name: "50KTC-DEMO-Archived",
    custom_display_name: null,
    balance: 49880.5,
    status: "missing",
    account_state: "MISSING",
    is_main: false,
    can_trade: null,
    is_visible: null,
    last_trade_at: "2026-05-29T14:12:00.000Z",
  },
];

const PRIMARY_TRADE_SPECS: DemoTradeSpec[] = [
  { day: "2026-06-10", time: "09:36", symbol: "MNQ", side: "LONG", size: 6, entry: 21842.5, points: 28.25, durationMinutes: 14, mfe: 34, mae: 8 },
  { day: "2026-06-10", time: "10:18", symbol: "MNQ", side: "SHORT", size: 4, entry: 21891, points: -15.5, durationMinutes: 8, mfe: 7, mae: 21 },
  { day: "2026-06-10", time: "11:07", symbol: "MES", side: "LONG", size: 3, entry: 5482.25, points: 9.75, durationMinutes: 23, mfe: 14, mae: 3 },
  { day: "2026-06-11", time: "09:44", symbol: "MNQ", side: "SHORT", size: 8, entry: 21935.75, points: 42.5, durationMinutes: 19, mfe: 51, mae: 11 },
  { day: "2026-06-11", time: "13:22", symbol: "MNQ", side: "LONG", size: 5, entry: 21840.25, points: -22.75, durationMinutes: 12, mfe: 6, mae: 29 },
  { day: "2026-06-12", time: "09:51", symbol: "MNQ", side: "LONG", size: 10, entry: 21796.5, points: 35.25, durationMinutes: 11, mfe: 41, mae: 7 },
  { day: "2026-06-12", time: "10:36", symbol: "MNQ", side: "LONG", size: 7, entry: 21812, points: 18.75, durationMinutes: 17, mfe: 22, mae: 5 },
  { day: "2026-06-12", time: "14:05", symbol: "MGC", side: "SHORT", size: 2, entry: 2375.4, points: -6.1, durationMinutes: 31, mfe: 2.8, mae: 8.4 },
  { day: "2026-06-15", time: "09:39", symbol: "MES", side: "SHORT", size: 5, entry: 5504.75, points: 12.5, durationMinutes: 21, mfe: 15, mae: 4 },
  { day: "2026-06-15", time: "10:28", symbol: "MNQ", side: "SHORT", size: 7, entry: 22006.25, points: 31.75, durationMinutes: 16, mfe: 39, mae: 9 },
  { day: "2026-06-16", time: "09:47", symbol: "MNQ", side: "LONG", size: 8, entry: 21912.75, points: -26, durationMinutes: 13, mfe: 5, mae: 34 },
  { day: "2026-06-16", time: "11:34", symbol: "MNQ", side: "SHORT", size: 6, entry: 21862.5, points: 21.25, durationMinutes: 18, mfe: 27, mae: 6 },
  { day: "2026-06-17", time: "09:35", symbol: "MNQ", side: "LONG", size: 9, entry: 21740.25, points: 47.75, durationMinutes: 24, mfe: 61, mae: 10 },
  { day: "2026-06-17", time: "13:12", symbol: "MES", side: "LONG", size: 4, entry: 5478.25, points: -8.25, durationMinutes: 10, mfe: 2, mae: 11 },
  { day: "2026-06-18", time: "09:42", symbol: "MNQ", side: "SHORT", size: 8, entry: 21984, points: 29.5, durationMinutes: 20, mfe: 33, mae: 8 },
  { day: "2026-06-18", time: "10:31", symbol: "MNQ", side: "LONG", size: 5, entry: 21922.75, points: 14.25, durationMinutes: 9, mfe: 18, mae: 5 },
  { day: "2026-06-19", time: "09:58", symbol: "MNQ", side: "LONG", size: 6, entry: 22018.25, points: -31.5, durationMinutes: 15, mfe: 9, mae: 38 },
  { day: "2026-06-19", time: "12:16", symbol: "MGC", side: "LONG", size: 2, entry: 2388.2, points: 10.4, durationMinutes: 36, mfe: 13.7, mae: 3.5 },
  { day: "2026-06-22", time: "09:33", symbol: "MNQ", side: "SHORT", size: 10, entry: 22102.5, points: 38.25, durationMinutes: 12, mfe: 44, mae: 7 },
  { day: "2026-06-22", time: "10:04", symbol: "MNQ", side: "SHORT", size: 6, entry: 22072.25, points: 16.5, durationMinutes: 7, mfe: 19, mae: 4 },
  { day: "2026-06-23", time: "09:49", symbol: "MES", side: "LONG", size: 4, entry: 5521.5, points: 13.25, durationMinutes: 28, mfe: 17, mae: 4 },
  { day: "2026-06-23", time: "11:41", symbol: "MNQ", side: "LONG", size: 7, entry: 22018, points: -18.25, durationMinutes: 16, mfe: 5, mae: 23 },
  { day: "2026-06-24", time: "09:37", symbol: "MNQ", side: "SHORT", size: 9, entry: 22068.5, points: 25.25, durationMinutes: 13, mfe: 30, mae: 7 },
  { day: "2026-06-24", time: "14:23", symbol: "MNQ", side: "LONG", size: 5, entry: 22020.25, points: 12.75, durationMinutes: 18, mfe: 16, mae: 5 },
  { day: "2026-06-25", time: "09:41", symbol: "MNQ", side: "LONG", size: 8, entry: 22118.25, points: -34.5, durationMinutes: 12, mfe: 7, mae: 42 },
  { day: "2026-06-25", time: "10:19", symbol: "MES", side: "SHORT", size: 5, entry: 5538, points: -10.5, durationMinutes: 18, mfe: 4, mae: 13 },
  { day: "2026-06-26", time: "09:46", symbol: "MNQ", side: "SHORT", size: 10, entry: 22082.5, points: 52.25, durationMinutes: 22, mfe: 64, mae: 9 },
  { day: "2026-06-26", time: "11:02", symbol: "MNQ", side: "LONG", size: 6, entry: 22004.75, points: 23.5, durationMinutes: 14, mfe: 28, mae: 6 },
  { day: "2026-06-29", time: "09:38", symbol: "MNQ", side: "LONG", size: 9, entry: 22148.25, points: 36.75, durationMinutes: 17, mfe: 42, mae: 8 },
  { day: "2026-06-29", time: "13:57", symbol: "MNQ", side: "SHORT", size: 7, entry: 22196.5, points: -19.75, durationMinutes: 11, mfe: 4, mae: 25 },
  { day: "2026-06-30", time: "09:34", symbol: "MNQ", side: "SHORT", size: 10, entry: 22235.75, points: 43.5, durationMinutes: 15, mfe: 51, mae: 8 },
  { day: "2026-06-30", time: "10:26", symbol: "MES", side: "SHORT", size: 4, entry: 5560.25, points: 8.75, durationMinutes: 19, mfe: 12, mae: 3 },
  { day: "2026-06-30", time: "14:11", symbol: "MNQ", side: "LONG", size: 5, entry: 22164.25, points: -12.5, durationMinutes: 9, mfe: 3, mae: 17 },
];

const DEMO_EXPENSES: ExpenseRecord[] = [
  {
    id: 501,
    account_id: PRIMARY_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-06-02",
    amount_cents: 4900,
    amount: 49,
    currency: "USD",
    category: "evaluation_fee",
    account_type: "standard",
    plan_size: "50k",
    description: "Demo 50K evaluation",
    tags: ["demo", "evaluation"],
    created_at: "2026-06-02T14:00:00.000Z",
    updated_at: "2026-06-02T14:00:00.000Z",
  },
  {
    id: 502,
    account_id: FOLLOWER_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-06-09",
    amount_cents: 14900,
    amount: 149,
    currency: "USD",
    category: "activation_fee",
    account_type: "standard",
    plan_size: "50k",
    description: "Demo activation",
    tags: ["demo", "activation"],
    created_at: "2026-06-09T14:00:00.000Z",
    updated_at: "2026-06-09T14:00:00.000Z",
  },
  {
    id: 503,
    account_id: null,
    provider: "Market data",
    expense_date: "2026-06-14",
    amount_cents: 3900,
    amount: 39,
    currency: "USD",
    category: "data_fee",
    account_type: null,
    plan_size: null,
    description: "Demo data subscription",
    tags: ["demo", "data"],
    created_at: "2026-06-14T14:00:00.000Z",
    updated_at: "2026-06-14T14:00:00.000Z",
  },
  {
    id: 504,
    account_id: SWING_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-06-20",
    amount_cents: 9900,
    amount: 99,
    currency: "USD",
    category: "reset_fee",
    account_type: "standard",
    plan_size: "100k",
    description: "Demo reset",
    tags: ["demo", "reset"],
    created_at: "2026-06-20T14:00:00.000Z",
    updated_at: "2026-06-20T14:00:00.000Z",
  },
];

const DEMO_PAYOUTS: PayoutRecord[] = [
  {
    id: 701,
    payout_date: "2026-06-28",
    amount_cents: 125000,
    amount: 1250,
    currency: "USD",
    notes: "Demo payout after consistency target",
    created_at: "2026-06-28T18:00:00.000Z",
    updated_at: "2026-06-28T18:00:00.000Z",
  },
  {
    id: 702,
    payout_date: "2026-05-31",
    amount_cents: 87500,
    amount: 875,
    currency: "USD",
    notes: "Demo prior-month payout",
    created_at: "2026-05-31T18:00:00.000Z",
    updated_at: "2026-05-31T18:00:00.000Z",
  },
];

function handled<T>(data: T): DemoApiResponse<T> {
  return { handled: true, data };
}

function readBooleanQuery(query: Record<string, DemoQueryValue> | undefined, key: string) {
  const value = query?.[key];
  return value === true || value === "true" || value === "1";
}

function readNumberQuery(query: Record<string, DemoQueryValue> | undefined, key: string) {
  const value = query?.[key];
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readStringQuery(query: Record<string, DemoQueryValue> | undefined, key: string) {
  const value = query?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function sum(values: number[]) {
  return values.reduce((total, value) => total + value, 0);
}

function average(values: number[]) {
  return values.length === 0 ? 0 : sum(values) / values.length;
}

function toEasternUtcIso(day: string, hhmm: string, durationMinutes = 0) {
  const [hours, minutes] = hhmm.split(":").map((value) => Number.parseInt(value, 10));
  const date = new Date(`${day}T00:00:00.000Z`);
  date.setUTCHours(hours + 4, minutes + durationMinutes, 0, 0);
  return date.toISOString();
}

function symbolPointValue(symbol: DemoTradeSpec["symbol"]) {
  if (symbol === "MES") {
    return 5;
  }
  if (symbol === "MGC") {
    return 10;
  }
  return 2;
}

function formatContractId(symbol: DemoTradeSpec["symbol"]) {
  return `CON.F.US.${symbol}.U26`;
}

function buildTrade(accountId: number, spec: DemoTradeSpec, index: number, pointScale = 1, sizeScale = 1): AccountTrade {
  const scaledPoints = Number((spec.points * pointScale).toFixed(spec.symbol === "MGC" ? 2 : 2));
  const size = Math.max(1, Math.round(spec.size * sizeScale));
  const entryTime = toEasternUtcIso(spec.day, spec.time);
  const exitTime = toEasternUtcIso(spec.day, spec.time, spec.durationMinutes);
  const exitPrice = spec.side === "LONG" ? spec.entry + scaledPoints : spec.entry - scaledPoints;
  const fees = Number((size * 1.34).toFixed(2));
  const pnl = Number((scaledPoints * symbolPointValue(spec.symbol) * size - fees).toFixed(2));

  return {
    id: accountId * 1000 + index + 1,
    account_id: accountId,
    contract_id: formatContractId(spec.symbol),
    symbol: spec.symbol,
    side: spec.side,
    size,
    price: Number(exitPrice.toFixed(spec.symbol === "MGC" ? 1 : 2)),
    timestamp: exitTime,
    entry_time: entryTime,
    exit_time: exitTime,
    duration_minutes: spec.durationMinutes,
    entry_price: spec.entry,
    exit_price: Number(exitPrice.toFixed(spec.symbol === "MGC" ? 1 : 2)),
    fees,
    pnl,
    mfe: spec.mfe,
    mae: spec.mae,
    order_id: `DEMO-${accountId}-${index + 1}`,
    source_trade_id: `DEMO-SRC-${accountId}-${index + 1}`,
  };
}

function buildTradesForAccount(accountId: number) {
  if (accountId === FOLLOWER_ACCOUNT_ID) {
    return PRIMARY_TRADE_SPECS.slice(0, 27).map((spec, index) =>
      buildTrade(accountId, spec, index, index % 7 === 0 ? 0.85 : 0.92, 0.55),
    );
  }

  if (accountId === SWING_ACCOUNT_ID) {
    return PRIMARY_TRADE_SPECS.filter((_, index) => index % 3 !== 1).map((spec, index) =>
      buildTrade(accountId, spec, index, index % 5 === 0 ? -0.65 : 0.7, 0.75),
    );
  }

  if (accountId === PRACTICE_ACCOUNT_ID) {
    return PRIMARY_TRADE_SPECS.slice(8, 22).map((spec, index) => buildTrade(accountId, spec, index, 0.5, 0.35));
  }

  return PRIMARY_TRADE_SPECS.map((spec, index) => buildTrade(PRIMARY_ACCOUNT_ID, spec, index));
}

const DEMO_TRADES_BY_ACCOUNT_ID = new Map<number, AccountTrade[]>(
  [PRIMARY_ACCOUNT_ID, FOLLOWER_ACCOUNT_ID, SWING_ACCOUNT_ID, PRACTICE_ACCOUNT_ID].map((accountId) => [
    accountId,
    buildTradesForAccount(accountId),
  ]),
);

function getRequestedAccountId(path: string) {
  const match = /^\/api\/accounts\/(\d+)(?:\/|$)/.exec(path);
  if (!match) {
    return PRIMARY_ACCOUNT_ID;
  }
  const parsed = Number.parseInt(match[1], 10);
  return DEMO_TRADES_BY_ACCOUNT_ID.has(parsed) ? parsed : PRIMARY_ACCOUNT_ID;
}

function getAccountTrades(accountId: number) {
  return DEMO_TRADES_BY_ACCOUNT_ID.get(accountId) ?? DEMO_TRADES_BY_ACCOUNT_ID.get(PRIMARY_ACCOUNT_ID) ?? [];
}

function filterByDateRange<
  T extends {
    entry_time?: string | null;
    entry_date?: string;
    timestamp?: string;
    date?: string;
    expense_date?: string;
    payout_date?: string;
  },
>(
  rows: T[],
  query: Record<string, DemoQueryValue> | undefined,
) {
  const start = readStringQuery(query, "start") || readStringQuery(query, "start_date");
  const end = readStringQuery(query, "end") || readStringQuery(query, "end_date");
  const startMs = start ? Date.parse(start.includes("T") ? start : `${start}T00:00:00.000Z`) : null;
  const endMs = end ? Date.parse(end.includes("T") ? end : `${end}T23:59:59.999Z`) : null;

  return rows.filter((row) => {
    const value = row.entry_time ?? row.entry_date ?? row.timestamp ?? row.date ?? row.expense_date ?? row.payout_date;
    if (!value) {
      return true;
    }
    const rowMs = Date.parse(value.includes("T") ? value : `${value}T12:00:00.000Z`);
    if (startMs !== null && rowMs < startMs) {
      return false;
    }
    if (endMs !== null && rowMs > endMs) {
      return false;
    }
    return true;
  });
}

function limitAndOffset<T>(rows: T[], query: Record<string, DemoQueryValue> | undefined, defaultLimit: number) {
  const limit = readNumberQuery(query, "limit") ?? defaultLimit;
  const offset = readNumberQuery(query, "offset") ?? 0;
  return rows.slice(offset, offset + limit);
}

function filterTrades(accountId: number, query: Record<string, DemoQueryValue> | undefined) {
  const symbol = readStringQuery(query, "symbol").trim().toLowerCase();
  let rows = filterByDateRange(getAccountTrades(accountId), query);
  if (symbol) {
    rows = rows.filter((trade) => `${trade.symbol} ${trade.contract_id}`.toLowerCase().includes(symbol));
  }
  rows = [...rows].sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
  return limitAndOffset(rows, query, 200);
}

function getTradeDay(trade: AccountTrade) {
  return (trade.entry_time ?? trade.timestamp).slice(0, 10);
}

function buildCalendarDays(trades: AccountTrade[]): AccountPnlCalendarDay[] {
  const byDate = new Map<string, AccountPnlCalendarDay>();
  for (const trade of trades) {
    const date = getTradeDay(trade);
    const current = byDate.get(date) ?? {
      date,
      trade_count: 0,
      gross_pnl: 0,
      fees: 0,
      net_pnl: 0,
    };
    current.trade_count += 1;
    current.fees = Number((current.fees + Math.abs(trade.fees)).toFixed(2));
    current.net_pnl = Number((current.net_pnl + (trade.pnl ?? 0)).toFixed(2));
    current.gross_pnl = Number((current.net_pnl + current.fees).toFixed(2));
    byDate.set(date, current);
  }
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function computeMaxDrawdown(days: AccountPnlCalendarDay[]) {
  let equity = 0;
  let peak = 0;
  let maxDrawdown = 0;
  const drawdowns: number[] = [];
  for (const day of days) {
    equity += day.net_pnl;
    peak = Math.max(peak, equity);
    const drawdown = equity - peak;
    drawdowns.push(drawdown);
    maxDrawdown = Math.min(maxDrawdown, drawdown);
  }
  const activeDrawdowns = drawdowns.filter((value) => value < 0);
  return {
    maxDrawdown,
    averageDrawdown: activeDrawdowns.length === 0 ? 0 : average(activeDrawdowns),
  };
}

function buildSummary(accountId: number, query: Record<string, DemoQueryValue> | undefined): AccountSummary {
  const trades = filterTrades(accountId, { ...query, limit: 1000, offset: 0 });
  const calendarDays = buildCalendarDays(trades);
  const pnlValues = trades.map((trade) => trade.pnl ?? 0);
  const wins = pnlValues.filter((value) => value > 0);
  const losses = pnlValues.filter((value) => value < 0);
  const breakevenCount = pnlValues.filter((value) => value === 0).length;
  const fees = sum(trades.map((trade) => Math.abs(trade.fees)));
  const netPnl = sum(pnlValues);
  const grossPnl = netPnl + fees;
  const winSum = sum(wins);
  const lossAbs = Math.abs(sum(losses));
  const activeDays = calendarDays.length;
  const greenDays = calendarDays.filter((day) => day.net_pnl > 0).length;
  const redDays = calendarDays.filter((day) => day.net_pnl < 0).length;
  const flatDays = calendarDays.filter((day) => day.net_pnl === 0).length;
  const durations = trades
    .map((trade) => trade.duration_minutes)
    .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value));
  const winDurations = trades
    .filter((trade) => (trade.pnl ?? 0) > 0)
    .map((trade) => trade.duration_minutes)
    .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value));
  const lossDurations = trades
    .filter((trade) => (trade.pnl ?? 0) < 0)
    .map((trade) => trade.duration_minutes)
    .filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value));
  const sizes = trades.map((trade) => Math.abs(trade.size)).sort((left, right) => left - right);
  const pointMoves = trades
    .map((trade) => {
      const entry = trade.entry_price ?? trade.price;
      const exit = trade.exit_price ?? trade.price;
      return trade.side === "LONG" ? exit - entry : entry - exit;
    })
    .filter((value) => Number.isFinite(value));
  const pointWins = pointMoves.filter((value) => value > 0).map(Math.abs);
  const pointLosses = pointMoves.filter((value) => value < 0).map(Math.abs);
  const { maxDrawdown, averageDrawdown } = computeMaxDrawdown(calendarDays);
  const averageSize = average(sizes);
  const benchmarkNetPnl = netPnl * 0.88;
  const benchmarkDiff = netPnl - benchmarkNetPnl;

  return {
    realized_pnl: Number(netPnl.toFixed(2)),
    gross_pnl: Number(grossPnl.toFixed(2)),
    fees: Number(fees.toFixed(2)),
    net_pnl: Number(netPnl.toFixed(2)),
    win_rate: trades.length === 0 ? 0 : (wins.length / trades.length) * 100,
    win_count: wins.length,
    loss_count: losses.length,
    breakeven_count: breakevenCount,
    profit_factor: lossAbs > 0 ? winSum / lossAbs : 0,
    avg_win: wins.length === 0 ? 0 : average(wins),
    avg_loss: losses.length === 0 ? 0 : average(losses),
    avg_win_duration_minutes: average(winDurations),
    avg_loss_duration_minutes: average(lossDurations),
    expectancy_per_trade: trades.length === 0 ? 0 : netPnl / trades.length,
    tail_risk_5pct: losses.length === 0 ? 0 : Math.min(...losses),
    max_drawdown: maxDrawdown,
    average_drawdown: averageDrawdown,
    risk_drawdown_score: Math.max(0, 100 - Math.abs(maxDrawdown) / 45),
    max_drawdown_length_hours: maxDrawdown === 0 ? 0 : 5.5,
    recovery_time_hours: maxDrawdown === 0 ? 0 : 8.25,
    average_recovery_length_hours: maxDrawdown === 0 ? 0 : 3.4,
    trade_count: trades.length,
    half_turn_count: trades.reduce((total, trade) => total + Math.abs(trade.size), 0),
    execution_count: trades.length * 2,
    day_win_rate: activeDays === 0 ? 0 : (greenDays / activeDays) * 100,
    green_days: greenDays,
    red_days: redDays,
    flat_days: flatDays,
    avg_trades_per_day: activeDays === 0 ? 0 : trades.length / activeDays,
    active_days: activeDays,
    efficiency_per_hour: durations.length === 0 ? 0 : netPnl / (sum(durations) / 60),
    profit_per_day: activeDays === 0 ? 0 : netPnl / activeDays,
    averagePositionSize: averageSize,
    medianPositionSize: sizes.length === 0 ? 0 : sizes[Math.floor(sizes.length / 2)],
    tradeCountUsedForSizingStats: trades.length,
    avgPointGain: pointWins.length === 0 ? null : average(pointWins),
    avgPointLoss: pointLosses.length === 0 ? null : average(pointLosses),
    pointsBasisUsed: "MNQ",
    sizingBenchmark: {
      benchmarkMode: "fixed_average_size",
      benchmarkSizeUsed: Number(averageSize.toFixed(1)),
      benchmarkGrossPnl: Number((benchmarkNetPnl + fees).toFixed(2)),
      benchmarkNetPnl: Number(benchmarkNetPnl.toFixed(2)),
      benchmarkDiff: Number(benchmarkDiff.toFixed(2)),
      benchmarkRatio: benchmarkNetPnl > 0 ? netPnl / benchmarkNetPnl : null,
      benchmarkLabel: Math.abs(benchmarkDiff) < Math.max(100, Math.abs(netPnl) * 0.05) ? "In Line With Benchmark" : "Above Benchmark",
    },
  };
}

function buildSummaryWithPointBases(accountId: number, query: Record<string, DemoQueryValue> | undefined): AccountSummaryWithPointBases {
  const summary = buildSummary(accountId, query);
  return {
    summary,
    point_payoff_by_basis: {
      MNQ: {
        avgPointGain: summary.avgPointGain,
        avgPointLoss: summary.avgPointLoss,
      },
      MES: {
        avgPointGain: summary.avgPointGain === null ? null : summary.avgPointGain / 4,
        avgPointLoss: summary.avgPointLoss === null ? null : summary.avgPointLoss / 4,
      },
      MGC: {
        avgPointGain: summary.avgPointGain === null ? null : summary.avgPointGain / 10,
        avgPointLoss: summary.avgPointLoss === null ? null : summary.avgPointLoss / 10,
      },
      SIL: {
        avgPointGain: summary.avgPointGain === null ? null : summary.avgPointGain / 100,
        avgPointLoss: summary.avgPointLoss === null ? null : summary.avgPointLoss / 100,
      },
    },
  };
}

function buildJournalEntries(accountId: number): JournalEntry[] {
  const calendarDays = buildCalendarDays(getAccountTrades(accountId));
  return calendarDays
    .filter((day) => ["2026-06-12", "2026-06-17", "2026-06-22", "2026-06-26", "2026-06-30"].includes(day.date))
    .map((day, index) => ({
      id: accountId * 10 + index + 1,
      account_id: accountId,
      entry_date: day.date,
      title: ["Opening drive review", "Patience after reversal", "Copy-trade check", "Best setup day", "Month-end review"][index] ?? "Demo review",
      mood: (["Focused", "Neutral", "Confident", "Focused", "Confident"] as const)[index] ?? "Focused",
      tags: [["trend", "discipline"], ["reversal"], ["copy-trade"], ["A-setup"], ["review"]][index] ?? ["demo"],
      body:
        "Demo note: waited for confirmation, sized within plan, and avoided adding after the first pullback. Review the entry timing and stop discipline.",
      version: 1,
      stats_source: "demo",
      stats_json: {
        snapshot_version: 1,
        trade_count: day.trade_count,
        total_pnl: day.gross_pnl,
        total_fees: day.fees,
        win_rate: 66.7,
        avg_win: day.net_pnl > 0 ? day.net_pnl / Math.max(1, day.trade_count) : 0,
        avg_loss: day.net_pnl < 0 ? day.net_pnl / Math.max(1, day.trade_count) : 0,
        largest_win: Math.max(day.net_pnl, 0),
        largest_loss: Math.min(day.net_pnl, 0),
        largest_position_size: 10,
        gross: day.gross_pnl,
        net: day.net_pnl,
        net_realized_pnl: day.net_pnl,
      },
      stats_pulled_at: `${day.date}T21:00:00.000Z`,
      is_archived: false,
      created_at: `${day.date}T21:00:00.000Z`,
      updated_at: `${day.date}T21:00:00.000Z`,
    }));
}

function filterJournalEntries(accountId: number, query: Record<string, DemoQueryValue> | undefined): JournalEntriesResponse {
  let entries = filterByDateRange(buildJournalEntries(accountId), query);
  const mood = readStringQuery(query, "mood");
  if (mood) {
    entries = entries.filter((entry) => entry.mood === mood);
  }
  entries = entries.sort((left, right) => right.entry_date.localeCompare(left.entry_date));
  return {
    items: limitAndOffset(entries, query, 20),
    total: entries.length,
  };
}

function buildExpenseTotals(rows: ExpenseRecord[], range: ExpenseRange): ExpenseTotals {
  const byCategory: ExpenseTotals["by_category"] = {};
  for (const row of rows) {
    const current = byCategory[row.category] ?? {
      amount: 0,
      amount_cents: 0,
      count: 0,
    };
    current.amount += row.amount;
    current.amount_cents += row.amount_cents;
    current.count += 1;
    byCategory[row.category] = current;
  }
  const totalCents = sum(rows.map((row) => row.amount_cents));
  return {
    range,
    start_date: rows[rows.length - 1]?.expense_date ?? null,
    end_date: rows[0]?.expense_date ?? "2026-06-30",
    total_amount: totalCents / 100,
    total_amount_cents: totalCents,
    by_category: byCategory,
    count: rows.length,
  };
}

function buildPayoutTotals(rows: PayoutRecord[]): PayoutTotals {
  const totalCents = sum(rows.map((row) => row.amount_cents));
  return {
    total_amount: totalCents / 100,
    total_amount_cents: totalCents,
    average_amount: rows.length === 0 ? 0 : totalCents / rows.length / 100,
    average_amount_cents: rows.length === 0 ? 0 : Math.round(totalCents / rows.length),
    count: rows.length,
  };
}

function buildDemoBotConfig(accountId = PRIMARY_ACCOUNT_ID): BotConfig {
  return {
    id: 8101,
    name: "Demo MNQ Pullback Bot",
    account_id: accountId,
    provider: "projectx",
    enabled: true,
    execution_mode: "dry_run",
    strategy_type: "ema_scalping",
    strategy_params: {},
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    timeframe_unit: "minute",
    timeframe_unit_number: 5,
    lookback_bars: 240,
    fast_period: 9,
    slow_period: 15,
    order_size: 2,
    max_contracts: 4,
    max_daily_loss: 650,
    max_trades_per_day: 5,
    max_open_position: 2,
    allowed_contracts: ["CON.F.US.MNQ.U26"],
    trading_start_time: "09:30",
    trading_end_time: "15:45",
    cooldown_seconds: 240,
    max_data_staleness_seconds: 600,
    allow_market_depth: false,
    created_at: "2026-06-20T14:00:00.000Z",
    updated_at: "2026-06-30T15:45:00.000Z",
  };
}

function buildDemoBotActivity(): BotActivity {
  const config = buildDemoBotConfig();
  const runs: BotRun[] = [
    {
      id: 9001,
      bot_config_id: config.id,
      account_id: config.account_id,
      status: "stopped",
      dry_run: true,
      started_at: "2026-06-30T13:30:00.000Z",
      stopped_at: "2026-06-30T15:45:00.000Z",
      stop_reason: "session_end",
      last_heartbeat_at: "2026-06-30T15:45:00.000Z",
    },
  ];
  const decisions: BotDecision[] = [
    {
      id: 9101,
      bot_config_id: config.id,
      bot_run_id: runs[0].id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      symbol: config.symbol,
      decision_type: "signal",
      action: "SELL",
      reason: "Demo short signal after failed reclaim of VWAP.",
      candle_timestamp: "2026-06-30T14:05:00.000Z",
      price: 22235.75,
      quantity: 2,
      raw_payload: null,
      created_at: "2026-06-30T14:05:03.000Z",
    },
    {
      id: 9102,
      bot_config_id: config.id,
      bot_run_id: runs[0].id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      symbol: config.symbol,
      decision_type: "risk_check",
      action: "HOLD",
      reason: "Demo hold: cooldown active after profitable exit.",
      candle_timestamp: "2026-06-30T14:25:00.000Z",
      price: 22192.25,
      quantity: null,
      raw_payload: null,
      created_at: "2026-06-30T14:25:02.000Z",
    },
  ];
  const order_attempts: BotOrderAttempt[] = [
    {
      id: 9201,
      bot_config_id: config.id,
      bot_run_id: runs[0].id,
      bot_decision_id: decisions[0].id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      side: "SELL",
      order_type: "market",
      size: 2,
      status: "dry_run",
      provider_order_id: "DEMO-ORDER-9201",
      rejection_reason: null,
      created_at: "2026-06-30T14:05:04.000Z",
      updated_at: "2026-06-30T14:05:04.000Z",
    },
  ];
  const risk_events: BotRiskEvent[] = [
    {
      id: 9301,
      bot_config_id: config.id,
      bot_run_id: runs[0].id,
      account_id: config.account_id,
      severity: "info",
      code: "demo_max_daily_loss_ok",
      message: "Demo risk check passed with available daily loss buffer.",
      created_at: "2026-06-30T14:05:02.000Z",
    },
  ];
  return {
    config,
    runs,
    decisions,
    order_attempts,
    risk_events,
  };
}

function buildDemoCandles(query: Record<string, DemoQueryValue> | undefined): ProjectXMarketCandle[] {
  const symbol = readStringQuery(query, "symbol") || "MNQ";
  const contractId = readStringQuery(query, "contract_id") || `CON.F.US.${symbol}.U26`;
  const unit = (readStringQuery(query, "unit") || "minute") as ProjectXMarketCandle["unit"];
  const unitNumber = readNumberQuery(query, "unit_number") ?? 5;
  const limit = Math.min(readNumberQuery(query, "limit") ?? 160, 240);
  const start = Date.parse("2026-06-30T13:30:00.000Z");

  return Array.from({ length: limit }, (_, index) => {
    const timestamp = new Date(start + index * unitNumber * 60_000).toISOString();
    const wave = Math.sin(index / 7) * 16 + Math.cos(index / 17) * 9;
    const open = 22200 + wave + index * 0.18;
    const close = open + Math.sin(index / 4) * 8;
    const high = Math.max(open, close) + 6 + (index % 5);
    const low = Math.min(open, close) - 6 - (index % 4);
    return {
      id: 10000 + index,
      contract_id: contractId,
      symbol,
      live: false,
      unit,
      unit_number: unitNumber,
      timestamp,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: 1200 + index * 12 + (index % 8) * 150,
      is_partial: index === limit - 1,
      fetched_at: "2026-06-30T20:00:00.000Z",
    };
  });
}

function buildLegacySummary(accountId: number): SummaryMetrics {
  const summary = buildSummary(accountId, undefined);
  return {
    trade_count: summary.trade_count,
    net_pnl: summary.net_pnl,
    win_rate: summary.win_rate,
    profit_factor: summary.profit_factor,
    expectancy: summary.expectancy_per_trade,
    average_win: summary.avg_win,
    average_loss: summary.avg_loss,
    average_win_loss_ratio: summary.avg_loss === 0 ? 0 : Math.abs(summary.avg_win / summary.avg_loss),
    max_drawdown: summary.max_drawdown,
    largest_losing_trade: summary.tail_risk_5pct,
    average_hold_minutes: average(getAccountTrades(accountId).map((trade) => trade.duration_minutes ?? 0)),
    average_hold_minutes_winners: summary.avg_win_duration_minutes,
    average_hold_minutes_losers: summary.avg_loss_duration_minutes,
  };
}

function buildLegacyTrades(accountId: number): TradeRecord[] {
  return getAccountTrades(accountId).map((trade) => ({
    id: trade.id,
    account_id: trade.account_id,
    symbol: trade.symbol,
    side: trade.side === "LONG" ? "LONG" : "SHORT",
    opened_at: trade.entry_time ?? trade.timestamp,
    closed_at: trade.exit_time ?? trade.timestamp,
    qty: trade.size,
    entry_price: trade.entry_price ?? trade.price,
    exit_price: trade.exit_price ?? trade.price,
    pnl: trade.pnl,
    fees: trade.fees,
    notes: null,
    is_rule_break: false,
    rule_break_type: null,
  }));
}

export function getDemoApiResponse<T>(
  path: string,
  query?: Record<string, DemoQueryValue>,
): DemoApiResponse<T> | null {
  if (path === "/api/auth/me") {
    return handled<AuthMe>({ user_id: DEMO_USER_ID, email: "demo@topsignal.local" }) as DemoApiResponse<T>;
  }

  if (path === "/api/me/providers/projectx/credentials/status") {
    return handled<ProjectXCredentialsStatus>({ configured: true }) as DemoApiResponse<T>;
  }

  if (path === "/api/accounts") {
    const showInactive = readBooleanQuery(query, "show_inactive");
    const showMissing = readBooleanQuery(query, "show_missing");
    const accounts = DEMO_ACCOUNTS.filter((account) => {
      if (account.account_state === "MISSING") {
        return showMissing;
      }
      if (account.account_state === "HIDDEN") {
        return showInactive;
      }
      if (account.account_state === "LOCKED_OUT") {
        return showInactive;
      }
      return account.account_state === "ACTIVE";
    });
    return handled<AccountInfo[]>(accounts) as DemoApiResponse<T>;
  }

  const accountId = getRequestedAccountId(path);

  if (/^\/api\/accounts\/\d+\/last-trade$/.test(path)) {
    const account = DEMO_ACCOUNTS.find((candidate) => candidate.id === accountId) ?? DEMO_ACCOUNTS[0];
    return handled<AccountLastTradeInfo>({
      account_id: accountId,
      last_trade_at: account.last_trade_at,
      source: "demo",
    }) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/trades$/.test(path)) {
    return handled<AccountTrade[]>(filterTrades(accountId, query)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/summary$/.test(path)) {
    return handled<AccountSummary>(buildSummary(accountId, query)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/summary-with-point-bases$/.test(path)) {
    return handled<AccountSummaryWithPointBases>(buildSummaryWithPointBases(accountId, query)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/pnl-calendar$/.test(path)) {
    const trades = filterTrades(accountId, { ...query, limit: 1000, offset: 0 });
    return handled<AccountPnlCalendarDay[]>(buildCalendarDays(trades)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/journal\/days$/.test(path)) {
    const days = filterByDateRange(buildJournalEntries(accountId), query).map((entry) => entry.entry_date);
    return handled<JournalDaysResponse>({ days }) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/journal\/\d+\/images$/.test(path)) {
    return handled([]) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/journal$/.test(path)) {
    return handled<JournalEntriesResponse>(filterJournalEntries(accountId, query)) as DemoApiResponse<T>;
  }

  if (path === "/api/expenses/totals") {
    const rows = filterByDateRange(DEMO_EXPENSES, query).sort((left, right) => right.expense_date.localeCompare(left.expense_date));
    return handled<ExpenseTotals>(buildExpenseTotals(rows, (readStringQuery(query, "range") || "all_time") as ExpenseRange)) as DemoApiResponse<T>;
  }

  if (path === "/api/expenses") {
    const category = readStringQuery(query, "category") as ExpenseCategory | "";
    let rows = filterByDateRange(DEMO_EXPENSES, query).sort((left, right) => right.expense_date.localeCompare(left.expense_date));
    if (category) {
      rows = rows.filter((row) => row.category === category);
    }
    return handled<ExpenseListResponse>({
      items: limitAndOffset(rows, query, 200),
      total: rows.length,
    }) as DemoApiResponse<T>;
  }

  if (path === "/api/payouts/totals") {
    return handled<PayoutTotals>(buildPayoutTotals(filterByDateRange(DEMO_PAYOUTS, query))) as DemoApiResponse<T>;
  }

  if (path === "/api/payouts") {
    const rows = filterByDateRange(DEMO_PAYOUTS, query).sort((left, right) => right.payout_date.localeCompare(left.payout_date));
    return handled<PayoutListResponse>({
      items: limitAndOffset(rows, query, 200),
      total: rows.length,
    }) as DemoApiResponse<T>;
  }

  if (path === "/api/bots") {
    const accountQuery = readNumberQuery(query, "account_id");
    const config = buildDemoBotConfig(accountQuery ?? PRIMARY_ACCOUNT_ID);
    return handled<BotConfigListResponse>({ items: [config], total: 1 }) as DemoApiResponse<T>;
  }

  if (/^\/api\/bots\/\d+\/activity$/.test(path)) {
    return handled<BotActivity>(buildDemoBotActivity()) as DemoApiResponse<T>;
  }

  if (path === "/api/projectx/contracts/search") {
    const searchText = readStringQuery(query, "search_text") || "MNQ";
    const contracts: ProjectXContract[] = ["MNQ", "MES", "MGC"]
      .filter((symbol) => symbol.includes(searchText.toUpperCase()) || searchText.trim() === "")
      .map((symbol) => ({
        id: `CON.F.US.${symbol}.U26`,
        name: `${symbol} Sep 2026`,
        description: `Demo ${symbol} futures contract`,
        tick_size: symbol === "MGC" ? 0.1 : 0.25,
        tick_value: symbol === "MNQ" ? 0.5 : symbol === "MES" ? 1.25 : 1,
        active_contract: true,
        symbol_id: symbol,
      }));
    return handled<ProjectXContract[]>(contracts.length > 0 ? contracts : [
      {
        id: "CON.F.US.MNQ.U26",
        name: "MNQ Sep 2026",
        description: "Demo MNQ futures contract",
        tick_size: 0.25,
        tick_value: 0.5,
        active_contract: true,
        symbol_id: "MNQ",
      },
    ]) as DemoApiResponse<T>;
  }

  if (path === "/api/projectx/candles") {
    return handled<ProjectXMarketCandle[]>(buildDemoCandles(query)) as DemoApiResponse<T>;
  }

  if (path === "/metrics/summary") {
    return handled<SummaryMetrics>(buildLegacySummary(PRIMARY_ACCOUNT_ID)) as DemoApiResponse<T>;
  }

  if (path === "/metrics/pnl-by-hour") {
    const byHour = new Map<number, HourPnlPoint>();
    for (const trade of getAccountTrades(PRIMARY_ACCOUNT_ID)) {
      const hour = new Date(trade.entry_time ?? trade.timestamp).getUTCHours() - 4;
      const normalizedHour = hour < 0 ? hour + 24 : hour;
      const current = byHour.get(normalizedHour) ?? { hour: normalizedHour, trade_count: 0, pnl: 0 };
      current.trade_count += 1;
      current.pnl += trade.pnl ?? 0;
      byHour.set(normalizedHour, current);
    }
    return handled<HourPnlPoint[]>([...byHour.values()].sort((left, right) => left.hour - right.hour)) as DemoApiResponse<T>;
  }

  if (path === "/metrics/pnl-by-day") {
    const labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const byDay = new Map<number, DayPnlPoint>();
    for (const trade of getAccountTrades(PRIMARY_ACCOUNT_ID)) {
      const day = new Date(trade.entry_time ?? trade.timestamp).getUTCDay();
      const current = byDay.get(day) ?? { day_of_week: day, day_label: labels[day], trade_count: 0, pnl: 0 };
      current.trade_count += 1;
      current.pnl += trade.pnl ?? 0;
      byDay.set(day, current);
    }
    return handled<DayPnlPoint[]>([...byDay.values()].sort((left, right) => left.day_of_week - right.day_of_week)) as DemoApiResponse<T>;
  }

  if (path === "/metrics/pnl-by-symbol") {
    const bySymbol = new Map<string, SymbolPnlPoint>();
    for (const trade of getAccountTrades(PRIMARY_ACCOUNT_ID)) {
      const current = bySymbol.get(trade.symbol) ?? { symbol: trade.symbol, trade_count: 0, pnl: 0, win_rate: 0 };
      current.trade_count += 1;
      current.pnl += trade.pnl ?? 0;
      bySymbol.set(trade.symbol, current);
    }
    return handled<SymbolPnlPoint[]>(
      [...bySymbol.values()].map((row) => ({
        ...row,
        win_rate:
          (getAccountTrades(PRIMARY_ACCOUNT_ID).filter((trade) => trade.symbol === row.symbol && (trade.pnl ?? 0) > 0).length /
            Math.max(1, row.trade_count)) *
          100,
      })),
    ) as DemoApiResponse<T>;
  }

  if (path === "/metrics/streaks") {
    return handled<StreakMetrics>({
      current_win_streak: 1,
      current_loss_streak: 0,
      longest_win_streak: 5,
      longest_loss_streak: 2,
      pnl_after_losses: [
        { loss_streak: 1, trade_count: 7, total_pnl: 1180.5, average_pnl: 168.64 },
        { loss_streak: 2, trade_count: 2, total_pnl: 230.25, average_pnl: 115.13 },
      ],
    }) as DemoApiResponse<T>;
  }

  if (path === "/metrics/behavior") {
    const summary = buildSummary(PRIMARY_ACCOUNT_ID, undefined);
    return handled<BehaviorMetrics>({
      trade_count: summary.trade_count,
      average_position_size: summary.averagePositionSize,
      max_position_size: Math.max(...getAccountTrades(PRIMARY_ACCOUNT_ID).map((trade) => trade.size)),
      rule_break_count: 2,
      rule_break_pnl: -210.5,
      rule_following_pnl: summary.net_pnl + 210.5,
    }) as DemoApiResponse<T>;
  }

  if (path === "/trades") {
    return handled<TradeRecord[]>(buildLegacyTrades(PRIMARY_ACCOUNT_ID)) as DemoApiResponse<T>;
  }

  return null;
}
