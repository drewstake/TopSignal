import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DailyAccountBalanceCard } from "./DailyAccountBalanceCard";

const days = [
  { date: "2026-06-01", net_pnl: 100, gross_pnl: 110, fees: 10, trade_count: 1 },
  { date: "2026-06-02", net_pnl: -25, gross_pnl: -20, fees: 5, trade_count: 1 },
];

describe("DailyAccountBalanceCard", () => {
  it("labels a current-balance-anchored curve as an estimate", () => {
    const markup = renderToStaticMarkup(
      <DailyAccountBalanceCard days={days} loading={false} error={null} currentBalance={50_075} />,
    );

    expect(markup).toContain("Estimated Balance Path");
    expect(markup).toContain("not historical broker closing balances");
    expect(markup).toContain("historical closes are unavailable");
  });

  it("labels an unanchored curve as cumulative net P&amp;L", () => {
    const markup = renderToStaticMarkup(
      <DailyAccountBalanceCard days={days} loading={false} error={null} currentBalance={null} />,
    );

    expect(markup).toContain("Cumulative Net P&amp;L");
    expect(markup).toContain("Starts at $0");
  });
});
