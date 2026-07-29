import { DEMO_AS_OF_DATE, DEMO_AS_OF_ISO, DEMO_SCENARIO_VERSION } from "./demoScenario";
import type {
  AccountInfo,
  AccountLastTradeInfo,
  AccountPnlCalendarDay,
  AccountSizingBenchmark,
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
  FinancialSummary,
  FinancialSummaryRange,
  HourPnlPoint,
  JournalDaysResponse,
  JournalEntriesResponse,
  JournalEntry,
  JournalMood,
  PayoutListResponse,
  PayoutRecord,
  PayoutTotals,
  PointsBasis,
  ProjectXContract,
  ProjectXCredentialsStatus,
  ProjectXMarketCandle,
  SizingBenchmarkLabel,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
} from "./types";

export { DEMO_AS_OF_DATE, DEMO_AS_OF_ISO, DEMO_AS_OF_LABEL, DEMO_SCENARIO_VERSION } from "./demoScenario";

type DemoQueryValue = string | number | boolean | null | undefined;
type DemoSymbol = "MNQ" | "MES" | "MGC";

interface DemoApiResponse<T> {
  handled: true;
  data: T;
}

interface DemoTradeSpec {
  entryDay: string;
  entryTime: string;
  exitDay?: string;
  exitTime?: string;
  durationMinutes?: number;
  symbol: DemoSymbol;
  side: "LONG" | "SHORT";
  size: number;
  entry: number;
  points: number;
  mfePoints: number;
  maePoints: number;
  ruleBreakType?: string;
}

interface JournalNote {
  day: string;
  title: string;
  mood: JournalMood;
  tags: string[];
  body: string;
}

interface DrawdownEpisode {
  peakEquity: number;
  startMs: number;
  troughMs: number;
  endMs: number | null;
  troughDrawdown: number;
}

const DEMO_USER_ID = "00000000-0000-4000-9000-000000000001";
const PRIMARY_ACCOUNT_ID = 910001;
const FOLLOWER_ACCOUNT_ID = 910002;
const SWING_ACCOUNT_ID = 910003;
const PRACTICE_ACCOUNT_ID = 910004;
const ARCHIVED_ACCOUNT_ID = 910005;
const MISSING_ACCOUNT_ID = 910099;

const POINT_VALUES: Record<PointsBasis, number> = {
  MNQ: 2,
  MES: 5,
  NQ: 20,
  ES: 50,
  MGC: 10,
  SIL: 1_000,
};

const FEE_RATES: Record<DemoSymbol, { nonCommission: number; commission: number }> = {
  MNQ: { nonCommission: 0.74, commission: 0.6 },
  MES: { nonCommission: 0.74, commission: 0.6 },
  MGC: { nonCommission: 1.04, commission: 0.8 },
};

const PRIMARY_TRADE_SPECS: DemoTradeSpec[] = [
  { entryDay: "2026-07-02", entryTime: "09:36", symbol: "MNQ", side: "LONG", size: 3, entry: 23084.25, points: 18.5, durationMinutes: 16, mfePoints: 25, maePoints: 7.5 },
  { entryDay: "2026-07-02", entryTime: "10:18", symbol: "MNQ", side: "SHORT", size: 2, entry: 23114.5, points: -11.25, durationMinutes: 9, mfePoints: 4.5, maePoints: 15.5 },
  { entryDay: "2026-07-06", entryTime: "09:41", symbol: "MES", side: "LONG", size: 2, entry: 5808.25, points: 8.5, durationMinutes: 27, mfePoints: 12.25, maePoints: 3.5 },
  { entryDay: "2026-07-06", entryTime: "11:06", symbol: "MNQ", side: "LONG", size: 4, entry: 23148.75, points: 14.75, durationMinutes: 18, mfePoints: 21.25, maePoints: 6 },
  { entryDay: "2026-07-07", entryTime: "09:38", symbol: "MNQ", side: "SHORT", size: 3, entry: 23206.5, points: -17, durationMinutes: 13, mfePoints: 5.25, maePoints: 23 },
  { entryDay: "2026-07-07", entryTime: "10:27", symbol: "MNQ", side: "SHORT", size: 2, entry: 23242.75, points: 22.5, durationMinutes: 22, mfePoints: 29.5, maePoints: 7.25 },
  { entryDay: "2026-07-08", entryTime: "09:34", symbol: "MNQ", side: "LONG", size: 5, entry: 23192.25, points: 26, durationMinutes: 20, mfePoints: 34.25, maePoints: 8 },
  { entryDay: "2026-07-08", entryTime: "13:18", symbol: "MES", side: "SHORT", size: 1, entry: 5832.75, points: -6.5, durationMinutes: 31, mfePoints: 2.25, maePoints: 9.5 },
  { entryDay: "2026-07-09", entryTime: "09:47", symbol: "MNQ", side: "SHORT", size: 3, entry: 23318.5, points: 31, durationMinutes: 25, mfePoints: 39, maePoints: 9.25 },
  { entryDay: "2026-07-09", entryTime: "11:14", symbol: "MNQ", side: "LONG", size: 2, entry: 23238.25, points: 12.5, durationMinutes: 14, mfePoints: 18.75, maePoints: 5 },
  { entryDay: "2026-07-10", entryTime: "09:51", symbol: "MNQ", side: "LONG", size: 4, entry: 23376.75, points: -13, durationMinutes: 11, mfePoints: 3.5, maePoints: 18.75, ruleBreakType: "moved_stop" },
  { entryDay: "2026-07-10", entryTime: "10:22", symbol: "MES", side: "SHORT", size: 2, entry: 5850.5, points: -4.5, durationMinutes: 8, mfePoints: 1.25, maePoints: 7.75, ruleBreakType: "revenge_trade" },
  { entryDay: "2026-07-13", entryTime: "09:35", symbol: "MNQ", side: "LONG", size: 3, entry: 23284, points: 38, durationMinutes: 32, mfePoints: 46.5, maePoints: 7 },
  { entryDay: "2026-07-13", entryTime: "13:08", symbol: "MNQ", side: "SHORT", size: 3, entry: 23386.25, points: 15, durationMinutes: 19, mfePoints: 20.25, maePoints: 5.75 },
  { entryDay: "2026-07-14", entryTime: "09:43", symbol: "MES", side: "LONG", size: 2, entry: 5862.25, points: 11, durationMinutes: 36, mfePoints: 14.75, maePoints: 3.25 },
  { entryDay: "2026-07-15", entryTime: "09:39", symbol: "MNQ", side: "SHORT", size: 4, entry: 23422.5, points: -21, durationMinutes: 17, mfePoints: 5, maePoints: 28 },
  { entryDay: "2026-07-15", entryTime: "11:17", symbol: "MNQ", side: "LONG", size: 2, entry: 23358.75, points: 33, durationMinutes: 28, mfePoints: 41.25, maePoints: 6.5 },
  { entryDay: "2026-07-16", entryTime: "09:46", symbol: "MNQ", side: "SHORT", size: 3, entry: 23506, points: 27, durationMinutes: 21, mfePoints: 35.75, maePoints: 8.25 },
  { entryDay: "2026-07-16", entryTime: "13:24", symbol: "MES", side: "LONG", size: 1, entry: 5884.75, points: -8, durationMinutes: 42, mfePoints: 2.5, maePoints: 11.5 },
  { entryDay: "2026-07-17", entryTime: "09:33", symbol: "MNQ", side: "LONG", size: 5, entry: 23468.25, points: 35, durationMinutes: 24, mfePoints: 43.5, maePoints: 7.75 },
  { entryDay: "2026-07-20", entryTime: "09:42", symbol: "MNQ", side: "SHORT", size: 4, entry: 23612.75, points: 24, durationMinutes: 19, mfePoints: 30.5, maePoints: 6.25 },
  { entryDay: "2026-07-20", entryTime: "12:36", symbol: "MES", side: "SHORT", size: 2, entry: 5914.5, points: -10, durationMinutes: 33, mfePoints: 3.25, maePoints: 14.25 },
  { entryDay: "2026-07-21", entryTime: "09:37", symbol: "MNQ", side: "LONG", size: 3, entry: 23542.25, points: 43, durationMinutes: 29, mfePoints: 52.25, maePoints: 9 },
  { entryDay: "2026-07-22", entryTime: "09:54", symbol: "MNQ", side: "LONG", size: 2, entry: 23704.5, points: -18, durationMinutes: 12, mfePoints: 4.25, maePoints: 24 },
  { entryDay: "2026-07-23", entryTime: "09:45", symbol: "MES", side: "SHORT", size: 3, entry: 5942.25, points: 12.5, durationMinutes: 34, mfePoints: 17.25, maePoints: 4.5 },
  { entryDay: "2026-07-23", entryTime: "13:06", symbol: "MNQ", side: "SHORT", size: 3, entry: 23808.75, points: 29, durationMinutes: 23, mfePoints: 36.5, maePoints: 7.25 },
  { entryDay: "2026-07-24", entryTime: "09:39", symbol: "MNQ", side: "LONG", size: 4, entry: 23736.25, points: 31, durationMinutes: 26, mfePoints: 39.75, maePoints: 8.5 },
  { entryDay: "2026-07-24", entryTime: "13:21", symbol: "MES", side: "LONG", size: 2, entry: 5961.5, points: -7, durationMinutes: 18, mfePoints: 2.75, maePoints: 10.25 },
];

