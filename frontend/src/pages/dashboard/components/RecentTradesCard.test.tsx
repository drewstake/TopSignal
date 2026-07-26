// @vitest-environment jsdom

import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountTrade } from "../../../lib/types";
import { getNextRecentTradesVisibleCount, RECENT_TRADES_RENDER_PAGE_SIZE } from "../recentTradesPagination";
import { RecentTradesCard } from "./RecentTradesCard";

afterEach(cleanup);

function buildTrades(count: number): AccountTrade[] {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    account_id: 7,
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    side: index % 2 === 0 ? "BUY" : "SELL",
    size: 1,
    price: 21_500.25,
    timestamp: "2026-07-24T15:35:00Z",
    entry_time: "2026-07-24T15:30:00Z",
    exit_time: "2026-07-24T15:35:00Z",
    duration_minutes: 5,
    entry_price: 21_495.25,
    exit_price: 21_500.25,
    fees: 0.74,
    pnl: 10,
    order_id: `order-${index + 1}`,
    source_trade_id: `trade-${index + 1}`,
  }));
}

function RecentTradesHarness({ trades }: { trades: AccountTrade[] }) {
  const [visibleCount, setVisibleCount] = useState(RECENT_TRADES_RENDER_PAGE_SIZE);

  return (
    <RecentTradesCard
      trades={trades}
      loading={false}
      error={null}
      selectedTradeDate={null}
      selectedTradeDateLabel={null}
      visibleCount={visibleCount}
      recentTradeLimit={200}
      dayFilterTradeLimit={1_000}
      onClearDayFilter={() => undefined}
      onShowMore={() =>
        setVisibleCount((current) => getNextRecentTradesVisibleCount(current, trades.length))
      }
    />
  );
}

describe("RecentTradesCard incremental rendering", () => {
  it("bounds the initial DOM to 50 trade rows and reveals the next page on request", () => {
    render(<RecentTradesHarness trades={buildTrades(200)} />);

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(51);
    expect(screen.getByText("Showing 50 of 200 trades")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Show 50 more" }));

    expect(within(table).getAllByRole("row")).toHaveLength(101);
    expect(screen.getByText("Showing 100 of 200 trades")).toBeTruthy();
  });
});
