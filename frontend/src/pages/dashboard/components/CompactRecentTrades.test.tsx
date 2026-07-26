// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AccountTrade } from "../../../lib/types";
import { CompactRecentTrades } from "./CompactRecentTrades";

afterEach(cleanup);

const trades = [
  { id: 1, account_id: 10, contract_id: "MNQU6", symbol: "MNQ", side: "SELL", size: 1, price: 1, timestamp: "2026-07-02T15:00:00Z", exit_time: "2026-07-02T15:00:00Z", fees: 0, pnl: 25, order_id: "1", source_trade_id: "1" },
  { id: 1, account_id: 11, contract_id: "MESU6", symbol: "MES", side: "BUY", size: 1, price: 1, timestamp: "2026-07-01T15:00:00Z", exit_time: "2026-07-01T15:00:00Z", fees: 0, pnl: -10, order_id: "2", source_trade_id: "2" },
] as AccountTrade[];

describe("CompactRecentTrades", () => {
  it("maps provider closing actions and attributes duplicate trade IDs by account", () => {
    render(
      <CompactRecentTrades
        trades={trades}
        loading={false}
        error={null}
        accountNameById={{ 10: "Primary", 11: "Follower" }}
      />,
    );
    expect(screen.getAllByText("LONG").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SHORT").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Primary").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Follower").length).toBeGreaterThan(0);
    const pnl = screen.getAllByText("+$25")[0];
    expect(pnl.className).toContain("whitespace-nowrap");
    expect(screen.getByRole("region", { name: "Recent Trades" }).className).toContain("h-full");
  });
});