const FOLLOWER_TRADE_SPECS: DemoTradeSpec[] = [
  { entryDay: "2026-07-02", entryTime: "09:37", symbol: "MNQ", side: "LONG", size: 2, entry: 23085.25, points: 16.75, durationMinutes: 16, mfePoints: 23.5, maePoints: 8 },
  { entryDay: "2026-07-02", entryTime: "10:19", symbol: "MNQ", side: "SHORT", size: 1, entry: 23113.75, points: -12, durationMinutes: 9, mfePoints: 4, maePoints: 16 },
  { entryDay: "2026-07-06", entryTime: "09:42", symbol: "MES", side: "LONG", size: 1, entry: 5808.75, points: 7.75, durationMinutes: 27, mfePoints: 11.5, maePoints: 4 },
  { entryDay: "2026-07-07", entryTime: "10:28", symbol: "MNQ", side: "SHORT", size: 1, entry: 23242, points: 21, durationMinutes: 22, mfePoints: 28, maePoints: 8 },
  { entryDay: "2026-07-08", entryTime: "09:35", symbol: "MNQ", side: "LONG", size: 3, entry: 23193.25, points: 24, durationMinutes: 20, mfePoints: 32, maePoints: 9 },
  { entryDay: "2026-07-09", entryTime: "09:48", symbol: "MNQ", side: "SHORT", size: 2, entry: 23317.5, points: 29.25, durationMinutes: 25, mfePoints: 37, maePoints: 10 },
  { entryDay: "2026-07-10", entryTime: "09:52", symbol: "MNQ", side: "LONG", size: 2, entry: 23377.75, points: -14.5, durationMinutes: 11, mfePoints: 3, maePoints: 20 },
  { entryDay: "2026-07-13", entryTime: "09:36", symbol: "MNQ", side: "LONG", size: 2, entry: 23285, points: 36.25, durationMinutes: 32, mfePoints: 44.5, maePoints: 8 },
  { entryDay: "2026-07-14", entryTime: "09:44", symbol: "MES", side: "LONG", size: 1, entry: 5862.75, points: 10.25, durationMinutes: 36, mfePoints: 14, maePoints: 3.75 },
  { entryDay: "2026-07-15", entryTime: "09:40", symbol: "MNQ", side: "SHORT", size: 2, entry: 23421.5, points: -22.5, durationMinutes: 17, mfePoints: 4.5, maePoints: 29.5 },
  { entryDay: "2026-07-16", entryTime: "09:47", symbol: "MNQ", side: "SHORT", size: 2, entry: 23505, points: 25.25, durationMinutes: 21, mfePoints: 34, maePoints: 9 },
  { entryDay: "2026-07-17", entryTime: "09:34", symbol: "MNQ", side: "LONG", size: 3, entry: 23469.5, points: 32.5, durationMinutes: 24, mfePoints: 41, maePoints: 8.5 },
  { entryDay: "2026-07-20", entryTime: "09:43", symbol: "MNQ", side: "SHORT", size: 2, entry: 23611.75, points: 22.25, durationMinutes: 19, mfePoints: 29, maePoints: 7 },
  { entryDay: "2026-07-20", entryTime: "12:37", symbol: "MES", side: "SHORT", size: 1, entry: 5914, points: -10.75, durationMinutes: 33, mfePoints: 3, maePoints: 15 },
  { entryDay: "2026-07-21", entryTime: "09:38", symbol: "MNQ", side: "LONG", size: 2, entry: 23543.5, points: 40.75, durationMinutes: 29, mfePoints: 50, maePoints: 10 },
  { entryDay: "2026-07-23", entryTime: "09:46", symbol: "MES", side: "SHORT", size: 2, entry: 5941.75, points: 11.5, durationMinutes: 34, mfePoints: 16, maePoints: 5 },
  { entryDay: "2026-07-24", entryTime: "09:40", symbol: "MNQ", side: "LONG", size: 2, entry: 23737.5, points: 28.75, durationMinutes: 26, mfePoints: 37.5, maePoints: 9.25 },
  { entryDay: "2026-07-24", entryTime: "13:22", symbol: "MES", side: "LONG", size: 1, entry: 5962, points: -7.75, durationMinutes: 18, mfePoints: 2.5, maePoints: 11 },
];

const SWING_TRADE_SPECS: DemoTradeSpec[] = [
  { entryDay: "2026-07-01", entryTime: "15:20", exitDay: "2026-07-02", exitTime: "10:10", symbol: "MGC", side: "LONG", size: 1, entry: 3348.6, points: 8.6, mfePoints: 13.2, maePoints: 4.1 },
  { entryDay: "2026-07-06", entryTime: "14:45", exitDay: "2026-07-07", exitTime: "11:05", symbol: "MES", side: "LONG", size: 3, entry: 5812.25, points: 19.5, mfePoints: 26.25, maePoints: 6.75 },
  { entryDay: "2026-07-08", entryTime: "13:30", exitDay: "2026-07-09", exitTime: "09:50", symbol: "MGC", side: "SHORT", size: 2, entry: 3372.4, points: -6.4, mfePoints: 3.1, maePoints: 10.8 },
  { entryDay: "2026-07-10", entryTime: "15:10", exitDay: "2026-07-13", exitTime: "10:25", symbol: "MES", side: "LONG", size: 2, entry: 5842.5, points: 27, mfePoints: 34.5, maePoints: 8.25 },
  { entryDay: "2026-07-14", entryTime: "14:38", exitDay: "2026-07-15", exitTime: "10:18", symbol: "MGC", side: "LONG", size: 1, entry: 3391.7, points: 12.8, mfePoints: 18.4, maePoints: 4.6 },
  { entryDay: "2026-07-16", entryTime: "13:55", exitDay: "2026-07-17", exitTime: "09:58", symbol: "MES", side: "SHORT", size: 3, entry: 5891.75, points: 22, mfePoints: 29.75, maePoints: 7.5 },
  { entryDay: "2026-07-20", entryTime: "14:20", exitDay: "2026-07-21", exitTime: "10:12", symbol: "MGC", side: "LONG", size: 2, entry: 3418.3, points: -9.2, mfePoints: 2.6, maePoints: 14.1, ruleBreakType: "held_through_news" },
  { entryDay: "2026-07-20", entryTime: "13:10", exitDay: "2026-07-24", exitTime: "11:16", symbol: "MES", side: "LONG", size: 2, entry: 5928.25, points: 31, mfePoints: 38.5, maePoints: 9.25 },
];

const PRACTICE_TRADE_SPECS: DemoTradeSpec[] = [
  { entryDay: "2026-07-03", entryTime: "10:02", symbol: "MNQ", side: "LONG", size: 1, entry: 23122.5, points: 9.5, durationMinutes: 7, mfePoints: 13, maePoints: 4 },
  { entryDay: "2026-07-06", entryTime: "14:10", symbol: "MGC", side: "SHORT", size: 1, entry: 3362.8, points: -3.7, durationMinutes: 24, mfePoints: 1.2, maePoints: 5.6 },
  { entryDay: "2026-07-08", entryTime: "10:16", symbol: "MES", side: "LONG", size: 1, entry: 5826.25, points: 5.25, durationMinutes: 12, mfePoints: 8, maePoints: 2.25 },
  { entryDay: "2026-07-08", entryTime: "11:32", symbol: "MNQ", side: "SHORT", size: 1, entry: 23244.75, points: -8, durationMinutes: 6, mfePoints: 2, maePoints: 11 },
  { entryDay: "2026-07-10", entryTime: "13:05", symbol: "MNQ", side: "LONG", size: 2, entry: 23316.25, points: 14, durationMinutes: 15, mfePoints: 19, maePoints: 5.25 },
  { entryDay: "2026-07-13", entryTime: "10:44", symbol: "MES", side: "SHORT", size: 1, entry: 5858.75, points: 7, durationMinutes: 18, mfePoints: 10.25, maePoints: 2.75 },
  { entryDay: "2026-07-15", entryTime: "13:11", symbol: "MGC", side: "LONG", size: 1, entry: 3400.2, points: 4.1, durationMinutes: 31, mfePoints: 7.4, maePoints: 2.6 },
  { entryDay: "2026-07-16", entryTime: "10:08", symbol: "MNQ", side: "SHORT", size: 1, entry: 23488.5, points: 12.75, durationMinutes: 9, mfePoints: 16.5, maePoints: 3.5 },
  { entryDay: "2026-07-20", entryTime: "11:25", symbol: "MES", side: "LONG", size: 1, entry: 5902.25, points: -6.25, durationMinutes: 22, mfePoints: 1.75, maePoints: 9 },
  { entryDay: "2026-07-21", entryTime: "14:02", symbol: "MNQ", side: "LONG", size: 2, entry: 23610.25, points: 18.5, durationMinutes: 17, mfePoints: 24, maePoints: 5.5 },
  { entryDay: "2026-07-23", entryTime: "10:31", symbol: "MGC", side: "SHORT", size: 1, entry: 3432.6, points: 5.8, durationMinutes: 28, mfePoints: 8.9, maePoints: 2.1 },
  { entryDay: "2026-07-24", entryTime: "11:47", symbol: "MNQ", side: "LONG", size: 1, entry: 23762.75, points: -10.25, durationMinutes: 8, mfePoints: 2.25, maePoints: 13.5 },
];

function handled<T>(data: T): DemoApiResponse<T> {
  return { handled: true, data };
}

function round(value: number, digits = 2) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  const factor = 10 ** digits;
  return Math.round((value + Number.EPSILON) * factor) / factor;
}

function sum(values: number[]) {
  return values.reduce((total, value) => total + value, 0);
}

function average(values: number[]) {
  return values.length === 0 ? 0 : sum(values) / values.length;
}

