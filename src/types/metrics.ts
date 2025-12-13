import type { PnlMode } from "./trades";

export type Kpi = { label: string; value: string; sub?: string };

export type EquityPoint = {
  date: string; // YYYY-MM-DD
  pnl: number; // net day PnL
  equity: number; // cumulative net PnL
  drawdown: number; // positive number
  trades: number;
  contracts: number;
};

export type DayPoint = {
  date: string; // YYYY-MM-DD
  grossPnl: number;
  fees: number;
  netPnl: number;
  trades: number; // realized trades (profitAndLoss != null)
  contracts: number; // sum size (all)
  buys: number;
  sells: number;
};

export type BucketRow = { label: string; count: number; winRate: number; pnl: number };
export type BreakdownRow = { key: string; trades: number; winRate: number; pnl: number; avgR: number };
export type ExitRow = { exitType: string; trades: number; winRate: number; pnl: number; avgR: number };

export type DashboardMetrics = {
  mode: PnlMode;

  // topstep scoreboard
  totalPnl: number;
  tradeWinPct: number;
  dayWinPct: number;
  avgWin: number;
  avgLossAbs: number;
  profitFactor: number;
  bestDayPctTotal: number;

  mostActiveWeekday: string;
  mostProfitableWeekday: string;
  leastProfitableWeekday: string;

  totalTrades: number;
  totalContracts: number;

  avgTradeDur: number;
  avgWinDur: number;
  avgLossDur: number;

  longPct: number;
  shortPct: number;

  bestTradePnl: number;
  worstTradePnl: number;

  // extra risk
  maxDrawdown: number;
  maxIntradayDrawdown: number;
  avgDrawdown: number;
  maxDdLengthDays: number;
  avgRecoveryDays: number;

  // edge and consistency
  expectancy: number; // per trade
  profitPerDay: number;
  profitPerHour: number;
  greenWeekPct: number;
  consistencyDayPct: number; // days above a threshold

  // R and tails
  avgR: number;
  medianR: number;
  tailAvgWorst5PctPnl: number;
  tailAvgWorst5PctR: number;

  // quality
  avgMAE: number;
  avgMFE: number;
  givebackPct: number;
  breakevenRate: number;
  avgScratch: number;

  // costs
  totalGross: number;
  totalFees: number;
  totalSlippage: number;
  pnlPerContractNet: number;
  feePerContract: number;

  // behavior and rules
  dailyLossLimitHits: number;
  trailingDdHits: number;
  maxSizeViolations: number;
  tiltTrades: number;
  clusters: number;
  avgTradesPerDay: number;
  riskCreepContracts: number; // avg contracts after loss minus overall avg

  // data for UI
  equity: EquityPoint[];
  calendar: { date: string; pnl: number }[];

  durationBuckets: BucketRow[];
  rBuckets: { label: string; count: number }[];

  byTimeBlock: BreakdownRow[];
  byInstrument: BreakdownRow[];
  bySetup: BreakdownRow[];
  byRegime: BreakdownRow[];

  exitBreakdown: ExitRow[];
};
