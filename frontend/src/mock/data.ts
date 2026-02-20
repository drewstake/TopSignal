export type TradeSide = "Long" | "Short";
export type TradeOutcome = "Win" | "Loss" | "Flat";
export type JournalMood = "Focused" | "Neutral" | "Frustrated" | "Confident";
export type FuturesRoot = "ES" | "NQ" | "RTY" | "CL" | "GC" | "SI" | "6E" | "ZN";
export type TradeSession = "RTH Open" | "Midday" | "Power Hour" | "London" | "Asia" | "NY Open";

interface FuturesContractSpec {
  tickSize: number;
  tickValue: number;
}

const contractSpecs: Record<FuturesRoot, FuturesContractSpec> = {
  ES: { tickSize: 0.25, tickValue: 12.5 },
  NQ: { tickSize: 0.25, tickValue: 5 },
  RTY: { tickSize: 0.1, tickValue: 5 },
  CL: { tickSize: 0.01, tickValue: 10 },
  GC: { tickSize: 0.1, tickValue: 10 },
  SI: { tickSize: 0.005, tickValue: 25 },
  "6E": { tickSize: 0.00005, tickValue: 6.25 },
  ZN: { tickSize: 0.015625, tickValue: 15.625 },
};

export interface Trade {
  id: string;
  symbol: FuturesRoot;
  contract: string;
  side: TradeSide;
  openedAt: string;
  closedAt: string;
  qty: number;
  entryPrice: number;
  exitPrice: number;
  points: number;
  ticks: number;
  pnlUsd: number;
  strategy: string;
  account: string;
  riskMultiple: number;
  setupQuality: number;
  ruleBreached: boolean;
  session: TradeSession;
  notes: string;
  entry: number;
  exit: number;
  quantity: number;
  pnl: number;
}

export interface KpiMetric {
  id: string;
  label: string;
  value: string;
  changePct: number;
  hint: string;
}

export interface DailyPnl {
  day: string;
  value: number;
}

export interface RiskRule {
  id: string;
  name: string;
  progress: number;
  status: "good" | "warning" | "risk";
  detail: string;
}

export interface SymbolPerformance {
  symbol: FuturesRoot;
  trades: number;
  winRate: number;
  avgHold: string;
  pnl: number;
  avgPoints?: number;
  profitFactor?: number;
}

export interface ContractPerformance {
  symbol: FuturesRoot;
  trades: number;
  winRate: number;
  avgHold: string;
  avgPoints: number;
  profitFactor: number;
  netPnlUsd: number;
}

export interface SessionPerformance {
  session: TradeSession;
  trades: number;
  winRate: number;
  avgPoints: number;
  netPnlUsd: number;
}

export interface StreakStat {
  label: string;
  value: string;
  tone: "positive" | "negative" | "neutral";
  helper: string;
}

export interface JournalEntry {
  id: string;
  date: string;
  title: string;
  mood: JournalMood;
  body: string;
  tags: string[];
}

interface TradeSeed {
  id: string;
  symbol: FuturesRoot;
  contract: string;
  side: TradeSide;
  qty: number;
  entryPrice: number;
  exitPrice: number;
  openedAt: string;
  closedAt: string;
  strategy: string;
  account: string;
  riskMultiple: number;
  setupQuality: number;
  ruleBreached: boolean;
  session: TradeSession;
  notes: string;
}