function median(values: number[]) {
  if (values.length === 0) {
    return 0;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
}

function readBooleanQuery(query: Record<string, DemoQueryValue> | undefined, key: string) {
  const value = query?.[key];
  return value === true || value === "true" || value === 1 || value === "1";
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

function toEasternUtcIso(day: string, hhmm: string, offsetMinutes = 0) {
  const [hours, minutes] = hhmm.split(":").map((value) => Number.parseInt(value, 10));
  const date = new Date(`${day}T00:00:00.000Z`);
  date.setUTCHours(hours + 4, minutes + offsetMinutes, 0, 0);
  return date.toISOString();
}

function pointValue(symbol: DemoSymbol) {
  return POINT_VALUES[symbol];
}

function contractId(symbol: DemoSymbol) {
  return `CON.F.US.${symbol}.${symbol === "MGC" ? "Q26" : "U26"}`;
}

function tradeNetPnl(trade: AccountTrade) {
  return round((trade.pnl ?? 0) - Math.abs(trade.fees));
}

const RULE_BREAK_BY_TRADE_ID = new Map<number, string>();

function buildTrade(accountId: number, spec: DemoTradeSpec, index: number): AccountTrade {
  const entryTime = toEasternUtcIso(spec.entryDay, spec.entryTime);
  const exitTime = spec.exitDay && spec.exitTime
    ? toEasternUtcIso(spec.exitDay, spec.exitTime)
    : toEasternUtcIso(spec.entryDay, spec.entryTime, spec.durationMinutes ?? 0);
  const durationMinutes = Math.round((Date.parse(exitTime) - Date.parse(entryTime)) / 60_000);
  const exitPrice = spec.side === "LONG" ? spec.entry + spec.points : spec.entry - spec.points;
  const feeRate = FEE_RATES[spec.symbol];
  const nonCommissionFees = round(spec.size * feeRate.nonCommission);
  const commissions = round(spec.size * feeRate.commission);
  const id = accountId * 1_000 + index + 1;
  if (spec.ruleBreakType) {
    RULE_BREAK_BY_TRADE_ID.set(id, spec.ruleBreakType);
  }

  return {
    id,
    account_id: accountId,
    contract_id: contractId(spec.symbol),
    symbol: spec.symbol,
    side: spec.side,
    size: spec.size,
    price: round(exitPrice, spec.symbol === "MGC" ? 1 : 2),
    timestamp: exitTime,
    entry_time: entryTime,
    exit_time: exitTime,
    duration_minutes: durationMinutes,
    entry_price: spec.entry,
    exit_price: round(exitPrice, spec.symbol === "MGC" ? 1 : 2),
    fees: round(nonCommissionFees + commissions),
    non_commission_fees: nonCommissionFees,
    commissions,
    // AccountTrade.pnl follows the production API: realized/gross P&L before fees.
    pnl: round(spec.points * pointValue(spec.symbol) * spec.size),
    // ProjectX lifecycle excursions are dollar amounts, with MAE represented as negative.
    mfe: round(Math.abs(spec.mfePoints) * pointValue(spec.symbol) * spec.size),
    mae: round(-Math.abs(spec.maePoints) * pointValue(spec.symbol) * spec.size),
    order_id: `DEMO-${accountId}-${String(index + 1).padStart(3, "0")}`,
    source_trade_id: `DEMO-${DEMO_SCENARIO_VERSION}-${accountId}-${String(index + 1).padStart(3, "0")}`,
  };
}

function buildTrades(accountId: number, specs: DemoTradeSpec[]) {
  return specs.map((spec, index) => buildTrade(accountId, spec, index));
}

const DEMO_TRADES_BY_ACCOUNT_ID = new Map<number, AccountTrade[]>([
  [PRIMARY_ACCOUNT_ID, buildTrades(PRIMARY_ACCOUNT_ID, PRIMARY_TRADE_SPECS)],
  [FOLLOWER_ACCOUNT_ID, buildTrades(FOLLOWER_ACCOUNT_ID, FOLLOWER_TRADE_SPECS)],
  [SWING_ACCOUNT_ID, buildTrades(SWING_ACCOUNT_ID, SWING_TRADE_SPECS)],
  [PRACTICE_ACCOUNT_ID, buildTrades(PRACTICE_ACCOUNT_ID, PRACTICE_TRADE_SPECS)],
  [ARCHIVED_ACCOUNT_ID, []],
  [MISSING_ACCOUNT_ID, []],
]);

function getAccountTrades(accountId: number) {
  return DEMO_TRADES_BY_ACCOUNT_ID.get(accountId) ?? [];
}

function getLastTradeAt(accountId: number) {
  const timestamps = getAccountTrades(accountId).map((trade) => trade.exit_time ?? trade.timestamp);
  return timestamps.length === 0 ? null : timestamps.sort().at(-1) ?? null;
}

function currentBalance(openingBalance: number, accountId: number) {
  return round(openingBalance + sum(getAccountTrades(accountId).map(tradeNetPnl)));
}

const DEMO_ACCOUNTS: AccountInfo[] = [
  {
    id: PRIMARY_ACCOUNT_ID,
    name: "Demo · 50K Main",
    provider_name: "50KTC-DEMO-Main",
    custom_display_name: "Demo Main — Intraday",
    trade_data_source: "projectx",
    balance: currentBalance(50_000, PRIMARY_ACCOUNT_ID),
    provider_data_stale: false,
    provider_sync_status: "cache_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: getLastTradeAt(PRIMARY_ACCOUNT_ID),
    last_seen_at: getLastTradeAt(PRIMARY_ACCOUNT_ID),
    status: "active",
    account_state: "ACTIVE",
    is_main: true,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: getLastTradeAt(PRIMARY_ACCOUNT_ID),
  },
  {
    id: FOLLOWER_ACCOUNT_ID,
    name: "Demo · Copy Follower",
    provider_name: "50KTC-DEMO-Copy",
    custom_display_name: "Demo Copy — Conservative",
    trade_data_source: "projectx",
    balance: currentBalance(50_000, FOLLOWER_ACCOUNT_ID),
    provider_data_stale: false,
    provider_sync_status: "cache_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: getLastTradeAt(FOLLOWER_ACCOUNT_ID),
    last_seen_at: getLastTradeAt(FOLLOWER_ACCOUNT_ID),
    status: "active",
    account_state: "ACTIVE",
    is_main: false,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: getLastTradeAt(FOLLOWER_ACCOUNT_ID),
  },
  {
    id: SWING_ACCOUNT_ID,
    name: "Demo · 100K Swing",
    provider_name: "100K-DEMO-Swing",
    custom_display_name: "Demo Swing — Overnight",
    trade_data_source: "projectx",
    balance: currentBalance(100_000, SWING_ACCOUNT_ID),
    provider_data_stale: false,
    provider_sync_status: "cache_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: getLastTradeAt(SWING_ACCOUNT_ID),
    last_seen_at: getLastTradeAt(SWING_ACCOUNT_ID),
    status: "locked_out",
    account_state: "LOCKED_OUT",
    is_main: false,
    is_archived: false,
    can_trade: false,
    is_visible: true,
    last_trade_at: getLastTradeAt(SWING_ACCOUNT_ID),
  },
  {
    id: PRACTICE_ACCOUNT_ID,
    name: "Demo · Practice Lab",
    provider_name: "PRACTICE-DEMO-Lab",
    custom_display_name: "Demo Practice — Strategy Lab",
    trade_data_source: "projectx",
    balance: currentBalance(50_000, PRACTICE_ACCOUNT_ID),
    provider_data_stale: false,
    provider_sync_status: "cache_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: getLastTradeAt(PRACTICE_ACCOUNT_ID),
    last_seen_at: getLastTradeAt(PRACTICE_ACCOUNT_ID),
    status: "active",
    account_state: "ACTIVE",
    is_main: false,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: getLastTradeAt(PRACTICE_ACCOUNT_ID),
  },
  {
    id: ARCHIVED_ACCOUNT_ID,
    name: "Demo · Archived Import",
    provider_name: "CSV-DEMO-Archive",
    custom_display_name: "Demo Archive — Empty Import",
    trade_data_source: "csv_import",
    balance: 50_000,
    provider_data_stale: false,
    provider_sync_status: "not_applicable",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: null,
    last_seen_at: "2026-06-30T20:00:00.000Z",
    status: "hidden",
    account_state: "HIDDEN",
    is_main: false,
    is_archived: true,
    can_trade: false,
    is_visible: false,
    last_trade_at: null,
  },
  {
    id: MISSING_ACCOUNT_ID,
    name: "Demo · Disconnected Account",
    provider_name: "50KTC-DEMO-Missing",
    custom_display_name: "Demo Missing — Provider Removed",
    trade_data_source: "projectx",
    balance: null,
    provider_data_stale: true,
    provider_sync_status: "cache_stale",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: "2026-07-20T13:30:00.000Z",
    last_seen_at: "2026-07-20T13:30:00.000Z",
    status: "missing",
    account_state: "MISSING",
    is_main: false,
    is_archived: false,
    can_trade: false,
    is_visible: false,
    last_trade_at: null,
  },
];

const DEMO_EXPENSES: ExpenseRecord[] = [
  {
    id: 7101,
    account_id: PRIMARY_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-06-29",
    amount_cents: 4_900,
    amount: 49,
    currency: "USD",
    category: "evaluation_fee",
    account_type: "standard",
    plan_size: "50k",
    description: "Demo main-account evaluation renewal",
    tags: ["demo", "main"],
    created_at: "2026-06-29T13:00:00.000Z",
    updated_at: "2026-06-29T13:00:00.000Z",
  },
  {
    id: 7102,
    account_id: null,
    provider: "CME",
    expense_date: "2026-07-01",
    amount_cents: 3_900,
    amount: 39,
    currency: "USD",
    category: "data_fee",
    account_type: null,
    plan_size: null,
    description: "Demo July market-data subscription",
    tags: ["demo", "data"],
    created_at: "2026-07-01T12:00:00.000Z",
    updated_at: "2026-07-01T12:00:00.000Z",
  },
  {
    id: 7103,
    account_id: FOLLOWER_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-07-06",
    amount_cents: 4_900,
    amount: 49,
    currency: "USD",
    category: "evaluation_fee",
    account_type: "standard",
    plan_size: "50k",
    description: "Demo copy-account evaluation renewal",
    tags: ["demo", "copy"],
    created_at: "2026-07-06T12:00:00.000Z",
    updated_at: "2026-07-06T12:00:00.000Z",
  },
  {
    id: 7104,
    account_id: PRIMARY_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-07-13",
    amount_cents: 14_900,
    amount: 149,
    currency: "USD",
    category: "activation_fee",
    account_type: "standard",
    plan_size: "50k",
    description: "Demo funded-account activation",
    tags: ["demo", "activation"],
    created_at: "2026-07-13T12:00:00.000Z",
    updated_at: "2026-07-13T12:00:00.000Z",
  },
  {
    id: 7105,
    account_id: SWING_ACCOUNT_ID,
    provider: "Topstep",
    expense_date: "2026-07-15",
    amount_cents: 9_900,
    amount: 99,
    currency: "USD",
    category: "other",
    account_type: "standard",
    plan_size: "100k",
    description: "Demo swing-account risk-review session",
    tags: ["demo", "swing", "risk-review"],
    created_at: "2026-07-15T12:00:00.000Z",
    updated_at: "2026-07-15T12:00:00.000Z",
  },
  {
    id: 7106,
    account_id: FOLLOWER_ACCOUNT_ID,
    provider: "Trade Copier Pro",
    expense_date: "2026-07-22",
    amount_cents: 2_500,
    amount: 25,
    currency: "USD",
    category: "other",
    account_type: "standard",
    plan_size: "50k",
    description: "Demo copy-trading software subscription",
    tags: ["demo", "copy", "software"],
    created_at: "2026-07-22T12:00:00.000Z",
    updated_at: "2026-07-22T12:00:00.000Z",
  },
];

const DEMO_PAYOUTS: PayoutRecord[] = [
  {
    id: 7201,
    payout_date: "2026-06-26",
    amount_cents: 87_500,
    amount: 875,
    currency: "USD",
    notes: "Demo funded-account payout (not deducted from evaluation balances)",
    created_at: "2026-06-26T16:00:00.000Z",
    updated_at: "2026-06-26T16:00:00.000Z",
  },
  {
    id: 7202,
    payout_date: "2026-07-17",
    amount_cents: 125_000,
    amount: 1_250,
    currency: "USD",
    notes: "Demo funded-account payout after the second qualifying cycle",
    created_at: "2026-07-17T16:00:00.000Z",
    updated_at: "2026-07-17T16:00:00.000Z",
  },
];

function getRequestedAccountId(path: string) {
  const match = /^\/api\/accounts\/(\d+)(?:\/|$)/.exec(path);
  return match ? Number.parseInt(match[1], 10) : null;
}

function getMetricsAccountId(query: Record<string, DemoQueryValue> | undefined) {
  return readNumberQuery(query, "account_id") ?? PRIMARY_ACCOUNT_ID;
}

function filterByDateRange<
  T extends {
    timestamp?: string;
    entry_time?: string | null;
    entry_date?: string;
    date?: string;
    expense_date?: string;
    payout_date?: string;
  },
>(rows: T[], query: Record<string, DemoQueryValue> | undefined) {
  const start = readStringQuery(query, "start") || readStringQuery(query, "start_date");
  const end = readStringQuery(query, "end") || readStringQuery(query, "end_date");
  const startMs = start ? Date.parse(start.includes("T") ? start : `${start}T00:00:00.000Z`) : null;
  const endMs = end ? Date.parse(end.includes("T") ? end : `${end}T23:59:59.999Z`) : null;

  return rows.filter((row) => {
    const value = row.timestamp ?? row.entry_time ?? row.entry_date ?? row.date ?? row.expense_date ?? row.payout_date;
    if (!value) {
      return true;
    }
    const rowMs = Date.parse(value.includes("T") ? value : `${value}T12:00:00.000Z`);
    return (startMs === null || rowMs >= startMs) && (endMs === null || rowMs <= endMs);
  });
}

function limitAndOffset<T>(rows: T[], query: Record<string, DemoQueryValue> | undefined, defaultLimit: number) {
  const limit = Math.max(0, Math.floor(readNumberQuery(query, "limit") ?? defaultLimit));
  const offset = Math.max(0, Math.floor(readNumberQuery(query, "offset") ?? 0));
  return rows.slice(offset, offset + limit);
}

function filterTrades(accountId: number, query: Record<string, DemoQueryValue> | undefined) {
  const symbol = readStringQuery(query, "symbol").trim().toUpperCase();
  let rows = filterByDateRange(getAccountTrades(accountId), query);
  if (symbol) {
    rows = rows.filter((trade) => trade.symbol.includes(symbol) || trade.contract_id.includes(symbol));
  }
  rows = [...rows].sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp));
  return limitAndOffset(rows, query, 200);
}

