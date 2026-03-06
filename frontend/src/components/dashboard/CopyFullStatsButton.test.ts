import { describe, expect, it } from "vitest";

import { buildFullStatsText, type CopyFullStatsMetrics, NO_DATA_TEXT } from "./CopyFullStatsButton";

function metric(value: number | null) {
  return { value };
}

const sampleMetrics: CopyFullStatsMetrics = {
  summary: {
    realized_pnl: 5458.3,
    gross_pnl: 5610,
    fees: 151.7,
    net_pnl: 5458.3,
    win_rate: 81,
    win_count: 17,
    loss_count: 4,
    breakeven_count: 0,
    profit_factor: 4.26,
    avg_win: 424.79,
    avg_loss: -440.8,
    avg_win_duration_minutes: 4.1,
    avg_loss_duration_minutes: 3 + 22 / 60,
    expectancy_per_trade: 259.92,
    tail_risk_5pct: 0,
    max_drawdown: 1664.4,
    average_drawdown: 853.4,
    risk_drawdown_score: 0,
    max_drawdown_length_hours: 0.1,
    recovery_time_hours: 0,
    average_recovery_length_hours: 0,
    trade_count: 21,
    half_turn_count: 0,
    execution_count: 0,
    day_win_rate: 0,
    green_days: 3,
    red_days: 0,
    flat_days: 0,
    avg_trades_per_day: 7,
    active_days: 3,
    efficiency_per_hour: 4967.04,
    profit_per_day: 1819.43,
    avgPointGain: 31.76,
    avgPointLoss: 14.87,
    pointsBasisUsed: "MNQ",
  },
  performance: {
    netPnl: metric(5458.3),
    profitPerDay: metric(1819.43),
    efficiencyPerHour: metric(4967.04),
    expectancyPerTrade: metric(259.92),
  },
  consistency: {
    dailyPnlVolatility: metric(330.15),
    bestDay: metric(2201.8),
    worstDay: metric(1396.2),
    bestDayPercentOfNet: metric(40.3),
    worstDayPercentOfNet: metric(25.6),
    medianDayPnl: metric(1860.3),
    avgGreenDay: metric(1819.43),
    avgRedDay: metric(null),
    redDayPercent: metric(0),
    worstDayImpact: metric(0.8),
    greenRedDaySizeRatio: metric(null),
    stabilityScore: metric(74),
    insight: "Stable across green days.",
  },
  risk: {
    maxDrawdown: metric(1664.4),
    drawdownPercentOfNet: metric(30.49),
    drawdownPercentOfEquityBase: metric(3.3288),
    equityBase: {
      value: 50000,
      label: "Current balance",
      detail: "Current balance is used for the equity base.",
    },
    averageDrawdown: metric(853.4),
    maxDrawdownLengthHours: 0.1,
    recoveryTimeHours: 0,
  },
  direction: {
    longPercent: metric(48),
    shortPercent: metric(52),
    longTrades: metric(10),
    shortTrades: metric(11),
    longPnl: metric(1095),
    shortPnl: metric(4515),
    longPnlShare: metric(19.5),
    shortPnlShare: metric(80.5),
    longWinRate: metric(70),
    shortWinRate: metric(90.9),
    longExpectancy: metric(109.5),
    shortExpectancy: metric(410.45),
    longProfitFactor: metric(1.67),
    shortProfitFactor: metric(51.17),
    longAvgWin: metric(389.64),
    longAvgLoss: metric(-544.17),
    shortAvgWin: metric(460.5),
    shortAvgLoss: metric(-90),
    longLargeLossRate: metric(0),
    shortLargeLossRate: metric(0),
    insight: "Shorts outperform longs on expectancy.",
  },
  payoff: {
    winLossRatio: metric(0.96),
    averageWin: metric(424.79),
    averageLoss: metric(-440.8),
    breakevenWinRate: metric(50.9),
    currentWinRate: metric(81),
    wrCushion: metric(30),
    largeLossThreshold: metric(881.6),
    largeLossRate: metric(0),
    p95Loss: metric(null),
    capture: metric(null),
    pointPayoffByBasis: {
      MNQ: { avgPointGain: 31.76, avgPointLoss: 14.87 },
      MES: { avgPointGain: null, avgPointLoss: null },
      MGC: { avgPointGain: null, avgPointLoss: null },
      SIL: { avgPointGain: null, avgPointLoss: null },
    },
    insight: "Payoff is close to balanced.",
  },
  activity: {
    medianTradesPerDay: 7,
    maxTradesInDay: 8,
    tradesPerWeek: 49,
    activeDaysPerWeek: 7,
    tradesPerActiveHour: null,
    rangeDays: 3,
  },
  sustainability: {
    score: 49,
    label: "Unstable",
    riskScore: 0,
    consistencyScore: 72.7,
    edgeScore: 100,
    debug: {
      nDays: 3,
      avgDay: 1819.43,
      vol: 330.15,
      posSum: 5458.3,
      negSum: 0,
      profitFactor: 100,
      concentration: 1,
      effectiveEquityBase: 50000,
      peakEquityFallback: 5458.3,
      maxDDPct: 0.033288,
      worstDayPct: 0.0256,
      swingRatio: 0.18,
      swingScore: 100,
      concScore: 9,
      confidence: 0.3,
      rawScore: 48.9,
    },
  },
  holdTime: {
    ratio: metric(1.22),
    averageWinDurationMinutes: 4.1,
    averageLossDurationMinutes: 3 + 22 / 60,
  },
  balance: {
    currentBalance: 5458.3,
  },
};

