import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { backtestResultFixture as result } from "./backtestTestFixtures";
import { BacktestResults, BacktestRunningState, BotBacktestPanel } from "./BotBacktestPanel";

describe("BacktestResults", () => {
  it("renders metrics, assumptions, warnings, chart, and trade ledger", () => {
    const markup = renderToStaticMarkup(<BacktestResults result={result} />);

    expect(markup).toContain("Net P&amp;L");
    expect(markup).toContain("$47.60");
    expect(markup).toContain("Sample-quality warnings");
    expect(markup).toContain("Only one trade occurred");
    expect(markup).toContain("Backtest equity and drawdown chart");
    expect(markup).toContain("MNQ (CON.F.US.MNQ.H26)");
    expect(markup).toContain("5-minute");
    expect(markup).toContain("500 closed bars");
    expect(markup).toContain("Jan 2, 2026");
    expect(markup).toContain("Jan 30, 2026");
    expect(markup).toContain("next bar open");
    expect(markup).toContain("External routing disabled");
    expect(markup).toContain("Trade ledger");
    expect(markup).toContain("take profit");
    expect(markup).toContain("Chronological holdout diagnostic (not strategy validation)");
    expect(markup).toContain("Final 20% (holdout)");
    expect(markup).toContain("diagnostic only");
    expect(markup).toContain("$1.20 / contract / side ($2.40 round trip)");
  });
});

describe("BotBacktestPanel", () => {
  it("offers TopBot Adaptive replay without a strategy picker or date inputs", () => {
    const markup = renderToStaticMarkup(<BotBacktestPanel bot={null} />);

    expect(markup).toContain("Run Backtest");
    expect(markup).not.toContain("Full History");
    expect(markup).not.toContain("Backtest strategy");
    expect(markup).not.toContain("<select");
    expect(markup).toContain("TopBot Adaptive · MNQ 5m · EMA/VWAP pullback");
    expect(markup).not.toContain("E-mini S&amp;P 500");
    expect(markup).not.toContain("SMA Cross");
    expect(markup).toContain("No order routing");
    expect(markup).not.toContain("Start date");
    expect(markup).not.toContain("End date");
    expect(markup).not.toContain('type="date"');
    expect(markup).toContain("Fees / contract / side");
    expect(markup).toContain('value="0.61"');
  });

  it("explains and disables server replay jobs in Demo Mode", () => {
    const markup = renderToStaticMarkup(<BotBacktestPanel bot={null} demoMode />);

    expect(markup).toContain("Demo snapshot");
    expect(markup).toContain("Demo Mode does not start a server replay job");
    expect(markup).toContain('disabled=""');
  });

  it("shows the replay percent and amount remaining", () => {
    const markup = renderToStaticMarkup(
      <BacktestRunningState
        progress={{
          phase: "replaying",
          completed: 610,
          total: 1_000,
          percent: 61,
          remaining_percent: 39,
        }}
      />,
    );

    expect(markup).toContain("Replaying closed candles — 61%");
    expect(markup).toContain("39% remaining");
    expect(markup).toContain('role="progressbar"');
    expect(markup).toContain('aria-valuenow="61"');
    expect(markup).not.toContain("Preparing and replaying the full closed-candle history");
  });
});