function getTradeDay(trade: AccountTrade) {
  return (trade.exit_time ?? trade.timestamp).slice(0, 10);
}

function buildCalendarDays(trades: AccountTrade[]): AccountPnlCalendarDay[] {
  const byDate = new Map<string, AccountPnlCalendarDay>();
  for (const trade of [...trades].sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))) {
    const date = getTradeDay(trade);
    const current = byDate.get(date) ?? {
      date,
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
    const gross = trade.pnl ?? 0;
    const nonCommissionFees = Math.abs(trade.non_commission_fees ?? trade.fees);
    const commissions = Math.abs(trade.commissions ?? 0);
    const fees = Math.abs(trade.fees);
    const net = gross - fees;
    current.trade_count += 1;
    if (net > 0) {
      current.win_count = (current.win_count ?? 0) + 1;
    } else if (net < 0) {
      current.loss_count = (current.loss_count ?? 0) + 1;
    } else {
      current.breakeven_count = (current.breakeven_count ?? 0) + 1;
    }
    current.gross_pnl = round(current.gross_pnl + gross);
    current.non_commission_fees = round((current.non_commission_fees ?? 0) + nonCommissionFees);
    current.commissions = round((current.commissions ?? 0) + commissions);
    current.fees = round(current.fees + fees);
    current.net_pnl = round(current.net_pnl + net);
    byDate.set(date, current);
  }
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function buildDrawdownEpisodes(trades: AccountTrade[]) {
  const sorted = [...trades].sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  let equity = 0;
  let peak = 0;
  let inDrawdown = false;
  let currentPeak = 0;
  let currentStartMs = 0;
  let currentTroughMs = 0;
  let currentTrough = 0;
  const episodes: DrawdownEpisode[] = [];

  for (const trade of sorted) {
    const timestampMs = Date.parse(trade.timestamp);
    equity += tradeNetPnl(trade);
    if (equity >= peak) {
      if (inDrawdown) {
        episodes.push({
          peakEquity: currentPeak,
          startMs: currentStartMs,
          troughMs: currentTroughMs,
          endMs: timestampMs,
          troughDrawdown: currentTrough,
        });
      }
      peak = equity;
      inDrawdown = false;
      continue;
    }

    const drawdown = equity - peak;
    if (!inDrawdown) {
      inDrawdown = true;
      currentPeak = peak;
      currentStartMs = timestampMs;
      currentTroughMs = timestampMs;
      currentTrough = drawdown;
    } else if (drawdown < currentTrough) {
      currentTrough = drawdown;
      currentTroughMs = timestampMs;
    }
  }

  if (inDrawdown) {
    episodes.push({
      peakEquity: currentPeak,
      startMs: currentStartMs,
      troughMs: currentTroughMs,
      endMs: null,
      troughDrawdown: currentTrough,
    });
  }
  return episodes;
}

function buildDrawdownStats(trades: AccountTrade[]) {
  const episodes = buildDrawdownEpisodes(trades);
  if (episodes.length === 0) {
    return {
      max_drawdown: 0,
      average_drawdown: 0,
      risk_drawdown_score: 0,
      max_drawdown_length_hours: 0,
      recovery_time_hours: 0,
      average_recovery_length_hours: 0,
    };
  }
  const lastMs = Math.max(...trades.map((trade) => Date.parse(trade.timestamp)));
  const maxEpisode = episodes.reduce((worst, episode) =>
    episode.troughDrawdown < worst.troughDrawdown ? episode : worst,
  );
  const hours = (startMs: number, endMs: number) => Math.max(0, (endMs - startMs) / 3_600_000);
  const drawdownLengths = episodes.map((episode) => hours(episode.startMs, episode.endMs ?? lastMs));
  const recoveryLengths = episodes
    .filter((episode): episode is DrawdownEpisode & { endMs: number } => episode.endMs !== null)
    .map((episode) => hours(episode.troughMs, episode.endMs));
  const denominator = Math.max(maxEpisode.peakEquity, Math.abs(maxEpisode.troughDrawdown), 1);
  return {
    max_drawdown: round(maxEpisode.troughDrawdown),
    average_drawdown: round(average(episodes.map((episode) => episode.troughDrawdown))),
    risk_drawdown_score: round((Math.abs(maxEpisode.troughDrawdown) / denominator) * 100),
    max_drawdown_length_hours: round(Math.max(...drawdownLengths)),
    recovery_time_hours: round(hours(maxEpisode.troughMs, maxEpisode.endMs ?? lastMs)),
    average_recovery_length_hours: round(average(recoveryLengths)),
  };
}

function computeActiveHours(trades: AccountTrade[]) {
  const byDay = new Map<string, { first: number; last: number }>();
  for (const trade of trades) {
    const timestampMs = Date.parse(trade.timestamp);
    const day = getTradeDay(trade);
    const current = byDay.get(day);
    if (!current) {
      byDay.set(day, { first: timestampMs, last: timestampMs });
    } else {
      current.first = Math.min(current.first, timestampMs);
      current.last = Math.max(current.last, timestampMs);
    }
  }
  return sum([...byDay.values()].map(({ first, last }) => Math.max((last - first) / 3_600_000, 1 / 60)));
}

function pointPayoff(trades: AccountTrade[], basis: PointsBasis | "auto") {
  const wins: number[] = [];
  const losses: number[] = [];
  for (const trade of trades) {
    const symbol = trade.symbol as PointsBasis;
    if (basis !== "auto" && symbol !== basis) {
      continue;
    }
    const value = basis === "auto" ? POINT_VALUES[symbol] : POINT_VALUES[basis];
    if (!value || trade.size <= 0) {
      continue;
    }
    const equivalentPoints = tradeNetPnl(trade) / (Math.abs(trade.size) * value);
    if (equivalentPoints > 0) {
      wins.push(equivalentPoints);
    } else if (equivalentPoints < 0) {
      losses.push(Math.abs(equivalentPoints));
    }
  }
  return {
    avgPointGain: wins.length === 0 ? null : round(average(wins), 4),
    avgPointLoss: losses.length === 0 ? null : round(average(losses), 4),
  };
}

function classifySizingBenchmark(actualNetPnl: number, benchmarkNetPnl: number, tradeCount: number): {
  label: SizingBenchmarkLabel;
  ratio: number | null;
} {
  const nearZeroThreshold = Math.max(25, Math.max(tradeCount, 1) * 5);
  if (benchmarkNetPnl > nearZeroThreshold) {
    const ratio = actualNetPnl / benchmarkNetPnl;
    if (ratio < 0.5) return { label: "Far Below Benchmark", ratio };
    if (ratio < 0.9) return { label: "Below Benchmark", ratio };
    if (ratio <= 1.1) return { label: "In Line With Benchmark", ratio };
    if (ratio <= 1.5) return { label: "Above Benchmark", ratio };
    return { label: "Far Above Benchmark", ratio };
  }
  const difference = actualNetPnl - benchmarkNetPnl;
  const inlineThreshold = Math.max(50, Math.max(tradeCount, 1) * 10);
  const farThreshold = Math.max(150, Math.max(tradeCount, 1) * 25);
  if (difference < -farThreshold) return { label: "Far Below Benchmark", ratio: null };
  if (difference < -inlineThreshold) return { label: "Below Benchmark", ratio: null };
  if (difference <= inlineThreshold) return { label: "In Line With Benchmark", ratio: null };
  if (difference <= farThreshold) return { label: "Above Benchmark", ratio: null };
  return { label: "Far Above Benchmark", ratio: null };
}

function buildSizingBenchmark(trades: AccountTrade[], actualNetPnl: number): AccountSizingBenchmark {
  const sizes = trades.map((trade) => Math.abs(trade.size)).filter((size) => size > 0);
  const benchmarkSize = average(sizes);
  let benchmarkGrossPnl = 0;
  let benchmarkFees = 0;
  for (const trade of trades) {
    const quantity = Math.abs(trade.size);
    if (quantity <= 0) continue;
    const sizeRatio = benchmarkSize / quantity;
    benchmarkGrossPnl += (trade.pnl ?? 0) * sizeRatio;
    benchmarkFees += Math.abs(trade.fees) * sizeRatio;
  }
  const benchmarkNetPnl = benchmarkGrossPnl - benchmarkFees;
  const benchmarkDiff = actualNetPnl - benchmarkNetPnl;
  const classification = classifySizingBenchmark(actualNetPnl, benchmarkNetPnl, trades.length);
  return {
    benchmarkMode: "fixed_average_size",
    benchmarkSizeUsed: round(benchmarkSize, 4),
    benchmarkGrossPnl: round(benchmarkGrossPnl),
    benchmarkNetPnl: round(benchmarkNetPnl),
    benchmarkDiff: round(benchmarkDiff),
    benchmarkRatio: classification.ratio === null ? null : round(classification.ratio, 4),
    benchmarkLabel: classification.label,
  };
}

function normalizePointsBasis(value: string): PointsBasis | "auto" {
  const normalized = value.toUpperCase();
  return normalized === "AUTO" ? "auto" : normalized in POINT_VALUES ? normalized as PointsBasis : "auto";
}

function buildSummary(accountId: number, query: Record<string, DemoQueryValue> | undefined): AccountSummary {
  const trades = filterTrades(accountId, { ...query, limit: 10_000, offset: 0 });
  const chronological = [...trades].sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  const calendarDays = buildCalendarDays(chronological);
  const grossValues = chronological.map((trade) => trade.pnl ?? 0);
  const netValues = chronological.map(tradeNetPnl);
  const wins = netValues.filter((value) => value > 0);
  const losses = netValues.filter((value) => value < 0);
  const grossWins = grossValues.filter((value) => value > 0);
  const grossLosses = grossValues.filter((value) => value < 0);
  const grossPnl = sum(grossValues);
  const fees = sum(chronological.map((trade) => Math.abs(trade.fees)));
  const netPnl = sum(netValues);
  const activeDays = calendarDays.length;
  const greenDays = calendarDays.filter((day) => day.net_pnl > 0).length;
  const redDays = calendarDays.filter((day) => day.net_pnl < 0).length;
  const flatDays = activeDays - greenDays - redDays;
  const sizes = chronological.map((trade) => Math.abs(trade.size)).filter((size) => size > 0);
  const winDurations = chronological
    .filter((trade) => tradeNetPnl(trade) > 0)
    .map((trade) => trade.duration_minutes ?? 0);
  const lossDurations = chronological
    .filter((trade) => tradeNetPnl(trade) < 0)
    .map((trade) => trade.duration_minutes ?? 0);
  const sortedTail = [...netValues].sort((left, right) => left - right);
  const worstCount = Math.max(1, Math.ceil(sortedTail.length * 0.05));
  const tailRisk = sortedTail.length === 0 ? 0 : Math.min(0, average(sortedTail.slice(0, worstCount)));
  const pointsBasis = normalizePointsBasis(readStringQuery(query, "points_basis") || "auto");
  const payoff = pointPayoff(chronological, pointsBasis);
  const drawdown = buildDrawdownStats(chronological);
  const activeHours = computeActiveHours(chronological);
  const tradeCount = chronological.length;

  return {
    realized_pnl: round(grossPnl),
    gross_pnl: round(grossPnl),
    fees: round(fees),
    net_pnl: round(netPnl),
    win_rate: tradeCount === 0 ? 0 : round((wins.length / tradeCount) * 100),
    win_count: wins.length,
    loss_count: losses.length,
    breakeven_count: tradeCount - wins.length - losses.length,
    profit_factor: grossLosses.length === 0 ? 0 : round(sum(grossWins) / Math.abs(sum(grossLosses)), 4),
    avg_win: round(average(wins)),
    avg_loss: round(average(losses)),
    avg_win_duration_minutes: round(average(winDurations)),
    avg_loss_duration_minutes: round(average(lossDurations)),
    expectancy_per_trade: round(average(netValues)),
    tail_risk_5pct: round(tailRisk),
    ...drawdown,
    trade_count: tradeCount,
    half_turn_count: new Set(chronological.map((trade) => trade.order_id).filter(Boolean)).size || tradeCount,
    execution_count: tradeCount,
    day_win_rate: activeDays === 0 ? 0 : round((greenDays / activeDays) * 100),
    green_days: greenDays,
    red_days: redDays,
    flat_days: flatDays,
    avg_trades_per_day: activeDays === 0 ? 0 : round(tradeCount / activeDays),
    active_days: activeDays,
    efficiency_per_hour: activeHours <= 0 ? 0 : round(netPnl / activeHours),
    profit_per_day: activeDays === 0 ? 0 : round(netPnl / activeDays),
    averagePositionSize: round(average(sizes), 4),
    medianPositionSize: round(median(sizes), 4),
    tradeCountUsedForSizingStats: sizes.length,
    avgPointGain: payoff.avgPointGain,
    avgPointLoss: payoff.avgPointLoss,
    pointsBasisUsed: pointsBasis,
    sizingBenchmark: buildSizingBenchmark(chronological, netPnl),
  };
}

function buildSummaryWithPointBases(
  accountId: number,
  query: Record<string, DemoQueryValue> | undefined,
): AccountSummaryWithPointBases {
  const trades = filterTrades(accountId, { ...query, limit: 10_000, offset: 0 });
  return {
    summary: buildSummary(accountId, query),
    point_payoff_by_basis: {
      MNQ: pointPayoff(trades, "MNQ"),
      MES: pointPayoff(trades, "MES"),
      NQ: pointPayoff(trades, "NQ"),
      ES: pointPayoff(trades, "ES"),
      MGC: pointPayoff(trades, "MGC"),
      SIL: pointPayoff(trades, "SIL"),
    },
  };
}

const JOURNAL_NOTES_BY_ACCOUNT_ID = new Map<number, JournalNote[]>([
  [
    PRIMARY_ACCOUNT_ID,
    [
      {
        day: "2026-07-10",
        title: "Stopped trading after the second mistake",
        mood: "Frustrated",
        tags: ["rule-break", "risk", "reset"],
        body: "I moved the first stop, then took the next setup too quickly. I shut the platform down after trade two instead of trying to earn it back. Tomorrow's rule is one full five-minute pause after every loss.",
      },
      {
        day: "2026-07-17",
        title: "Opening pullback, planned size",
        mood: "Confident",
        tags: ["A-setup", "patience", "MNQ"],
        body: "The opening push held above the premarket level, so I waited for the first orderly pullback. Size and stop matched the plan, and I did not add after the move accelerated.",
      },
      {
        day: "2026-07-24",
        title: "Strong morning, protected the week",
        mood: "Focused",
        tags: ["weekly-review", "discipline", "mixed-session"],
        body: "The MNQ morning trade did the work. The later MES attempt failed cleanly, and I accepted the planned loss without a third trade. Good finish to a week built on fewer, clearer setups.",
      },
    ],
  ],
  [
    FOLLOWER_ACCOUNT_ID,
    [
      {
        day: "2026-07-09",
        title: "Follower slippage stayed inside tolerance",
        mood: "Neutral",
        tags: ["copy-trade", "slippage", "review"],
        body: "The follower entered one point behind the main account but exited within the expected band. Reduced size kept the dollar result proportional without pretending every fill was identical.",
      },
      {
        day: "2026-07-20",
        title: "Did not override the copier after the loss",
        mood: "Focused",
        tags: ["copy-trade", "discipline", "MES"],
        body: "The morning MNQ copy worked and the midday MES copy did not. I left allocation unchanged and avoided a manual make-up trade, which is the behavior this account is meant to test.",
      },
      {
        day: "2026-07-24",
        title: "Conservative allocation behaved as designed",
        mood: "Confident",
        tags: ["copy-trade", "allocation", "weekly-review"],
        body: "Fills were slightly worse than the main account, but the smaller allocation kept risk controlled. The account closed the week with the same directional story and appropriately smaller P&L.",
      },
    ],
  ],
  [
    SWING_ACCOUNT_ID,
    [
      {
        day: "2026-07-13",
        title: "Held the thesis, not the noise",
        mood: "Confident",
        tags: ["swing", "MES", "overnight"],
        body: "The multi-session MES position stayed above invalidation through the weekend reopen. I exited into the first clean extension instead of converting the trade into an indefinite hold.",
      },
      {
        day: "2026-07-21",
        title: "News hold was outside the playbook",
        mood: "Frustrated",
        tags: ["rule-break", "MGC", "news"],
        body: "I kept the MGC position through a scheduled release even though the plan called for flattening. The loss was manageable, but the process was not. New entries are locked; the MES position opened before the review remains under its original stop and target.",
      },
      {
        day: "2026-07-24",
        title: "Patient exit after two-session hold",
        mood: "Focused",
        tags: ["swing", "MES", "recovery"],
        body: "The MES long recovered without requiring extra size. I used the planned target and documented the trade, but the account stays locked until the earlier news-rule review is complete.",
      },
    ],
  ],
  [
    PRACTICE_ACCOUNT_ID,
    [
      {
        day: "2026-07-08",
        title: "Tested two instruments at minimum size",
        mood: "Neutral",
        tags: ["practice", "MES", "MNQ"],
        body: "The MES setup worked and the MNQ follow-up did not. Keeping both at minimum size made the comparison useful without turning an experiment into a performance target.",
      },
      {
        day: "2026-07-16",
        title: "Cleaner short trigger in the lab",
        mood: "Focused",
        tags: ["practice", "trigger", "MNQ"],
        body: "I waited for the lower high instead of anticipating it. The short was small, quick, and consistent with the exact trigger I am testing before it reaches a live allocation.",
      },
      {
        day: "2026-07-24",
        title: "A useful failed experiment",
        mood: "Neutral",
        tags: ["practice", "review", "failed-setup"],
        body: "The late-morning continuation failed. The entry met the experimental rule, so the planned loss is useful data; no changes move to the main account until the sample is larger.",
      },
    ],
  ],
]);

function buildJournalEntries(accountId: number): JournalEntry[] {
  const notes = JOURNAL_NOTES_BY_ACCOUNT_ID.get(accountId) ?? [];
  return notes.map((note, index) => {
    const trades = getAccountTrades(accountId).filter((trade) => getTradeDay(trade) === note.day);
    const grossValues = trades.map((trade) => trade.pnl ?? 0);
    const netValues = trades.map(tradeNetPnl);
    const wins = netValues.filter((value) => value > 0);
    const losses = netValues.filter((value) => value < 0);
    const fees = sum(trades.map((trade) => Math.abs(trade.fees)));
    const gross = sum(grossValues);
    const net = sum(netValues);
    const snapshotAt = `${note.day}T19:30:00.000Z`;
    return {
      id: accountId * 10 + index + 1,
      account_id: accountId,
      entry_date: note.day,
      title: note.title,
      mood: note.mood,
      tags: note.tags,
      body: note.body,
      version: 1,
      stats_source: `demo:${DEMO_SCENARIO_VERSION}`,
      stats_json: {
        snapshot_version: 1,
        trade_count: trades.length,
        total_pnl: round(gross),
        total_fees: round(fees),
        win_rate: trades.length === 0 ? 0 : round((wins.length / trades.length) * 100),
        avg_win: round(average(wins)),
        avg_loss: round(average(losses)),
        largest_win: wins.length === 0 ? 0 : round(Math.max(...wins)),
        largest_loss: losses.length === 0 ? 0 : round(Math.min(...losses)),
        largest_position_size: trades.length === 0 ? 0 : Math.max(...trades.map((trade) => trade.size)),
        gross: round(gross),
        net: round(net),
        net_realized_pnl: round(net),
      },
      stats_pulled_at: snapshotAt,
      is_archived: false,
      created_at: snapshotAt,
      updated_at: snapshotAt,
    };
  });
}

const DEMO_JOURNALS_BY_ACCOUNT_ID = new Map<number, JournalEntry[]>(
  DEMO_ACCOUNTS.map((account) => [account.id, buildJournalEntries(account.id)]),
);

function filterJournalEntries(
  accountId: number,
  query: Record<string, DemoQueryValue> | undefined,
): JournalEntriesResponse {
  let entries = filterByDateRange(DEMO_JOURNALS_BY_ACCOUNT_ID.get(accountId) ?? [], query);
  const mood = readStringQuery(query, "mood");
  const search = readStringQuery(query, "q").trim().toLowerCase();
  const includeArchived = readBooleanQuery(query, "include_archived");
  if (!includeArchived) entries = entries.filter((entry) => !entry.is_archived);
  if (mood) entries = entries.filter((entry) => entry.mood === mood);
  if (search) {
    entries = entries.filter((entry) =>
      `${entry.title} ${entry.body} ${entry.tags.join(" ")}`.toLowerCase().includes(search),
    );
  }
  entries = [...entries].sort((left, right) => right.entry_date.localeCompare(left.entry_date));
  return { items: limitAndOffset(entries, query, 20), total: entries.length };
}

function isoDateFromMs(milliseconds: number) {
  return new Date(milliseconds).toISOString().slice(0, 10);
}

function subtractMonths(day: string, months: number) {
  const [year, month, date] = day.split("-").map(Number);
  const monthIndex = year * 12 + month - 1 - months;
  const targetYear = Math.floor(monthIndex / 12);
  const targetMonthIndex = monthIndex % 12;
  const lastDay = new Date(Date.UTC(targetYear, targetMonthIndex + 1, 0)).getUTCDate();
  return isoDateFromMs(Date.UTC(targetYear, targetMonthIndex, Math.min(date, lastDay)));
}

function addYears(day: string, years: number) {
  const [year, month, date] = day.split("-").map(Number);
  const lastDay = new Date(Date.UTC(year + years, month, 0)).getUTCDate();
  return isoDateFromMs(Date.UTC(year + years, month - 1, Math.min(date, lastDay)));
}

function previousDay(day: string) {
  return isoDateFromMs(Date.parse(`${day}T12:00:00.000Z`) - 86_400_000);
}

function rangeStart(range: ExpenseRange, endDate = DEMO_AS_OF_DATE) {
  if (range === "month") return `${endDate.slice(0, 7)}-01`;
  if (range === "ytd") return `${endDate.slice(0, 4)}-01-01`;
  if (range === "week") {
    const date = new Date(`${endDate}T12:00:00.000Z`);
    const weekday = date.getUTCDay();
    const daysSinceMonday = weekday === 0 ? 6 : weekday - 1;
    return isoDateFromMs(date.getTime() - daysSinceMonday * 86_400_000);
  }
  return null;
}

function rowsInDateRange<T extends { expense_date?: string; payout_date?: string }>(
  rows: T[],
  startDate: string | null,
  endDate: string | null,
) {
  return rows.filter((row) => {
    const day = row.expense_date ?? row.payout_date ?? "";
    return (!startDate || day >= startDate) && (!endDate || day <= endDate);
  });
}

function buildExpenseTotals(
  rows: ExpenseRecord[],
  range: ExpenseRange,
  startDate: string | null,
  endDate: string,
): ExpenseTotals {
  const byCategory: ExpenseTotals["by_category"] = {};
  for (const row of rows) {
    const current = byCategory[row.category] ?? { amount: 0, amount_cents: 0, count: 0 };
    current.amount_cents += row.amount_cents;
    current.amount = round(current.amount_cents / 100);
    current.count += 1;
    byCategory[row.category] = current;
  }
  const totalCents = sum(rows.map((row) => row.amount_cents));
  return {
    range,
    start_date: startDate,
    end_date: endDate,
    total_amount: round(totalCents / 100),
    total_amount_cents: totalCents,
    by_category: byCategory,
    count: rows.length,
  };
}

function buildPayoutTotals(rows: PayoutRecord[]): PayoutTotals {
  const totalCents = sum(rows.map((row) => row.amount_cents));
  const averageCents = rows.length === 0 ? 0 : Math.round(totalCents / rows.length);
  return {
    total_amount: round(totalCents / 100),
    total_amount_cents: totalCents,
    average_amount: round(averageCents / 100),
    average_amount_cents: averageCents,
    count: rows.length,
  };
}

function filterExpenses(query: Record<string, DemoQueryValue> | undefined, useSemanticRange: boolean) {
  const range = (readStringQuery(query, "range") || "all_time") as ExpenseRange;
  const endDate = readStringQuery(query, "end_date") || DEMO_AS_OF_DATE;
  const explicitStart = readStringQuery(query, "start_date") || null;
  const startDate = explicitStart ?? (useSemanticRange ? rangeStart(range, endDate) : null);
  const accountId = readNumberQuery(query, "account_id");
  const category = readStringQuery(query, "category") as ExpenseCategory | "";
  let rows = rowsInDateRange(DEMO_EXPENSES, startDate, endDate);
  if (accountId !== null) rows = rows.filter((row) => row.account_id === accountId);
  if (category) rows = rows.filter((row) => row.category === category);
  return { rows, range, startDate, endDate };
}

function financialRangeSpecs(firstCashFlowDate: string | null, asOfDate: string) {
  const specs: Array<{ key: string; label: string; startDate: string | null; endDate: string | null }> = [
    { key: "one_month", label: "1 Month", startDate: subtractMonths(asOfDate, 1), endDate: asOfDate },
    { key: "three_months", label: "3 Months", startDate: subtractMonths(asOfDate, 3), endDate: asOfDate },
    { key: "six_months", label: "6 Months", startDate: subtractMonths(asOfDate, 6), endDate: asOfDate },
    { key: "year_to_date", label: "YTD", startDate: `${asOfDate.slice(0, 4)}-01-01`, endDate: asOfDate },
    { key: "one_year", label: "1 Year", startDate: subtractMonths(asOfDate, 12), endDate: asOfDate },
  ];
  if (firstCashFlowDate && firstCashFlowDate <= asOfDate) {
    for (let yearIndex = 0; yearIndex < 100; yearIndex += 1) {
      const startDate = addYears(firstCashFlowDate, yearIndex);
      if (startDate > asOfDate) break;
      const fullYearEnd = previousDay(addYears(firstCashFlowDate, yearIndex + 1));
      specs.push({
        key: `anniversary_year_${yearIndex + 1}`,
        label: `Year ${yearIndex + 1}`,
        startDate,
        endDate: fullYearEnd < asOfDate ? fullYearEnd : asOfDate,
      });
    }
  }
  specs.push({ key: "all_time", label: "All Time", startDate: null, endDate: null });
  return specs;
}

function buildFinancialSummary(query: Record<string, DemoQueryValue> | undefined): FinancialSummary {
  const asOfDate = readStringQuery(query, "as_of_date") || DEMO_AS_OF_DATE;
  const accountId = readNumberQuery(query, "account_id");
  const accountExpenses = DEMO_EXPENSES.filter((row) => accountId === null || row.account_id === accountId);
  const expensesThroughAsOf = rowsInDateRange(accountExpenses, null, asOfDate);
  const payoutsThroughAsOf = rowsInDateRange(DEMO_PAYOUTS, null, asOfDate);
  const cashFlowDates = [
    ...expensesThroughAsOf.map((row) => row.expense_date),
    ...payoutsThroughAsOf.map((row) => row.payout_date),
  ];
  const firstCashFlowDate = cashFlowDates.length === 0 ? null : [...cashFlowDates].sort()[0];
  const payoutDates = payoutsThroughAsOf.map((row) => row.payout_date).sort();
  const lastPayoutDate = payoutDates.at(-1) ?? null;
  const expenseTotals = buildExpenseTotals(expensesThroughAsOf, "all_time", null, asOfDate);
  const payoutTotals = buildPayoutTotals(payoutsThroughAsOf);
  const spendRows = rowsInDateRange(expensesThroughAsOf, lastPayoutDate, asOfDate);
  const ranges: FinancialSummaryRange[] = financialRangeSpecs(firstCashFlowDate, asOfDate).map((spec) => {
    const rangeExpenses = rowsInDateRange(expensesThroughAsOf, spec.startDate, spec.endDate ?? asOfDate);
    const rangePayouts = rowsInDateRange(payoutsThroughAsOf, spec.startDate, spec.endDate);
    return {
      key: spec.key,
      label: spec.label,
      start_date: spec.startDate,
      end_date: spec.endDate,
      expense_totals: buildExpenseTotals(rangeExpenses, "all_time", spec.startDate, spec.endDate ?? asOfDate),
      payout_totals: buildPayoutTotals(rangePayouts),
    };
  });
  const spendCents = sum(spendRows.map((row) => row.amount_cents));
  return {
    as_of_date: asOfDate,
    first_cash_flow_date: firstCashFlowDate,
    expense_totals: expenseTotals,
    payout_totals: payoutTotals,
    spend_since_last_payout: {
      last_payout_date: lastPayoutDate,
      total_amount: round(spendCents / 100),
      total_amount_cents: spendCents,
      expense_count: spendRows.length,
    },
    ranges,
  };
}

function buildDemoBotConfig(accountId: number): BotConfig {
  const suffix = accountId - 910_000;
  const practice = accountId === PRACTICE_ACCOUNT_ID;
  return {
    id: 8_100 + suffix,
    name: practice ? "Demo EMA Bot — Strategy Lab" : accountId === FOLLOWER_ACCOUNT_ID ? "Demo EMA Bot — Copy Account" : "Demo EMA Bot — Main Account",
    account_id: accountId,
    provider: "projectx",
    enabled: true,
    execution_mode: "dry_run",
    strategy_type: "ema_scalping",
    strategy_params: {},
    contract_id: contractId("MNQ"),
    symbol: "MNQ",
    timeframe_unit: "minute",
    timeframe_unit_number: 5,
    lookback_bars: 240,
    fast_period: 9,
    slow_period: 21,
    order_size: practice ? 1 : accountId === FOLLOWER_ACCOUNT_ID ? 1 : 2,
    max_contracts: practice ? 1 : 4,
    max_daily_loss: practice ? 250 : 650,
    max_trades_per_day: 5,
    max_open_position: practice ? 1 : 2,
    allowed_contracts: [contractId("MNQ")],
    // Bot settings are explicitly New York / Eastern wall-clock times.
    trading_start_time: "09:30",
    trading_end_time: "15:45",
    cooldown_seconds: 240,
    max_data_staleness_seconds: 600,
    allow_market_depth: false,
    created_at: "2026-07-01T13:00:00.000Z",
    updated_at: "2026-07-24T19:46:00.000Z",
  };
}

const DEMO_BOT_CONFIGS = [PRIMARY_ACCOUNT_ID, FOLLOWER_ACCOUNT_ID, PRACTICE_ACCOUNT_ID].map(buildDemoBotConfig);
const DEMO_BOT_CONFIG_BY_ID = new Map(DEMO_BOT_CONFIGS.map((config) => [config.id, config]));

function buildDemoBotActivity(config: BotConfig): BotActivity {
  const offset = config.id * 10;
  const run: BotRun = {
    id: offset + 1,
    bot_config_id: config.id,
    account_id: config.account_id,
    status: "stopped",
    dry_run: true,
    started_at: "2026-07-24T13:30:00.000Z",
    // 15:45 ET is 19:45Z during daylight-saving time.
    stopped_at: "2026-07-24T19:45:00.000Z",
    stop_reason: "session_end",
    last_heartbeat_at: "2026-07-24T19:45:00.000Z",
    last_evaluated_at: "2026-07-24T19:44:58.000Z",
    raw_state: { timezone: "America/New_York", scenario_version: DEMO_SCENARIO_VERSION },
  };
  const quantity = config.order_size;
  const decisions: BotDecision[] = [
    {
      id: offset + 101,
      bot_config_id: config.id,
      bot_run_id: run.id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      symbol: config.symbol,
      decision_type: "entry_signal",
      action: "SELL",
      reason: "9 EMA crossed below the 21 EMA after a failed opening-range reclaim.",
      candle_timestamp: "2026-07-24T14:05:00.000Z",
      price: 23784.25,
      quantity,
      correlation_id: `demo-${config.id}-entry`,
      idempotency_key: `demo-${config.id}-20260724-1405`,
      raw_payload: { dry_run: true, timezone: "America/New_York" },
      created_at: "2026-07-24T14:05:03.000Z",
    },
    {
      id: offset + 102,
      bot_config_id: config.id,
      bot_run_id: run.id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      symbol: config.symbol,
      decision_type: "exit_signal",
      action: "BUY",
      reason: "The dry-run short reached its EMA-reversion target; the simulated position was closed.",
      candle_timestamp: "2026-07-24T14:35:00.000Z",
      price: 23760.5,
      quantity,
      correlation_id: `demo-${config.id}-exit`,
      idempotency_key: `demo-${config.id}-20260724-1435`,
      raw_payload: { dry_run: true, closes_position: true },
      created_at: "2026-07-24T14:35:02.000Z",
    },
    {
      id: offset + 103,
      bot_config_id: config.id,
      bot_run_id: run.id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      symbol: config.symbol,
      decision_type: "risk_check",
      action: "HOLD",
      reason: "Cooldown is active after the completed dry-run trade.",
      candle_timestamp: "2026-07-24T14:40:00.000Z",
      price: 23763.25,
      quantity: null,
      correlation_id: `demo-${config.id}-cooldown`,
      idempotency_key: null,
      raw_payload: { remaining_cooldown_seconds: 180 },
      created_at: "2026-07-24T14:40:01.000Z",
    },
  ];
  const orderAttempts: BotOrderAttempt[] = [
    {
      id: offset + 201,
      bot_config_id: config.id,
      bot_run_id: run.id,
      bot_decision_id: decisions[0].id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      side: "SELL",
      order_type: "market",
      size: quantity,
      status: "dry_run",
      execution_mode: "dry_run",
      correlation_id: decisions[0].correlation_id,
      idempotency_key: decisions[0].idempotency_key,
      provider_order_id: null,
      rejection_reason: null,
      created_at: "2026-07-24T14:05:04.000Z",
      updated_at: "2026-07-24T14:05:04.000Z",
    },
    {
      id: offset + 202,
      bot_config_id: config.id,
      bot_run_id: run.id,
      bot_decision_id: decisions[1].id,
      account_id: config.account_id,
      contract_id: config.contract_id,
      side: "BUY",
      order_type: "market",
      size: quantity,
      status: "dry_run",
      execution_mode: "dry_run",
      correlation_id: decisions[1].correlation_id,
      idempotency_key: decisions[1].idempotency_key,
      provider_order_id: null,
      rejection_reason: null,
      created_at: "2026-07-24T14:35:03.000Z",
      updated_at: "2026-07-24T14:35:03.000Z",
    },
  ];
  const riskEvents: BotRiskEvent[] = [
    {
      id: offset + 301,
      bot_config_id: config.id,
      bot_run_id: run.id,
      account_id: config.account_id,
      severity: "info",
      code: "daily_loss_buffer_ok",
      message: "Dry-run risk check passed: daily loss and position limits had available capacity.",
      created_at: "2026-07-24T14:05:02.000Z",
    },
    {
      id: offset + 302,
      bot_config_id: config.id,
      bot_run_id: run.id,
      account_id: config.account_id,
      severity: "info",
      code: "session_complete",
      message: "Dry-run session ended at the configured 15:45 America/New_York cutoff.",
      created_at: "2026-07-24T19:45:00.000Z",
    },
  ];
  return { config, runs: [run], decisions, order_attempts: orderAttempts, risk_events: riskEvents };
}

const DEMO_CONTRACTS: ProjectXContract[] = [
  {
    id: contractId("MNQ"),
    name: "MNQ Sep 2026",
    description: "Demo Micro E-mini Nasdaq-100 futures contract",
    tick_size: 0.25,
    tick_value: 0.5,
    active_contract: true,
    symbol_id: "MNQ",
  },
  {
    id: contractId("MES"),
    name: "MES Sep 2026",
    description: "Demo Micro E-mini S&P 500 futures contract",
    tick_size: 0.25,
    tick_value: 1.25,
    active_contract: true,
    symbol_id: "MES",
  },
  {
    id: contractId("MGC"),
    name: "MGC Aug 2026",
    description: "Demo Micro Gold futures contract",
    tick_size: 0.1,
    tick_value: 1,
    active_contract: true,
    symbol_id: "MGC",
  },
];

function candleIntervalMs(unit: ProjectXMarketCandle["unit"], unitNumber: number) {
  const unitMs: Record<Exclude<ProjectXMarketCandle["unit"], "month">, number> = {
    second: 1_000,
    minute: 60_000,
    hour: 3_600_000,
    day: 86_400_000,
    week: 7 * 86_400_000,
  };
  return unit === "month" ? 0 : unitMs[unit] * unitNumber;
}

function isDemoTradingTimestamp(timestampMs: number, unit: ProjectXMarketCandle["unit"]) {
  const date = new Date(timestampMs);
  if (unit === "week" || unit === "month") return true;
  const weekday = date.getUTCDay();
  if (weekday === 0 || weekday === 6) return false;
  if (unit === "day") return true;
  const easternHour = ((date.getUTCHours() - 4 + 24) % 24) + date.getUTCMinutes() / 60;
  return easternHour >= 8 && easternHour <= 16;
}

function snapToTick(value: number, tickSize: number) {
  return round(Math.round(value / tickSize) * tickSize, tickSize === 0.1 ? 1 : 2);
}

function buildDemoCandles(query: Record<string, DemoQueryValue> | undefined): ProjectXMarketCandle[] {
  const requestedSymbol = readStringQuery(query, "symbol").toUpperCase();
  const requestedContract = readStringQuery(query, "contract_id");
  const supportedSymbols = ["MNQ", "MES", "MGC"];
  const matchedContract = requestedContract
    ? DEMO_CONTRACTS.find((contract) => contract.id === requestedContract)
    : undefined;
  if ((requestedSymbol && !supportedSymbols.includes(requestedSymbol)) || (requestedContract && !matchedContract)) {
    return [];
  }
  if (requestedSymbol && matchedContract?.symbol_id && requestedSymbol !== matchedContract.symbol_id) {
    return [];
  }
  const symbol = (requestedSymbol || matchedContract?.symbol_id || "MNQ") as DemoSymbol;
  const selectedContractId = requestedContract || contractId(symbol);
  const requestedUnit = readStringQuery(query, "unit");
  const unit = (["second", "minute", "hour", "day", "week", "month"].includes(requestedUnit)
    ? requestedUnit
    : "minute") as ProjectXMarketCandle["unit"];
  const unitNumber = Math.max(1, Math.floor(readNumberQuery(query, "unit_number") ?? 5));
  const limit = Math.min(2_000, Math.max(0, Math.floor(readNumberQuery(query, "limit") ?? 160)));
  const includePartial = readBooleanQuery(query, "include_partial_bar");
  const intervalMs = candleIntervalMs(unit, unitNumber);
  const asOfMs = Date.parse(DEMO_AS_OF_ISO);
  const requestedEndMs = Date.parse(readStringQuery(query, "end") || DEMO_AS_OF_ISO);
  const endMs = Math.min(Number.isFinite(requestedEndMs) ? requestedEndMs : asOfMs, asOfMs);
  const requestedStartMs = Date.parse(readStringQuery(query, "start"));
  const defaultStartMs = unit === "month"
    ? Date.UTC(
        new Date(endMs).getUTCFullYear(),
        new Date(endMs).getUTCMonth() - Math.max(limit * unitNumber * 2, 1),
        1,
      )
    : endMs - intervalMs * Math.max(limit * 4, 1);
  const startMs = Number.isFinite(requestedStartMs) ? requestedStartMs : defaultStartMs;
  if (limit === 0 || startMs > endMs) return [];

  const timestamps: number[] = [];
  let latestIsPartial = false;
  if (unit === "month") {
    const endDate = new Date(endMs);
    let cursor = Date.UTC(endDate.getUTCFullYear(), endDate.getUTCMonth(), 1);
    const nextMonth = Date.UTC(endDate.getUTCFullYear(), endDate.getUTCMonth() + unitNumber, 1);
    latestIsPartial = nextMonth > endMs;
    if (latestIsPartial && !includePartial) {
      cursor = Date.UTC(endDate.getUTCFullYear(), endDate.getUTCMonth() - unitNumber, 1);
      latestIsPartial = false;
    }
    for (let iteration = 0; cursor >= startMs && timestamps.length < limit && iteration < limit * 4 + 12; iteration += 1) {
      timestamps.push(cursor);
      const cursorDate = new Date(cursor);
      cursor = Date.UTC(cursorDate.getUTCFullYear(), cursorDate.getUTCMonth() - unitNumber, 1);
    }
  } else {
    let cursor = Math.floor(endMs / intervalMs) * intervalMs;
    latestIsPartial = cursor + intervalMs > endMs;
    if (latestIsPartial && !includePartial) {
      cursor -= intervalMs;
      latestIsPartial = false;
    }
    const maxIterations = Math.max(limit * 20, 100);
    for (let iteration = 0; cursor >= startMs && timestamps.length < limit && iteration < maxIterations; iteration += 1) {
      if (isDemoTradingTimestamp(cursor, unit)) timestamps.push(cursor);
      cursor -= intervalMs;
    }
  }
  timestamps.reverse();

  const profile = {
    MNQ: { base: 23_760, amplitude: 36, drift: 0.45, tick: 0.25, volume: 1_450 },
    MES: { base: 5_955, amplitude: 9, drift: 0.11, tick: 0.25, volume: 2_100 },
    MGC: { base: 3_430, amplitude: 7, drift: 0.08, tick: 0.1, volume: 820 },
  }[symbol];

  return timestamps.map((timestampMs, index) => {
    const timestampDate = new Date(timestampMs);
    const bucket = unit === "month"
      ? timestampDate.getUTCFullYear() * 12 + timestampDate.getUTCMonth()
      : Math.floor(timestampMs / intervalMs);
    const wave = Math.sin(bucket * 0.37) * profile.amplitude + Math.cos(bucket * 0.11) * profile.amplitude * 0.42;
    const open = snapToTick(profile.base + wave + (index - timestamps.length / 2) * profile.drift, profile.tick);
    const close = snapToTick(open + Math.sin(bucket * 0.23) * profile.amplitude * 0.18, profile.tick);
    const spread = Math.max(profile.tick * 2, profile.amplitude * (0.08 + (Math.abs(bucket) % 5) * 0.012));
    return {
      id: 100_000 + Math.abs(bucket % 80_000),
      contract_id: selectedContractId,
      symbol,
      live: false,
      unit,
      unit_number: unitNumber,
      timestamp: new Date(timestampMs).toISOString(),
      open,
      high: snapToTick(Math.max(open, close) + spread, profile.tick),
      low: snapToTick(Math.min(open, close) - spread, profile.tick),
      close,
      volume: Math.round(profile.volume + Math.abs(Math.sin(bucket * 0.17)) * profile.volume * 0.65),
      is_partial: includePartial && latestIsPartial && index === timestamps.length - 1,
      fetched_at: DEMO_AS_OF_ISO,
    };
  });
}

function buildLegacySummary(accountId: number): SummaryMetrics {
  const summary = buildSummary(accountId, undefined);
  const trades = getAccountTrades(accountId);
  return {
    trade_count: summary.trade_count,
    net_pnl: summary.net_pnl,
    win_rate: summary.win_rate,
    profit_factor: summary.profit_factor,
    expectancy: summary.expectancy_per_trade,
    average_win: summary.avg_win,
    average_loss: summary.avg_loss,
    average_win_loss_ratio: summary.avg_loss === 0 ? 0 : round(Math.abs(summary.avg_win / summary.avg_loss), 4),
    max_drawdown: summary.max_drawdown,
    largest_losing_trade: Math.min(0, ...trades.map(tradeNetPnl)),
    average_hold_minutes: round(average(trades.map((trade) => trade.duration_minutes ?? 0))),
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
    is_rule_break: RULE_BREAK_BY_TRADE_ID.has(trade.id),
    rule_break_type: RULE_BREAK_BY_TRADE_ID.get(trade.id) ?? null,
  }));
}