const sampleCalendarDays = [
  { date: "2026-03-03", trade_count: 7, gross_pnl: 0, fees: 0, net_pnl: 2201.8 },
  { date: "2026-03-04", trade_count: 6, gross_pnl: 0, fees: 0, net_pnl: 1860.3 },
  { date: "2026-03-05", trade_count: 8, gross_pnl: 0, fees: 0, net_pnl: 1396.2 },
];

describe("buildFullStatsText", () => {
  it("builds the full dashboard stats block", () => {
    expect(
      buildFullStatsText({
        metrics: sampleMetrics,
        rangeLabel: "Mar 3 to Mar 5, 2026",
        calendarDays: sampleCalendarDays,
      }),
    ).toBe(`TopSignal Full Stats (Mar 3 to Mar 5, 2026)

PERFORMANCE
• Net PnL (after fees): +$5,458.30
• Gross PnL: +$5,610.00
• Fees: $151.70
• Trades: 21
• Win Rate: 81.0%
• Profit Factor: 4.26x
• Profit / Day: +$1,819.43
• Efficiency / Hour: +$4,967.04
• Edge (Expectancy): +$259.92 per trade
• Outcome Mix: 17W / 4L / 0 BE

CONSISTENCY
• Swing (daily PnL volatility): $330.15
• Best Day: +$2,201.80 (40.3%)
• Worst Day: +$1,396.20 (25.6%)
• Median Day: +$1,860.30
• Avg Green: +$1,819.43
• Avg Red: N/A
• Red Day %: 0.0%
• Worst Day Impact: Worst Day = 0.8 days of avg profit
• G/R Size Ratio: N/A
• Stability: 74.0%

RISK
• Max Drawdown: -$1,664.40
• DD % of Net PnL: 30.5%
• Max DD % of Equity Base: 3.3%
• Equity Base: $50,000.00
• Equity Base Basis: Current balance
• Avg Drawdown: -$853.40
• DD Length: 0.1 h
• Recovery: 0.0 h

DIRECTION
• Long %: 48.0%
• Short %: 52.0%
• PnL Share: Long +$1,095.00 (19.5%) | Short +$4,515.00 (80.5%)
• Insight: Shorts outperform longs on expectancy.

Direction Breakdown
• Long: Trades 10 | WR 70.0% | Expectancy +$109.50 | PF 1.67x | Avg W/L +$389.64 / -$544.17 | Large Loss % 0.0%
• Short: Trades 11 | WR 90.9% | Expectancy +$410.45 | PF 51.17x | Avg W/L +$460.50 / -$90.00 | Large Loss % 0.0%

PAYOFF
• W/L Ratio: 0.96x
• Avg Win: +$424.79
• Avg Loss: -$440.80
• Breakeven WR: 50.9%
• Current WR: 81.0%
• WR Cushion: +30.0 pts
• Large Loss Rate: 0.0% (<= -$881.60)
• P95 Loss: N/A
• Capture: N/A

Points Payoff By Basis
• MNQ: Avg Point Gain 31.76 pts | Avg Point Loss 14.87 pts
• MES: Avg Point Gain N/A | Avg Point Loss N/A

ACTIVITY
• Active Days: 3
• Avg Trades / Day: 7.0
• Median / Day: 7.0
• Max / Day: 8
• Trades / Week: 49.0
• Days / Week: 7.0
• Trades / Active Hour: N/A

SUSTAINABILITY
• Score: 49/100 (Unstable)
• Risk: 0.0/100
• Consistency: 72.7/100
• Edge: 100.0/100

HOLD TIME
• Hold Time Ratio (win/loss): 1.22x
• Avg Win Duration: 4m 6s
• Avg Loss Duration: 3m 22s

DAILY BALANCE
• Start Balance: $2,201.80
• Ending Balance: $5,458.30
• High: $5,458.30
• Low: $2,201.80
• Largest Day: +$2,201.80

PNL CALENDAR (Mar 3 to Mar 5, 2026)
• Mar 3: +$2,201.80 (7 trades)
• Mar 4: +$1,860.30 (6 trades)
• Mar 5: +$1,396.20 (8 trades)`);
  });

  it("returns the empty-range fallback when there are no trades", () => {
    expect(
      buildFullStatsText({
        metrics: {
          ...sampleMetrics,
          summary: {
            ...sampleMetrics.summary,
            trade_count: 0,
          },
        },
        rangeLabel: "selected range",
        calendarDays: [],
      }),
    ).toBe(NO_DATA_TEXT);
  });
});
