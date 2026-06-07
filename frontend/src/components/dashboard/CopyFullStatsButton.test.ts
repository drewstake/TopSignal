import { describe, expect, it } from "vitest";

import { buildFullStatsText, buildStatsCoachSummary, type CopyFullStatsMetrics, NO_DATA_TEXT } from "./CopyFullStatsButton";

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
    averagePositionSize: 8.6,
    medianPositionSize: 8,
    tradeCountUsedForSizingStats: 21,
    avgPointGain: 31.76,
    avgPointLoss: 14.87,
    pointsBasisUsed: "MNQ",
    sizingBenchmark: {
      benchmarkMode: "fixed_average_size",
      benchmarkSizeUsed: 8.6,
      benchmarkGrossPnl: 5120,
      benchmarkNetPnl: 4980.4,
      benchmarkDiff: 477.9,
      benchmarkRatio: 1.0959,
      benchmarkLabel: "In Line With Benchmark",
    },
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
      detail: "Current balance is used for the risk base.",
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

function makeMetrics(
  overrides: {
    summary?: Partial<CopyFullStatsMetrics["summary"]>;
    performance?: Partial<CopyFullStatsMetrics["performance"]>;
    consistency?: Partial<CopyFullStatsMetrics["consistency"]>;
    risk?: Partial<CopyFullStatsMetrics["risk"]>;
    direction?: Partial<CopyFullStatsMetrics["direction"]>;
    payoff?: Partial<CopyFullStatsMetrics["payoff"]>;
    activity?: Partial<CopyFullStatsMetrics["activity"]>;
    sustainability?: Partial<CopyFullStatsMetrics["sustainability"]>;
  } = {},
): CopyFullStatsMetrics {
  return {
    ...sampleMetrics,
    summary: { ...sampleMetrics.summary, ...overrides.summary },
    performance: { ...sampleMetrics.performance, ...overrides.performance },
    consistency: { ...sampleMetrics.consistency, ...overrides.consistency },
    risk: { ...sampleMetrics.risk, ...overrides.risk },
    direction: { ...sampleMetrics.direction, ...overrides.direction },
    payoff: {
      ...sampleMetrics.payoff,
      ...overrides.payoff,
      pointPayoffByBasis: {
        ...sampleMetrics.payoff.pointPayoffByBasis,
        ...(overrides.payoff?.pointPayoffByBasis ?? {}),
      },
    },
    activity: { ...sampleMetrics.activity, ...overrides.activity },
    sustainability: {
      ...sampleMetrics.sustainability,
      ...overrides.sustainability,
      debug: {
        ...sampleMetrics.sustainability.debug,
        ...(overrides.sustainability?.debug ?? {}),
      },
    },
  };
}

function getSection(summary: ReturnType<typeof buildStatsCoachSummary>, title: string) {
  return summary.sections.find((section) => section.title === title)?.items ?? [];
}