function buildStreakMetrics(accountId: number): StreakMetrics {
  const trades = [...getAccountTrades(accountId)].sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  let currentWin = 0;
  let currentLoss = 0;
  let longestWin = 0;
  let longestLoss = 0;
  let consecutiveLosses = 0;
  const afterLosses = new Map<number, number[]>([[1, []], [2, []], [3, []]]);
  for (const trade of trades) {
    const net = tradeNetPnl(trade);
    if (consecutiveLosses > 0) {
      afterLosses.get(Math.min(3, consecutiveLosses))?.push(net);
    }
    if (net > 0) {
      currentWin += 1;
      currentLoss = 0;
      consecutiveLosses = 0;
    } else if (net < 0) {
      currentLoss += 1;
      currentWin = 0;
      consecutiveLosses += 1;
    } else {
      currentWin = 0;
      currentLoss = 0;
      consecutiveLosses = 0;
    }
    longestWin = Math.max(longestWin, currentWin);
    longestLoss = Math.max(longestLoss, currentLoss);
  }
  return {
    current_win_streak: currentWin,
    current_loss_streak: currentLoss,
    longest_win_streak: longestWin,
    longest_loss_streak: longestLoss,
    pnl_after_losses: [1, 2, 3].map((lossStreak) => {
      const values = afterLosses.get(lossStreak) ?? [];
      return {
        loss_streak: lossStreak,
        trade_count: values.length,
        total_pnl: round(sum(values)),
        average_pnl: round(average(values)),
      };
    }),
  };
}