const tradeSeeds: TradeSeed[] = [
  {
    id: "TS-5020",
    symbol: "ES",
    contract: "ESH6",
    side: "Long",
    qty: 3,
    entryPrice: 5312.25,
    exitPrice: 5316.75,
    openedAt: "2026-02-20T14:22:00Z",
    closedAt: "2026-02-20T15:04:00Z",
    strategy: "RTH Break and Hold",
    account: "Topstep Combine 31428",
    riskMultiple: 2.1,
    setupQuality: 9,
    ruleBreached: false,
    session: "Power Hour",
    notes: "Held runner into settlement ramp after reclaim.",
  },
  {
    id: "TS-5019",
    symbol: "NQ",
    contract: "NQH6",
    side: "Short",
    qty: 2,
    entryPrice: 19042.75,
    exitPrice: 19031.5,
    openedAt: "2026-02-20T13:55:00Z",
    closedAt: "2026-02-20T14:18:00Z",
    strategy: "Failed Auction Reversal",
    account: "Topstep Combine 31428",
    riskMultiple: 1.5,
    setupQuality: 8,
    ruleBreached: false,
    session: "Midday",
    notes: "Shorted back inside value after upside failure.",
  },
  {
    id: "TS-5018",
    symbol: "CL",
    contract: "CLH6",
    side: "Long",
    qty: 2,
    entryPrice: 74.18,
    exitPrice: 73.95,
    openedAt: "2026-02-20T12:31:00Z",
    closedAt: "2026-02-20T13:06:00Z",
    strategy: "Inventory Rebalance",
    account: "Topstep Combine 31428",
    riskMultiple: -1.0,
    setupQuality: 6,
    ruleBreached: false,
    session: "London",
    notes: "Loss on continuation attempt before data release.",
  },
  {
    id: "TS-5017",
    symbol: "GC",
    contract: "GCH6",
    side: "Long",
    qty: 1,
    entryPrice: 2364.2,
    exitPrice: 2368.9,
    openedAt: "2026-02-19T14:47:00Z",
    closedAt: "2026-02-19T16:02:00Z",
    strategy: "Trend Day Pullback",
    account: "Topstep Combine 31428",
    riskMultiple: 2.0,
    setupQuality: 8,
    ruleBreached: false,
    session: "Power Hour",
    notes: "Added on higher low after CPI reaction settled.",
  },
  {
    id: "TS-5016",
    symbol: "RTY",
    contract: "RTYH6",
    side: "Short",
    qty: 3,
    entryPrice: 2128.4,
    exitPrice: 2125.9,
    openedAt: "2026-02-19T14:34:00Z",
    closedAt: "2026-02-19T15:15:00Z",
    strategy: "Value Area Rejection",
    account: "Topstep Combine 31428",
    riskMultiple: 1.4,
    setupQuality: 7,
    ruleBreached: false,
    session: "Midday",
    notes: "Captured rotation back to overnight midpoint.",
  },
  {
    id: "TS-5015",
    symbol: "SI",
    contract: "SIH6",
    side: "Short",
    qty: 1,
    entryPrice: 31.245,
    exitPrice: 31.315,
    openedAt: "2026-02-19T11:03:00Z",
    closedAt: "2026-02-19T11:42:00Z",
    strategy: "Momentum Fade",
    account: "Topstep Combine 31428",
    riskMultiple: -0.8,
    setupQuality: 5,
    ruleBreached: true,
    session: "London",
    notes: "Early short during strong trend, exited on stop.",
  },
  {
    id: "TS-5014",
    symbol: "6E",
    contract: "6EH6",
    side: "Long",
    qty: 4,
    entryPrice: 1.08465,
    exitPrice: 1.08555,
    openedAt: "2026-02-18T14:49:00Z",
    closedAt: "2026-02-18T15:31:00Z",
    strategy: "London Continuation",
    account: "Topstep Combine 31428",
    riskMultiple: 1.8,
    setupQuality: 8,
    ruleBreached: false,
    session: "London",
    notes: "Breakout pullback aligned with EU bond strength.",
  },
  {
    id: "TS-5013",
    symbol: "ZN",
    contract: "ZNH6",
    side: "Short",
    qty: 2,
    entryPrice: 111.78125,
    exitPrice: 111.640625,
    openedAt: "2026-02-18T12:28:00Z",
    closedAt: "2026-02-18T13:22:00Z",
    strategy: "Fed Speaker Repricing",
    account: "Topstep Combine 31428",
    riskMultiple: 1.2,
    setupQuality: 7,
    ruleBreached: false,
    session: "Midday",
    notes: "Short after failed bounce at prior swing high.",
  },
  {
    id: "TS-5012",
    symbol: "ES",
    contract: "ESH6",
    side: "Short",
    qty: 1,
    entryPrice: 5308.75,
    exitPrice: 5311.5,
    openedAt: "2026-02-18T09:41:00Z",
    closedAt: "2026-02-18T10:05:00Z",
    strategy: "Open Drive Countertrend",
    account: "Topstep Combine 31428",
    riskMultiple: -0.6,
    setupQuality: 5,
    ruleBreached: false,
    session: "RTH Open",
    notes: "Tried fading open drive too early.",
  },
  {
    id: "TS-5011",
    symbol: "NQ",
    contract: "NQH6",
    side: "Long",
    qty: 1,
    entryPrice: 18988.25,
    exitPrice: 19002.75,
    openedAt: "2026-02-17T14:57:00Z",
    closedAt: "2026-02-17T15:46:00Z",
    strategy: "VWAP Reclaim",
    account: "Topstep Combine 31428",
    riskMultiple: 1.6,
    setupQuality: 7,
    ruleBreached: false,
    session: "Power Hour",
    notes: "Long after reclaim and volume expansion over VWAP.",
  },
  {
    id: "TS-5010",
    symbol: "CL",
    contract: "CLH6",
    side: "Short",
    qty: 1,
    entryPrice: 74.84,
    exitPrice: 74.52,
    openedAt: "2026-02-17T13:38:00Z",
    closedAt: "2026-02-17T14:20:00Z",
    strategy: "Failed Breakout",
    account: "Topstep Combine 31428",
    riskMultiple: 1.3,
    setupQuality: 7,
    ruleBreached: false,
    session: "Midday",
    notes: "Breakout failed above prior high, sold retest.",
  },
  {
    id: "TS-5009",
    symbol: "GC",
    contract: "GCH6",
    side: "Short",
    qty: 2,
    entryPrice: 2357.4,
    exitPrice: 2359.1,
    openedAt: "2026-02-17T10:41:00Z",
    closedAt: "2026-02-17T11:08:00Z",
    strategy: "Opening Reversal",
    account: "Topstep Combine 31428",
    riskMultiple: -0.9,
    setupQuality: 5,
    ruleBreached: false,
    session: "RTH Open",
    notes: "Stopped quickly after trend continuation higher.",
  },
  {
    id: "TS-5008",
    symbol: "RTY",
    contract: "RTYH6",
    side: "Long",
    qty: 2,
    entryPrice: 2119.7,
    exitPrice: 2122.1,
    openedAt: "2026-02-14T15:42:00Z",
    closedAt: "2026-02-14T16:24:00Z",
    strategy: "Power Hour Breakout",
    account: "Topstep Combine 31428",
    riskMultiple: 1.1,
    setupQuality: 6,
    ruleBreached: false,
    session: "Power Hour",
    notes: "Squeeze into close after low-volume base.",
  },
  {
    id: "TS-5007",
    symbol: "6E",
    contract: "6EH6",
    side: "Short",
    qty: 3,
    entryPrice: 1.08745,
    exitPrice: 1.08695,
    openedAt: "2026-02-14T13:35:00Z",
    closedAt: "2026-02-14T14:11:00Z",
    strategy: "ECB Fade",
    account: "Topstep Combine 31428",
    riskMultiple: 0.9,
    setupQuality: 7,
    ruleBreached: false,
    session: "London",
    notes: "Faded policy headline spike back into range.",
  },
  {
    id: "TS-5006",
    symbol: "ZN",
    contract: "ZNH6",
    side: "Long",
    qty: 1,
    entryPrice: 111.546875,
    exitPrice: 111.640625,
    openedAt: "2026-02-13T15:05:00Z",
    closedAt: "2026-02-13T15:39:00Z",
    strategy: "Late Session Reversion",
    account: "Topstep Combine 31428",
    riskMultiple: 0.7,
    setupQuality: 6,
    ruleBreached: false,
    session: "Power Hour",
    notes: "Bought back above value low after washout.",
  },
  {
    id: "TS-5005",
    symbol: "SI",
    contract: "SIH6",
    side: "Long",
    qty: 1,
    entryPrice: 30.985,
    exitPrice: 31.04,
    openedAt: "2026-02-13T10:58:00Z",
    closedAt: "2026-02-13T11:26:00Z",
    strategy: "Mean Reversion Bounce",
    account: "Topstep Combine 31428",
    riskMultiple: 1.0,
    setupQuality: 7,
    ruleBreached: false,
    session: "RTH Open",
    notes: "Reclaimed overnight VWAP with strong tape.",
  },
];

