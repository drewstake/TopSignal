import { describe, expect, it, vi } from "vitest";

const { getSelectableAccountsMock, getSelectableAccountsLocalFirstMock, refreshTradesMock } = vi.hoisted(() => ({
  getSelectableAccountsMock: vi.fn(),
  getSelectableAccountsLocalFirstMock: vi.fn(),
  refreshTradesMock: vi.fn(),
}));

vi.mock("./api", () => ({
  accountsApi: {
    getSelectableAccounts: getSelectableAccountsMock,
    getSelectableAccountsLocalFirst: getSelectableAccountsLocalFirstMock,
    refreshTrades: refreshTradesMock,
  },
}));

import { getSelectableAccounts, getSelectableAccountsLocalFirst, refreshTrades } from "./appShellApi";

describe("appShellApi", () => {
  it("delegates account loading to the shared accounts api", async () => {
    const accounts = [{ id: 1, name: "Main", account_state: "ACTIVE" }];
    getSelectableAccountsMock.mockResolvedValueOnce(accounts);

    await expect(getSelectableAccounts({ refreshProvider: false })).resolves.toBe(accounts);
    expect(getSelectableAccountsMock).toHaveBeenCalledWith({ refreshProvider: false });
  });

  it("delegates local-first startup account loading", async () => {
    const accounts = [{ id: 1, name: "Live", account_state: "ACTIVE", trade_data_source: "csv_import" }];
    getSelectableAccountsLocalFirstMock.mockResolvedValueOnce(accounts);

    await expect(getSelectableAccountsLocalFirst()).resolves.toBe(accounts);
    expect(getSelectableAccountsLocalFirstMock).toHaveBeenCalledTimes(1);
  });

  it("delegates trade refresh to the shared accounts api", async () => {
    const refreshResult = { fetched_count: 2, inserted_count: 1 };
    refreshTradesMock.mockResolvedValueOnce(refreshResult);

    await expect(refreshTrades(7012, { start: "2026-03-09T00:00:00Z", end: "2026-03-09T23:59:59Z" })).resolves.toBe(
      refreshResult,
    );
    expect(refreshTradesMock).toHaveBeenCalledWith(7012, {
      start: "2026-03-09T00:00:00Z",
      end: "2026-03-09T23:59:59Z",
    });
  });

  it("uses the backend incremental latest-trade window by default", async () => {
    const refreshResult = { fetched_count: 1, inserted_count: 1 };
    refreshTradesMock.mockResolvedValueOnce(refreshResult);

    await expect(refreshTrades(7012)).resolves.toBe(refreshResult);
    expect(refreshTradesMock).toHaveBeenCalledWith(7012, {});
  });
});