function buildBehaviorMetrics(accountId: number): BehaviorMetrics {
  const trades = getAccountTrades(accountId);
  const ruleBreaks = trades.filter((trade) => RULE_BREAK_BY_TRADE_ID.has(trade.id));
  const following = trades.filter((trade) => !RULE_BREAK_BY_TRADE_ID.has(trade.id));
  return {
    trade_count: trades.length,
    average_position_size: round(average(trades.map((trade) => trade.size)), 4),
    max_position_size: trades.length === 0 ? 0 : Math.max(...trades.map((trade) => trade.size)),
    rule_break_count: ruleBreaks.length,
    rule_break_pnl: round(sum(ruleBreaks.map(tradeNetPnl))),
    rule_following_pnl: round(sum(following.map(tradeNetPnl))),
  };
}

function buildPnlByHour(accountId: number): HourPnlPoint[] {
  const byHour = new Map<number, HourPnlPoint>();
  for (const trade of getAccountTrades(accountId)) {
    const utcHour = new Date(trade.entry_time ?? trade.timestamp).getUTCHours();
    const hour = (utcHour - 4 + 24) % 24;
    const current = byHour.get(hour) ?? { hour, trade_count: 0, pnl: 0 };
    current.trade_count += 1;
    current.pnl = round(current.pnl + tradeNetPnl(trade));
    byHour.set(hour, current);
  }
  return [...byHour.values()].sort((left, right) => left.hour - right.hour);
}