function roundTo(value: number, decimals = 2) {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function computeHoldMinutes(openedAt: string, closedAt: string) {
  const opened = new Date(openedAt).getTime();
  const closed = new Date(closedAt).getTime();
  return Math.max(1, Math.round((closed - opened) / 60000));
}

function formatCurrency(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPercentage(value: number) {
  return `${roundTo(value, 1).toFixed(1)}%`;
}

function formatAvgHold(minutes: number) {
  if (minutes < 60) {
    return `${minutes}m`;
  }

  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return `${hours}h ${remainder.toString().padStart(2, "0")}m`;
}

function toTrade(seed: TradeSeed): Trade {
  const spec = contractSpecs[seed.symbol];
  const sideMultiplier = seed.side === "Long" ? 1 : -1;
  const points = roundTo((seed.exitPrice - seed.entryPrice) * sideMultiplier, 6);
  const ticks = roundTo(points / spec.tickSize, 2);
  const pnlUsd = roundTo(ticks * spec.tickValue * seed.qty, 2);

  return {
    ...seed,
    points,
    ticks,
    pnlUsd,
    entry: seed.entryPrice,
    exit: seed.exitPrice,
    quantity: seed.qty,
    pnl: pnlUsd,
  };
}

export const mockTrades: Trade[] = tradeSeeds.map(toTrade);

const chronologicalTrades = [...mockTrades].sort((a, b) => a.closedAt.localeCompare(b.closedAt));

function sum(values: number[]) {
  return roundTo(values.reduce((total, value) => total + value, 0), 2);
}

function average(values: number[]) {
  if (values.length === 0) {
    return 0;
  }

  return roundTo(values.reduce((total, value) => total + value, 0) / values.length, 2);
}

function calculateMaxDrawdown(trades: Trade[]) {
  let equity = 0;
  let peak = 0;
  let drawdown = 0;

  for (const trade of trades) {
    equity += trade.pnlUsd;
    peak = Math.max(peak, equity);
    drawdown = Math.min(drawdown, equity - peak);
  }

  return roundTo(drawdown, 2);
}

const netPnl = sum(mockTrades.map((trade) => trade.pnlUsd));
const winCount = mockTrades.filter((trade) => trade.pnlUsd > 0).length;
const averageHoldMinutes = average(mockTrades.map((trade) => computeHoldMinutes(trade.openedAt, trade.closedAt)));
const maxDrawdown = calculateMaxDrawdown(chronologicalTrades);

export const kpiMetrics: KpiMetric[] = [
  { id: "net-pnl", label: "Net PnL", value: formatCurrency(netPnl), changePct: 6.4, hint: "Last 16 futures trades" },
  {
    id: "win-rate",
    label: "Win Rate",
    value: formatPercentage((winCount / mockTrades.length) * 100),
    changePct: 1.2,
    hint: "Quality over frequency",
  },
  {
    id: "trade-count",
    label: "Trades",
    value: `${mockTrades.length}`,
    changePct: -4.1,
    hint: "Focused session count",
  },
  {
    id: "avg-hold",
    label: "Avg Hold",
    value: formatAvgHold(Math.round(averageHoldMinutes)),
    changePct: 0.8,
    hint: "Controlled exposure time",
  },
  {
    id: "max-drawdown",
    label: "Max Drawdown",
    value: formatCurrency(maxDrawdown),
    changePct: 2.3,
    hint: "Peak-to-valley closed PnL",
  },
];

const startingBalance = 50000;
export const equityCurve = chronologicalTrades.reduce<number[]>((curve, trade) => {
  const prior = curve.at(-1) ?? startingBalance;
  curve.push(roundTo(prior + trade.pnlUsd, 2));
  return curve;
}, []);

const dailyTotals = new Map<string, number>();
for (const trade of chronologicalTrades) {
  const dayKey = trade.closedAt.slice(0, 10);
  dailyTotals.set(dayKey, roundTo((dailyTotals.get(dayKey) ?? 0) + trade.pnlUsd, 2));
}

const dayLabelFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

export const dailyPnl: DailyPnl[] = [...dailyTotals.entries()].map(([day, value]) => ({
  day: dayLabelFormatter.format(new Date(`${day}T00:00:00Z`)),
  value,
}));

export const riskRules: RiskRule[] = [
  {
    id: "daily-loss-limit",
    name: "Daily Loss Limit",
    progress: 54,
    status: "good",
    detail: "$1,620 of $3,000 limit used",
  },
  {
    id: "max-positions",
    name: "Max Contracts Rule",
    progress: 76,
    status: "warning",
    detail: "High size reached in NQ at midday",
  },
  {
    id: "scaling",
    name: "Scaling Discipline",
    progress: 67,
    status: "good",
    detail: "Average add size stayed within plan",
  },
  {
    id: "revenge-guard",
    name: "Cooldown After Loss",
    progress: 88,
    status: "risk",
    detail: "One immediate re-entry flagged in SI",
  },
];

const tradedRoots = [...new Set(mockTrades.map((trade) => trade.symbol))] as FuturesRoot[];

function profitFactorFor(trades: Trade[]) {
  const grossProfit = sum(trades.filter((trade) => trade.pnlUsd > 0).map((trade) => trade.pnlUsd));
  const grossLoss = Math.abs(sum(trades.filter((trade) => trade.pnlUsd < 0).map((trade) => trade.pnlUsd)));

  if (grossLoss === 0) {
    return 99;
  }

  return roundTo(grossProfit / grossLoss, 2);
}

export const contractPerformance: ContractPerformance[] = tradedRoots
  .map((symbol) => {
    const trades = mockTrades.filter((trade) => trade.symbol === symbol);
    const wins = trades.filter((trade) => trade.pnlUsd > 0).length;
    const avgHoldMinutes = average(trades.map((trade) => computeHoldMinutes(trade.openedAt, trade.closedAt)));
    const avgPoints = average(trades.map((trade) => trade.points));

    return {
      symbol,
      trades: trades.length,
      winRate: roundTo((wins / trades.length) * 100, 1),
      avgHold: formatAvgHold(Math.round(avgHoldMinutes)),
      avgPoints,
      profitFactor: profitFactorFor(trades),
      netPnlUsd: sum(trades.map((trade) => trade.pnlUsd)),
    };
  })
  .sort((left, right) => right.netPnlUsd - left.netPnlUsd);

export const symbolPerformance: SymbolPerformance[] = contractPerformance.map((row) => ({
  symbol: row.symbol,
  trades: row.trades,
  winRate: row.winRate,
  avgHold: row.avgHold,
  pnl: row.netPnlUsd,
  avgPoints: row.avgPoints,
  profitFactor: row.profitFactor,
}));

const sessionOrder: TradeSession[] = ["RTH Open", "London", "Midday", "Power Hour", "Asia", "NY Open"];

export const sessionPerformance: SessionPerformance[] = sessionOrder
  .map((session) => {
    const trades = mockTrades.filter((trade) => trade.session === session);
    if (trades.length === 0) {
      return null;
    }

    const wins = trades.filter((trade) => trade.pnlUsd > 0).length;
    return {
      session,
      trades: trades.length,
      winRate: roundTo((wins / trades.length) * 100, 1),
      avgPoints: average(trades.map((trade) => trade.points)),
      netPnlUsd: sum(trades.map((trade) => trade.pnlUsd)),
    };
  })
  .filter((item): item is SessionPerformance => item !== null)
  .sort((left, right) => right.netPnlUsd - left.netPnlUsd);

export const heatmapLabels = ["Mon", "Tue", "Wed", "Thu", "Fri"];

export const timeOfDayHeatmap = [
  { hour: "07", values: [0.36, 0.28, 0.33, 0.24, 0.31] },
  { hour: "08", values: [0.58, 0.49, 0.55, 0.47, 0.52] },
  { hour: "09", values: [0.8, 0.74, 0.79, 0.7, 0.76] },
  { hour: "10", values: [0.67, 0.61, 0.64, 0.57, 0.62] },
  { hour: "11", values: [0.42, 0.38, 0.44, 0.35, 0.4] },
  { hour: "12", values: [0.35, 0.3, 0.34, 0.29, 0.33] },
  { hour: "13", values: [0.48, 0.42, 0.46, 0.39, 0.45] },
  { hour: "14", values: [0.62, 0.55, 0.59, 0.51, 0.57] },
  { hour: "15", values: [0.71, 0.65, 0.69, 0.63, 0.68] },
];

export const streakStats: StreakStat[] = [
  {
    label: "Best Streak",
    value: "4 wins",
    tone: "positive",
    helper: "Momentum cluster on Feb 13-14",
  },
  {
    label: "Current Streak",
    value: "2 wins",
    tone: "neutral",
    helper: "Positive close into week end",
  },
  {
    label: "Worst Streak",
    value: "1 loss",
    tone: "negative",
    helper: "Losses cut quickly at invalidation",
  },
  {
    label: "Recovery Time",
    value: "1 session",
    tone: "positive",
    helper: "Average time to return green",
  },
];

export const journalEntries: JournalEntry[] = [
  {
    id: "JR-104",
    date: "2026-02-20",
    title: "NQ failure setup executed cleanly",
    mood: "Focused",
    body: "Held short only after auction failed back inside value. The entry waited for absorption and avoided chasing.",
    tags: ["nq", "auction", "discipline"],
  },
  {
    id: "JR-103",
    date: "2026-02-19",
    title: "Silver trade violated context",
    mood: "Frustrated",
    body: "SI short had weak context against higher-timeframe trend. Need to enforce trend filter before entering metals.",
    tags: ["si", "context", "risk"],
  },
  {
    id: "JR-102",
    date: "2026-02-18",
    title: "Great pacing in rates and euro",
    mood: "Confident",
    body: "ZN and 6E trades were patient. I waited for pullbacks instead of entering first impulse and sized correctly.",
    tags: ["6e", "zn", "patience"],
  },
  {
    id: "JR-101",
    date: "2026-02-17",
    title: "Crude setup quality improved",
    mood: "Neutral",
    body: "CL short followed plan and respected risk. Continue limiting crude trades to A and B setups with clear invalidation.",
    tags: ["cl", "playbook"],
  },
];