describe("buildFullStatsText", () => {
  it("builds the full dashboard stats block with safe copy formatting", () => {
    const text = buildFullStatsText({
      metrics: sampleMetrics,
      rangeLabel: "Mar 3 to Mar 5, 2026",
      calendarDays: sampleCalendarDays,
      generatedAt: new Date("2026-05-13T14:42:00.000Z"),
    });

    expect(text).toContain("TopSignal Full Stats (Mar 3 to Mar 5, 2026)");
    expect(text).toContain("- Generated: May 13, 2026, 10:42 AM ET");
    expect(text).toContain("- Range: Mar 3 to Mar 5, 2026");
    expect(text).toContain("- Sample: 21 trades across 3 active days");
    expect(text).toContain("- Basis: Net PnL after fees unless marked Gross");
    expect(text).toContain(
      "- Sample Warning: Only 21 trades across 3 active days. Win rate, profit factor, sustainability, and loss percentile metrics may be unstable.",
    );
    expect(text).toContain("- Net PnL (after fees): +$5,458.30");
    expect(text).toContain("- Long: Trades 10 | WR 70.0% | Expectancy +$109.50");
    expect(text).toContain("- Mar 5: +$1,396.20 (8 trades)");
    expect(text).toContain("- Risk Base Definition: Current balance: Current balance is used for the risk base.");
    expect(text).toContain("- P95 Loss: N/A - requires at least 20 losses; current losses: 4");
    expect(text).toContain("- Capture: N/A - capture data not available");
    expect(text).toContain("- Projected Trades / Week: 49.0, based on 3-day pace");
    expect(text).toContain("- Projected Active Days / Week: 7.0, based on 3-day pace");
    expect(text).toContain("- Trades / Active Hour: N/A - active trading time not available");
    expect(text).toContain("- Risk Quality: 0.0/100 (higher is better)");
    expect(text).not.toContain("\u00e2\u20ac\u00a2");
    expect(text).not.toContain("\u2022");
  });

  it("labels all-green ranges by lowest day instead of worst day", () => {
    const text = buildFullStatsText({
      metrics: sampleMetrics,
      rangeLabel: "Mar 3 to Mar 5, 2026",
      calendarDays: sampleCalendarDays,
    });

    expect(text).toContain("- Lowest Day: +$1,396.20 (25.6%)");
    expect(text).toContain("- Lowest Day Impact: Lowest Day = 0.8 days of avg profit");
    expect(text).not.toContain("Worst Day: +$1,396.20");
  });

  it("explains unavailable direction metrics when one side has no trades", () => {
    const text = buildFullStatsText({
      metrics: makeMetrics({
        summary: {
          trade_count: 10,
          loss_count: 4,
          active_days: 2,
        },
        direction: {
          longPercent: metric(0),
          shortPercent: metric(100),
          longTrades: metric(0),
          shortTrades: metric(10),
          longPnl: metric(0),
          shortPnl: metric(1538),
          longPnlShare: metric(0),
          shortPnlShare: metric(100),
          longWinRate: metric(null),
          shortWinRate: metric(60),
          longExpectancy: metric(null),
          shortExpectancy: metric(153.8),
          longProfitFactor: metric(null),
          shortProfitFactor: metric(5.82),
          longAvgWin: metric(null),
          longAvgLoss: metric(null),
          shortAvgWin: metric(309.5),
          shortAvgLoss: metric(-79.75),
          longLargeLossRate: metric(null),
          shortLargeLossRate: metric(0),
          insight: "",
        },
        activity: {
          rangeDays: 2,
          tradesPerWeek: 35,
          activeDaysPerWeek: 7,
          tradesPerActiveHour: null,
        },
      }),
      rangeLabel: "May 11 to May 12, 2026",
      calendarDays: [],
      generatedAt: new Date("2026-05-13T14:42:00.000Z"),
    });

    expect(text).toContain("- Sample: 10 trades across 2 active days");
    expect(text).toContain("- Insight: Only short trades taken; no long/short comparison available.");
    expect(text).toContain("Expectancy N/A - no long trades in range");
    expect(text).toContain("- Projected Trades / Week: 35.0, based on 2-day pace");
    expect(text).toContain("- Projected Active Days / Week: 7.0, based on 2-day pace");
  });

  it("returns the empty-range fallback when there are no trades", () => {
    expect(
      buildFullStatsText({
        metrics: makeMetrics({
          summary: {
            trade_count: 0,
          },
        }),
        rangeLabel: "selected range",
        calendarDays: [],
      }),
    ).toBe(NO_DATA_TEXT);
  });
});