function buildPnlByDay(accountId: number): DayPnlPoint[] {
  const labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const byDay = new Map<number, DayPnlPoint>();
  for (const trade of getAccountTrades(accountId)) {
    const day = new Date(`${getTradeDay(trade)}T12:00:00.000Z`).getUTCDay();
    const current = byDay.get(day) ?? { day_of_week: day, day_label: labels[day], trade_count: 0, pnl: 0 };
    current.trade_count += 1;
    current.pnl = round(current.pnl + tradeNetPnl(trade));
    byDay.set(day, current);
  }
  return [...byDay.values()].sort((left, right) => left.day_of_week - right.day_of_week);
}

function buildPnlBySymbol(accountId: number): SymbolPnlPoint[] {
  const trades = getAccountTrades(accountId);
  const symbols = [...new Set(trades.map((trade) => trade.symbol))].sort();
  return symbols.map((symbol) => {
    const matching = trades.filter((trade) => trade.symbol === symbol);
    const wins = matching.filter((trade) => tradeNetPnl(trade) > 0).length;
    return {
      symbol,
      trade_count: matching.length,
      pnl: round(sum(matching.map(tradeNetPnl))),
      win_rate: matching.length === 0 ? 0 : round((wins / matching.length) * 100),
    };
  });
}

export function getDemoApiResponse<T>(
  path: string,
  query?: Record<string, DemoQueryValue>,
): DemoApiResponse<T> | null {
  if (path === "/api/auth/me") {
    return handled<AuthMe>({ user_id: DEMO_USER_ID, email: "demo@topsignal.local" }) as DemoApiResponse<T>;
  }

  if (path === "/api/me/providers/projectx/credentials/status") {
    return handled<ProjectXCredentialsStatus>({
      configured: true,
      decryptable: true,
      status: "ready",
      error_code: null,
    }) as DemoApiResponse<T>;
  }

  if (path === "/api/accounts") {
    const showInactive = readBooleanQuery(query, "show_inactive");
    const showMissing = readBooleanQuery(query, "show_missing");
    const includeArchived = readBooleanQuery(query, "include_archived");
    const accounts = DEMO_ACCOUNTS.filter((account) => {
      if (account.is_archived && !includeArchived) return false;
      if (account.account_state === "MISSING") return showMissing;
      if (account.account_state === "HIDDEN" || account.account_state === "LOCKED_OUT") return showInactive;
      return account.account_state === "ACTIVE";
    });
    return handled<AccountInfo[]>(accounts) as DemoApiResponse<T>;
  }

  const accountId = getRequestedAccountId(path);

  if (/^\/api\/accounts\/\d+\/last-trade$/.test(path) && accountId !== null) {
    return handled<AccountLastTradeInfo>({
      account_id: accountId,
      last_trade_at: getLastTradeAt(accountId),
      source: `demo:${DEMO_SCENARIO_VERSION}`,
    }) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/trades$/.test(path) && accountId !== null) {
    return handled<AccountTrade[]>(filterTrades(accountId, query)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/summary$/.test(path) && accountId !== null) {
    return handled<AccountSummary>(buildSummary(accountId, query)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/summary-with-point-bases$/.test(path) && accountId !== null) {
    return handled<AccountSummaryWithPointBases>(buildSummaryWithPointBases(accountId, query)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/pnl-calendar$/.test(path) && accountId !== null) {
    const trades = filterTrades(accountId, { ...query, limit: 10_000, offset: 0 });
    return handled<AccountPnlCalendarDay[]>(buildCalendarDays(trades)) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/journal\/days$/.test(path) && accountId !== null) {
    const includeArchived = readBooleanQuery(query, "include_archived");
    const entries = filterByDateRange(DEMO_JOURNALS_BY_ACCOUNT_ID.get(accountId) ?? [], query)
      .filter((entry) => includeArchived || !entry.is_archived);
    return handled<JournalDaysResponse>({ days: entries.map((entry) => entry.entry_date).sort() }) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/journal\/\d+\/images$/.test(path)) {
    return handled([]) as DemoApiResponse<T>;
  }

  if (/^\/api\/accounts\/\d+\/journal$/.test(path) && accountId !== null) {
    return handled<JournalEntriesResponse>(filterJournalEntries(accountId, query)) as DemoApiResponse<T>;
  }

  if (path === "/api/expenses/financial-summary") {
    return handled<FinancialSummary>(buildFinancialSummary(query)) as DemoApiResponse<T>;
  }

  if (path === "/api/expenses/totals") {
    const filtered = filterExpenses(query, true);
    const rows = [...filtered.rows].sort((left, right) => right.expense_date.localeCompare(left.expense_date));
    return handled<ExpenseTotals>(
      buildExpenseTotals(rows, filtered.range, filtered.startDate, filtered.endDate),
    ) as DemoApiResponse<T>;
  }

  if (path === "/api/expenses") {
    const filtered = filterExpenses(query, false);
    const rows = [...filtered.rows].sort((left, right) => right.expense_date.localeCompare(left.expense_date));
    return handled<ExpenseListResponse>({ items: limitAndOffset(rows, query, 200), total: rows.length }) as DemoApiResponse<T>;
  }

  if (path === "/api/payouts/totals") {
    const rows = filterByDateRange(DEMO_PAYOUTS, query);
    return handled<PayoutTotals>(buildPayoutTotals(rows)) as DemoApiResponse<T>;
  }

  if (path === "/api/payouts") {
    const rows = filterByDateRange(DEMO_PAYOUTS, query)
      .sort((left, right) => right.payout_date.localeCompare(left.payout_date));
    return handled<PayoutListResponse>({ items: limitAndOffset(rows, query, 200), total: rows.length }) as DemoApiResponse<T>;
  }

  if (path === "/api/bots") {
    const requestedAccountId = readNumberQuery(query, "account_id");
    const items = requestedAccountId === null
      ? DEMO_BOT_CONFIGS
      : DEMO_BOT_CONFIGS.filter((config) => config.account_id === requestedAccountId);
    return handled<BotConfigListResponse>({ items, total: items.length }) as DemoApiResponse<T>;
  }

  const botActivityMatch = /^\/api\/bots\/(\d+)\/activity$/.exec(path);
  if (botActivityMatch) {
    const config = DEMO_BOT_CONFIG_BY_ID.get(Number.parseInt(botActivityMatch[1], 10));
    return handled<BotActivity | null>(config ? buildDemoBotActivity(config) : null) as DemoApiResponse<T>;
  }

  if (path === "/api/projectx/contracts/search") {
    const searchText = readStringQuery(query, "search_text").trim().toUpperCase();
    const contracts = searchText
      ? DEMO_CONTRACTS.filter((contract) =>
          `${contract.id} ${contract.name} ${contract.description ?? ""}`.toUpperCase().includes(searchText),
        )
      : DEMO_CONTRACTS;
    return handled<ProjectXContract[]>(contracts) as DemoApiResponse<T>;
  }

  if (path === "/api/projectx/candles") {
    return handled<ProjectXMarketCandle[]>(buildDemoCandles(query)) as DemoApiResponse<T>;
  }

  const metricsAccountId = getMetricsAccountId(query);
  if (path === "/metrics/summary") {
    return handled<SummaryMetrics>(buildLegacySummary(metricsAccountId)) as DemoApiResponse<T>;
  }
  if (path === "/metrics/pnl-by-hour") {
    return handled<HourPnlPoint[]>(buildPnlByHour(metricsAccountId)) as DemoApiResponse<T>;
  }
  if (path === "/metrics/pnl-by-day") {
    return handled<DayPnlPoint[]>(buildPnlByDay(metricsAccountId)) as DemoApiResponse<T>;
  }
  if (path === "/metrics/pnl-by-symbol") {
    return handled<SymbolPnlPoint[]>(buildPnlBySymbol(metricsAccountId)) as DemoApiResponse<T>;
  }
  if (path === "/metrics/streaks") {
    return handled<StreakMetrics>(buildStreakMetrics(metricsAccountId)) as DemoApiResponse<T>;
  }
  if (path === "/metrics/behavior") {
    return handled<BehaviorMetrics>(buildBehaviorMetrics(metricsAccountId)) as DemoApiResponse<T>;
  }
  if (path === "/trades") {
    return handled<TradeRecord[]>(buildLegacyTrades(metricsAccountId)) as DemoApiResponse<T>;
  }

  return null;
}
