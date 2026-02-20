export type TradeSide = "Long" | "Short";
export type TradeOutcome = "Win" | "Loss" | "Flat";
export type JournalMood = "Focused" | "Neutral" | "Frustrated" | "Confident";

export interface Trade {
  id: string;
  symbol: string;
  side: TradeSide;
  openedAt: string;
  closedAt: string;
  entry: number;
  exit: number;
  quantity: number;
  pnl: number;
  riskMultiple: number;
  strategy: string;
  setupQuality: number;
  ruleBreached: boolean;
  session: "NY Open" | "London" | "Asia";
  notes: string;
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
  symbol: string;
  trades: number;
  winRate: number;
  avgHold: string;
  pnl: number;
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

export const kpiMetrics: KpiMetric[] = [
  { id: "net", label: "Net PnL", value: "$24,830", changePct: 6.2, hint: "Month to date" },
  { id: "win", label: "Win Rate", value: "58.4%", changePct: 1.8, hint: "Last 60 trades" },
  { id: "profit", label: "Profit Factor", value: "1.84", changePct: 0.4, hint: "Gross wins / losses" },
  { id: "drawdown", label: "Max Drawdown", value: "-4.3%", changePct: -0.9, hint: "Improved from January" },
  { id: "expectancy", label: "Expectancy", value: "$182", changePct: 3.1, hint: "Per trade" },
  { id: "avg-risk", label: "Avg Risk", value: "0.73R", changePct: -1.1, hint: "Position sizing" },
];

export const equityCurve = [
  10240, 10510, 10420, 10820, 11120, 11300, 11240, 11610, 11940, 11800, 12110, 12330, 12680,
];

export const dailyPnl: DailyPnl[] = [
  { day: "Mon", value: 420 },
  { day: "Tue", value: -160 },
  { day: "Wed", value: 310 },
  { day: "Thu", value: -90 },
  { day: "Fri", value: 560 },
  { day: "Mon", value: 230 },
  { day: "Tue", value: -210 },
  { day: "Wed", value: 380 },
  { day: "Thu", value: 120 },
  { day: "Fri", value: 460 },
];

export const riskRules: RiskRule[] = [
  {
    id: "daily-loss",
    name: "Daily Loss Limit",
    progress: 62,
    status: "good",
    detail: "$1,240 used of $2,000 limit",
  },
  {
    id: "exposure",
    name: "Sector Exposure",
    progress: 78,
    status: "warning",
    detail: "Tech allocation nearing threshold",
  },
  {
    id: "hold-time",
    name: "Overnight Hold Rule",
    progress: 22,
    status: "good",
    detail: "No unauthorized overnight positions",
  },
  {
    id: "revenge",
    name: "Revenge Trade Guard",
    progress: 88,
    status: "risk",
    detail: "Two same-direction re-entries flagged",
  },
];

export const mockTrades: Trade[] = [
  {
    id: "TR-4012",
    symbol: "NVDA",
    side: "Long",
    openedAt: "2026-02-18 09:41",
    closedAt: "2026-02-18 10:27",
    entry: 906.4,
    exit: 919.1,
    quantity: 25,
    pnl: 317.5,
    riskMultiple: 1.9,
    strategy: "Opening Range Breakout",
    setupQuality: 8,
    ruleBreached: false,
    session: "NY Open",
    notes: "Volume confirmation on breakout candle.",
  },
  {
    id: "TR-4011",
    symbol: "TSLA",
    side: "Short",
    openedAt: "2026-02-18 08:54",
    closedAt: "2026-02-18 09:16",
    entry: 198.1,
    exit: 201.2,
    quantity: 45,
    pnl: -139.5,
    riskMultiple: -0.8,
    strategy: "VWAP Fade",
    setupQuality: 6,
    ruleBreached: false,
    session: "London",
    notes: "Early short before trend reversal.",
  },
  {
    id: "TR-4010",
    symbol: "AAPL",
    side: "Long",
    openedAt: "2026-02-17 10:15",
    closedAt: "2026-02-17 11:03",
    entry: 213.6,
    exit: 216.2,
    quantity: 60,
    pnl: 156,
    riskMultiple: 1.2,
    strategy: "Trend Pullback",
    setupQuality: 7,
    ruleBreached: false,
    session: "NY Open",
    notes: "Held through first pullback and scaled out.",
  },
  {
    id: "TR-4009",
    symbol: "MSFT",
    side: "Short",
    openedAt: "2026-02-17 09:28",
    closedAt: "2026-02-17 09:49",
    entry: 422.8,
    exit: 418.7,
    quantity: 30,
    pnl: 123,
    riskMultiple: 0.9,
    strategy: "Gap Fill",
    setupQuality: 7,
    ruleBreached: false,
    session: "NY Open",
    notes: "Quick rotation back to prior close.",
  },
  {
    id: "TR-4008",
    symbol: "META",
    side: "Long",
    openedAt: "2026-02-16 14:05",
    closedAt: "2026-02-16 15:11",
    entry: 481.3,
    exit: 477.9,
    quantity: 28,
    pnl: -95.2,
    riskMultiple: -0.7,
    strategy: "Momentum Continuation",
    setupQuality: 5,
    ruleBreached: true,
    session: "NY Open",
    notes: "Late entry after extension.",
  },
  {
    id: "TR-4007",
    symbol: "AMD",
    side: "Long",
    openedAt: "2026-02-16 11:19",
    closedAt: "2026-02-16 12:42",
    entry: 182.4,
    exit: 187.6,
    quantity: 70,
    pnl: 364,
    riskMultiple: 2.3,
    strategy: "Break and Retest",
    setupQuality: 9,
    ruleBreached: false,
    session: "NY Open",
    notes: "Strong continuation after reclaim.",
  },
  {
    id: "TR-4006",
    symbol: "AMZN",
    side: "Short",
    openedAt: "2026-02-15 13:03",
    closedAt: "2026-02-15 13:31",
    entry: 177.2,
    exit: 176.7,
    quantity: 90,
    pnl: 45,
    riskMultiple: 0.3,
    strategy: "Range Reversal",
    setupQuality: 5,
    ruleBreached: false,
    session: "NY Open",
    notes: "Partial win into mid-day chop.",
  },
  {
    id: "TR-4005",
    symbol: "NFLX",
    side: "Long",
    openedAt: "2026-02-15 10:44",
    closedAt: "2026-02-15 11:08",
    entry: 634.6,
    exit: 628.9,
    quantity: 18,
    pnl: -102.6,
    riskMultiple: -0.9,
    strategy: "Earnings Drift",
    setupQuality: 4,
    ruleBreached: true,
    session: "NY Open",
    notes: "Ignored weak breadth.",
  },
  {
    id: "TR-4004",
    symbol: "GOOGL",
    side: "Short",
    openedAt: "2026-02-14 09:34",
    closedAt: "2026-02-14 10:01",
    entry: 168.5,
    exit: 165.9,
    quantity: 75,
    pnl: 195,
    riskMultiple: 1.5,
    strategy: "Opening Fade",
    setupQuality: 8,
    ruleBreached: false,
    session: "NY Open",
    notes: "Clear rejection from premarket high.",
  },
  {
    id: "TR-4003",
    symbol: "SPY",
    side: "Long",
    openedAt: "2026-02-14 12:12",
    closedAt: "2026-02-14 13:20",
    entry: 529.8,
    exit: 532.2,
    quantity: 50,
    pnl: 120,
    riskMultiple: 0.8,
    strategy: "Lunch Breakout",
    setupQuality: 6,
    ruleBreached: false,
    session: "NY Open",
    notes: "Slow grind higher with low volatility.",
  },
  {
    id: "TR-4002",
    symbol: "QQQ",
    side: "Short",
    openedAt: "2026-02-13 09:42",
    closedAt: "2026-02-13 10:22",
    entry: 463.4,
    exit: 459.3,
    quantity: 40,
    pnl: 164,
    riskMultiple: 1.1,
    strategy: "Trend Pullback",
    setupQuality: 7,
    ruleBreached: false,
    session: "NY Open",
    notes: "Strong downside follow through.",
  },
  {
    id: "TR-4001",
    symbol: "IWM",
    side: "Long",
    openedAt: "2026-02-13 08:58",
    closedAt: "2026-02-13 09:12",
    entry: 208.4,
    exit: 207.3,
    quantity: 80,
    pnl: -88,
    riskMultiple: -0.6,
    strategy: "Pre-open Reversal",
    setupQuality: 5,
    ruleBreached: false,
    session: "London",
    notes: "Low conviction setup before cash open.",
  },
];

export const symbolPerformance: SymbolPerformance[] = [
  { symbol: "NVDA", trades: 18, winRate: 67, avgHold: "39m", pnl: 3140 },
  { symbol: "AMD", trades: 14, winRate: 64, avgHold: "42m", pnl: 2120 },
  { symbol: "AAPL", trades: 22, winRate: 55, avgHold: "31m", pnl: 1640 },
  { symbol: "TSLA", trades: 17, winRate: 47, avgHold: "36m", pnl: -410 },
  { symbol: "META", trades: 11, winRate: 45, avgHold: "49m", pnl: -290 },
];

export const heatmapLabels = ["Mon", "Tue", "Wed", "Thu", "Fri"];

export const timeOfDayHeatmap = [
  { hour: "08", values: [0.2, 0.35, 0.45, 0.31, 0.42] },
  { hour: "09", values: [0.72, 0.68, 0.8, 0.63, 0.75] },
  { hour: "10", values: [0.66, 0.52, 0.59, 0.48, 0.53] },
  { hour: "11", values: [0.43, 0.37, 0.4, 0.35, 0.33] },
  { hour: "12", values: [0.27, 0.24, 0.21, 0.25, 0.29] },
  { hour: "13", values: [0.38, 0.33, 0.35, 0.3, 0.41] },
  { hour: "14", values: [0.5, 0.47, 0.56, 0.52, 0.6] },
  { hour: "15", values: [0.58, 0.62, 0.67, 0.61, 0.71] },
];

export const streakStats: StreakStat[] = [
  {
    label: "Best Streak",
    value: "7 wins",
    tone: "positive",
    helper: "Jan 29 to Feb 7",
  },
  {
    label: "Current Streak",
    value: "2 wins",
    tone: "neutral",
    helper: "Momentum stable",
  },
  {
    label: "Worst Streak",
    value: "4 losses",
    tone: "negative",
    helper: "Mostly overtrading days",
  },
  {
    label: "Recovery Time",
    value: "1.6 sessions",
    tone: "positive",
    helper: "Back to green after red day",
  },
];

export const journalEntries: JournalEntry[] = [
  {
    id: "JR-921",
    date: "2026-02-18",
    title: "Stayed patient at open",
    mood: "Focused",
    body: "Waited for confirmation on NVDA instead of chasing first impulse. Position sizing stayed within plan and exits were clean.",
    tags: ["discipline", "open", "risk"],
  },
  {
    id: "JR-920",
    date: "2026-02-17",
    title: "Mixed execution on pullbacks",
    mood: "Neutral",
    body: "AAPL trade followed plan, but META entry was late and too close to extension. Need stricter checklist before entries.",
    tags: ["execution", "checklist"],
  },
  {
    id: "JR-919",
    date: "2026-02-16",
    title: "Revenge risk after first loss",
    mood: "Frustrated",
    body: "After early stop out, I forced an NFLX trade with weak context. Must pause 5 minutes after each full-loss trade.",
    tags: ["psychology", "revenge"],
  },
  {
    id: "JR-918",
    date: "2026-02-15",
    title: "Strong process day",
    mood: "Confident",
    body: "Journaled before session, defined A+ setups, and stuck with them. Only four trades and all aligned with playbook.",
    tags: ["routine", "consistency"],
  },
];
