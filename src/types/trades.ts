export type PnlMode = "net" | "gross";
export type Direction = "Long" | "Short";
export type ExitType = "Target" | "Stop" | "Manual" | "Time" | "Breakeven";
export type TimeBlock = "Pre" | "Open" | "Mid" | "Close" | "After";
export type Regime = "Trend" | "Range" | "HighVol" | "LowVol";

export type Trade = {
  id: string;
  date: string; // YYYY-MM-DD
  instrument: string; // MNQ, MES, etc
  setup: string; // Pullback, Breakout, etc
  direction: Direction;
  contracts: number;

  plannedRisk: number; // dollars at stop
  grossPnl: number;
  fees: number;
  netPnl: number;

  durationMin: number;
  entryTime: string; // HH:MM
  exitTime: string; // HH:MM
  timeBlock: TimeBlock;

  win: boolean;

  mae: number; // max adverse excursion ($)
  mfe: number; // max favorable excursion ($)
  giveback: number; // amount given back from MFE ($)

  exitType: ExitType;

  // optional but useful
  slippage: number; // estimate in $ (0 if unknown)
  regime: Regime;
};

export type RuleConfig = {
  dailyLossLimit: number;       // dollars
  trailingDdLimit: number;      // dollars
  maxContracts: number;         // max contracts allowed
  tiltDdTrigger: number;        // dollars intraday, when hit we count "tilt trades"
  clusterWindowMin: number;     // minutes
  clusterTradeCount: number;    // trades in window to call it a cluster
};

export function defaultRules(): RuleConfig {
  return {
    dailyLossLimit: 2000,
    trailingDdLimit: 2500,
    maxContracts: 3,
    tiltDdTrigger: 800,
    clusterWindowMin: 20,
    clusterTradeCount: 5,
  };
}
