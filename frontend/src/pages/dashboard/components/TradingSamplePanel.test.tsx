// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { accountsApi } from "../../../lib/api";
import type { AccountInfo, AccountTrade } from "../../../lib/types";
import { TradingSamplePanel } from "./TradingSamplePanel";

const accounts = [1, 2, 3, 4, 5].map(id => ({
  id, name: `Combine ${id}`, provider_name: `250KTC-LABS002-V2-DLL-test-${id}`,
  account_state: "ACTIVE", trade_data_source: "projectx", balance: 251197.56,
} as AccountInfo));
const close: AccountTrade = {
  id: 1, account_id: 1, side: "SELL", symbol: "MNQ", contract_id: "MNQ.Z26", size: 2,
  price: 25785, timestamp: "2026-11-25T17:55:42.326Z", pnl: 1200, fees: 2.44,
  order_id: "1", source_trade_id: null,
};
const props = { account: accounts[0], accounts, copyIds: [], tradeCount: 1, activeDays: 1,
  singleTrade: close, loading: false, error: null };

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows one decision across five verified closes and progress per Freedom combine", async () => {
  vi.spyOn(accountsApi, "getTrades").mockImplementation(async id => [{ ...close, account_id: id }]);
  render(<TradingSamplePanel {...props} />);
  expect(await screen.findByText("1 trade across 5 accounts")).toBeTruthy();
  expect(screen.getByText(/\+\$5,987.80 net on matched accounts/)).toBeTruthy();
  expect(screen.getAllByText("7.98%")).toHaveLength(5);
  expect(screen.getAllByText("$13,802.44")).toHaveLength(5);
  expect(screen.getByText(/\$10,000.00 end-of-day trailing loss limit/)).toBeTruthy();
  expect(screen.getByText(/grades begin at 5 closed trades across 2 trading days/)).toBeTruthy();
});

it("excludes unmatched, ambiguous, unselected and unreadable fills", async () => {
  const fetch = vi.spyOn(accountsApi, "getTrades").mockImplementation(async id => {
    if (id === 3) throw Error("unavailable");
    if (id === 4) return [close, close].map(trade => ({ ...trade, account_id: id }));
    return [{ ...close, account_id: id, size: 5 }];
  });
  render(<TradingSamplePanel {...props} copyIds={[1, 2, 3, 4]} />);
  expect(await screen.findByText("1 matched account")).toBeTruthy();
  expect(screen.getByText(/Some account matches could not be verified/)).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(3);
});
