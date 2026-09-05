import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BacktestDiagnostics } from "./BacktestDiagnostics";
import { backtestResultFixture } from "./backtestTestFixtures";

describe("BacktestDiagnostics", () => {
  it("keeps routine replay notes out of sample warnings", () => {
    const markup = renderToStaticMarkup(<BacktestDiagnostics result={{
      ...backtestResultFixture, warnings: [],
      notes: ["Warmup ready: 200 of 200", "Blocked 569 replay signal(s) due to max daily loss."],
    }} />);
    expect(markup).not.toContain("Sample-quality warnings");
    expect(markup).toContain("Replay details");
    expect(markup).toContain("Blocked 569");
    expect(markup).toContain("Warmup ready");
  });

  it("keeps older saved warnings visible when structured diagnostics are absent", () => {
    const markup = renderToStaticMarkup(<BacktestDiagnostics result={backtestResultFixture} />);
    expect(markup).toContain("Sample-quality warnings");
    expect(markup).toContain("Only one trade occurred");
    expect(markup).not.toContain("Candle coverage");
  });

  it("shows entry-hour impact, bounded examples and the uncertainty of missing bars", () => {
    const markup = renderToStaticMarkup(<BacktestDiagnostics result={{
      ...backtestResultFixture, warnings: ["Missing complete bars"],
      data_quality: {
        available_start: "2026-07-06T12:00:00Z", first_evaluation: "2026-07-06T14:00:00Z",
        warmup_available: 200, warmup_required: 200,
        gaps: {
          gap_count: 3, missing_bar_count: 12, in_session_gap_count: 1, in_session_missing_bar_count: 2,
          by_year: [{ year: 2026, gap_count: 3, missing_bar_count: 12, in_session_gap_count: 1 }],
          largest_gaps: [{ start: "2026-07-06T13:40:00Z", end: "2026-07-06T13:50:00Z", missing_bar_count: 2, in_session_missing_bar_count: 2 }],
        },
      },
    }} />);
    expect(markup).toContain("Sample-quality warnings");
    expect(markup).toContain("1 gaps during entry hours");
    expect(markup).toContain("12 expected complete bars");
    expect(markup).toContain("Gaps by year");
    expect(markup).toContain("9:40 AM");
    expect(markup).toContain("OHLCV alone cannot establish the cause");
    expect(markup).toContain("positions held overnight");
  });
});
