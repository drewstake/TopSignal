// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { CompactChartPoint } from "../compactDashboardData";
import {
  CompactContextStrip,
  CompactPerformanceChart,
  CompactScoreCard,
} from "./CompactDashboardPerformance";

afterEach(cleanup);

const points: CompactChartPoint[] = [
  { date: "2026-07-01", dailyPnl: 120, cumulativePnl: 120 },
  { date: "2026-07-02", dailyPnl: -50, cumulativePnl: 70 },
];

describe("Compact dashboard performance components", () => {
  it("supports a roving keyboard point readout and an accessible series toggle", () => {
    render(<CompactPerformanceChart points={points} loading={false} error={null} />);
    const lastPoint = screen.getByRole("img", { name: "Jul 2, +$70" });
    lastPoint.focus();
    fireEvent.keyDown(lastPoint, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(screen.getByRole("img", { name: "Jul 1, +$120" }));
    expect(screen.getByText((_text, element) => element?.tagName === "P" && element.textContent === "Selected: Jul 1 · +$120")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Daily" }));
    expect(screen.getByRole("img", { name: /Daily P&L chart/i })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Daily P&L data" })).toBeTruthy();
  });

  it("follows the latest point when data arrives after the loading state", () => {
    const { rerender } = render(<CompactPerformanceChart points={[]} loading error={null} />);

    rerender(<CompactPerformanceChart points={points} loading={false} error={null} />);

    expect(screen.getByText((_text, element) => (
      element?.tagName === "P"
      && element.textContent?.includes("Selected: Jul 2")
      && element.textContent.includes("+$70")
    ))).toBeTruthy();
    expect(screen.getByRole("img", { name: "Jul 2, +$70" }).getAttribute("tabindex")).toBe("0");
  });

  it("uses actual score components and allows a long trading scope to wrap", () => {
    const { rerender } = render(
      <CompactContextStrip
        rangeLabel="January 1, 2026 through December 31, 2026"
        context={{ tradingDayCount: 120, maxDrawdown: 500, riskBase: 50_000, riskBaseLabel: "Account balance" }}
        scoreBreakdown={{ label: "Healthy", riskScore: 84, consistencyScore: 72, edgeScore: 78, sampleSize: 120, sampleConfidence: 1 }}
        loading={false}
        error={null}
      />,
    );
    const scope = screen.getByText("January 1, 2026 through December 31, 2026");
    expect(scope.className).toContain("break-words");
    expect(scope.className).not.toContain("truncate");

    rerender(
      <CompactScoreCard
        score={81}
        breakdown={{ label: "Healthy", riskScore: 84, consistencyScore: 72, edgeScore: 78, sampleSize: 120, sampleConfidence: 1 }}
        loading={false}
        error={null}
      />,
    );
    expect(screen.getByRole("img", { name: /Risk 84, consistency 72, edge 78/i })).toBeTruthy();
  });
});