describe("buildStatsCoachSummary", () => {
  it("uses Scaling Candidate for strong profitable ranges", () => {
    const metrics = makeMetrics({
      summary: {
        trade_count: 80,
        active_days: 12,
        avg_trades_per_day: 4,
        profit_factor: 2.2,
        avg_win: 450,
        avg_loss: -300,
        win_rate: 62,
      },
      performance: {
        netPnl: metric(8000),
        profitPerDay: metric(666.67),
        expectancyPerTrade: metric(100),
      },
      consistency: {
        worstDay: metric(-450),
        worstDayPercentOfNet: metric(5.6),
        redDayPercent: metric(25),
        stabilityScore: metric(88),
        insight: "Daily PnL is stable.",
      },
      risk: {
        maxDrawdown: metric(960),
        drawdownPercentOfNet: metric(12),
        drawdownPercentOfEquityBase: metric(1.2),
      },
      direction: {
        longPnlShare: metric(49),
        shortPnlShare: metric(51),
        longExpectancy: metric(95),
        shortExpectancy: metric(105),
        insight: "Direction mix is balanced.",
      },
      payoff: {
        winLossRatio: metric(1.5),
        averageWin: metric(450),
        averageLoss: metric(-300),
        largeLossRate: metric(2),
      },
      activity: {
        tradesPerWeek: 20,
        activeDaysPerWeek: 3.5,
      },
      sustainability: {
        score: 84,
        label: "Healthy",
        riskScore: 82,
        consistencyScore: 86,
        edgeScore: 90,
      },
    });

    const summary = buildStatsCoachSummary({ metrics, rangeLabel: "Last 30 days" });

    expect(summary.verdict).toContain("Scaling Candidate");
    expect(summary.confidence).toEqual({
      label: "High confidence sample",
      detail: "High confidence sample - 80 trades / 12 active days / 0 missing key metrics",
      tone: "positive",
    });
    expect(summary.keyStats).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "Net PnL", value: "+$8,000.00", tone: "positive" }),
        expect.objectContaining({ label: "Trades / Days", value: "80 / 12", tone: "positive" }),
      ]),
    );
    expect(summary.sections[0]).toEqual(expect.objectContaining({ title: "Top 3 Levers" }));
    expect(summary.sections[0].items).toHaveLength(3);
    expect(summary.topLevers).toHaveLength(3);
  });

  it("uses Profitable But Fragile for profitable ranges with weak risk", () => {
    const metrics = makeMetrics({
      summary: {
        trade_count: 45,
        active_days: 6,
      },
      sustainability: {
        score: 55,
        label: "Unstable",
        riskScore: 45,
      },
    });

    const summary = buildStatsCoachSummary({ metrics, rangeLabel: "Last 2 weeks" });

    expect(summary.verdict).toContain("Profitable But Fragile");
    expect(summary.confidence).toEqual({
      label: "Medium confidence sample",
      detail: "Medium confidence sample - 45 trades / 6 active days / 0 missing key metrics",
      tone: "neutral",
    });
    expect(summary.topLevers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ issue: "Loss sizing", metric: "avg loss -$440.80 vs avg win +$424.79" }),
        expect.objectContaining({ issue: "Drawdown pressure" }),
      ]),
    );
    expect(getSection(summary, "Improvements")).toEqual(
      expect.arrayContaining([
        expect.stringContaining("Trade frequency: current pace projects to 49.0 trades/week"),
        expect.stringContaining("require at least 4 of 5 A-setup checklist items before entry"),
      ]),
    );
  });

  it("uses Defensive Mode for losing ranges", () => {
    const metrics = makeMetrics({
      summary: {
        trade_count: 60,
        active_days: 11,
        net_pnl: -1200,
        realized_pnl: -1200,
        gross_pnl: -1000,
        profit_factor: 0.75,
        win_rate: 42,
      },
      performance: {
        netPnl: metric(-1200),
        profitPerDay: metric(-109.09),
        expectancyPerTrade: metric(-20),
      },
      risk: {
        drawdownPercentOfNet: metric(null),
        drawdownPercentOfEquityBase: metric(2.1),
      },
      activity: {
        activeDaysPerWeek: 4,
        tradesPerWeek: 24,
      },
      sustainability: {
        score: 61,
        label: "Mostly healthy",
        riskScore: 72,
      },
    });

    const summary = buildStatsCoachSummary({ metrics, rangeLabel: "April" });

    expect(summary.verdict).toContain("Defensive Mode");
    expect(summary.keyStats).toEqual(expect.arrayContaining([expect.objectContaining({ label: "Net PnL", value: "-$1,200.00", tone: "negative" })]));
    expect(summary.sections[0].items[0]).toContain("Negative edge");
  });

  it("uses Insufficient Sample and low confidence for small profitable samples", () => {
    const summary = buildStatsCoachSummary({
      metrics: makeMetrics({
        summary: {
          trade_count: 12,
          active_days: 3,
          avg_trades_per_day: 4,
          profit_factor: 2.1,
          avg_win: 500,
          avg_loss: -250,
          win_rate: 66,
        },
        performance: {
          netPnl: metric(1800),
          profitPerDay: metric(600),
          expectancyPerTrade: metric(150),
        },
        consistency: {
          worstDay: metric(-180),
          worstDayPercentOfNet: metric(10),
          redDayPercent: metric(33),
          stabilityScore: metric(82),
        },
        risk: {
          maxDrawdown: metric(300),
          drawdownPercentOfNet: metric(16.7),
          drawdownPercentOfEquityBase: metric(0.6),
        },
        direction: {
          longPnlShare: metric(52),
          shortPnlShare: metric(48),
          longExpectancy: metric(145),
          shortExpectancy: metric(155),
          insight: "Direction mix is balanced.",
        },
        payoff: {
          winLossRatio: metric(2),
          averageWin: metric(500),
          averageLoss: metric(-250),
          largeLossRate: metric(0),
        },
        activity: {
          tradesPerWeek: 18,
          activeDaysPerWeek: 3,
        },
        sustainability: {
          score: 76,
          label: "Mostly healthy",
          riskScore: 80,
        },
      }),
      rangeLabel: "Three sessions",
    });

    expect(summary.verdict).toContain("Insufficient Sample");
    expect(summary.confidence).toEqual({
      label: "Low confidence sample",
      detail: "Low confidence sample - 12 trades / 3 active days / 0 missing key metrics",
      tone: "negative",
    });
    expect(getSection(summary, "Main Risks")).toContain(
      "Low confidence sample - 12 trades / 3 active days / 0 missing key metrics. Treat positive stats as directional, not proven, until the sample reaches at least 30 trades and 5 active days.",
    );
  });

  it("names missing key metrics in the sample quality detail", () => {
    const summary = buildStatsCoachSummary({
      metrics: makeMetrics({
        summary: {
          trade_count: 30,
          active_days: 5,
        },
        direction: {
          longExpectancy: metric(null),
        },
      }),
      rangeLabel: "Five sessions",
    });

    expect(summary.confidence.detail).toBe("Medium confidence sample - 30 trades / 5 active days / 1 missing key metric: long expectancy");
  });

  it("puts underperforming direction insights in the risk section", () => {
    const summary = buildStatsCoachSummary({
      metrics: sampleMetrics,
      rangeLabel: "Mar 3 to Mar 5, 2026",
      calendarDays: sampleCalendarDays,
    });

    expect(summary.topLevers).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          issue: "Direction imbalance",
          metric: "long expectancy +$109.50 vs short expectancy +$410.45",
          action: "keep long size reduced or require stricter confirmation until long expectancy improves over at least 10 more trades.",
        }),
      ]),
    );
    expect(getSection(summary, "Main Risks")).toContain("Shorts outperform longs on expectancy.");
    expect(getSection(summary, "What You're Doing Right")).not.toContain("Shorts outperform longs on expectancy.");
  });

  it("does not tell traders to make an already-positive weaker side positive", () => {
    const summary = buildStatsCoachSummary({
      metrics: makeMetrics({
        summary: {
          trade_count: 53,
          active_days: 16,
          avg_trades_per_day: 3.3,
          profit_factor: 1.98,
          win_rate: 66,
        },
        performance: {
          netPnl: metric(5180.49),
          expectancyPerTrade: metric(97.75),
        },
        direction: {
          longPnlShare: metric(9.4),
          shortPnlShare: metric(90.6),
          longExpectancy: metric(30.43),
          shortExpectancy: metric(138.13),
          insight: "",
        },
        activity: {
          tradesPerWeek: 23.2,
          activeDaysPerWeek: 5,
        },
      }),
      rangeLabel: "May 15 to Jun 5, 2026",
    });

    expect(getSection(summary, "Next 10 Trading Days Plan").join("\n")).toContain(
      "PnL concentration: keep longs at reduced size until expectancy improves across at least 10 more trades and loss size stays controlled.",
    );
    expect(getSection(summary, "Next 10 Trading Days Plan").join("\n")).not.toContain(
      "keep longs at 50% size until expectancy is positive across at least 10 trades.",
    );
    expect(getSection(summary, "Main Risks")).toContain(
      "Long side is profitable but materially weaker: long expectancy is +$30.43 versus short expectancy at +$138.13.",
    );
  });

  it("returns the empty-range fallback when there are no trades", () => {
    expect(
      buildStatsCoachSummary({
        metrics: makeMetrics({
          summary: {
            trade_count: 0,
          },
        }),
        rangeLabel: "selected range",
        calendarDays: [],
      }),
    ).toEqual({
      verdict: NO_DATA_TEXT,
      confidence: {
        label: "No meaningful sample yet",
        detail: "No meaningful sample yet - 0 trades / 0 active days / 0 missing key metrics",
        tone: "negative",
      },
      keyStats: [],
      topLevers: [],
      sections: [],
    });
  });
});
