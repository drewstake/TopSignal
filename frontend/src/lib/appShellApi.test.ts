import { describe, expect, it, vi } from "vitest";

const { getSelectableAccountsMock, refreshTradesMock } = vi.hoisted(() => ({
  getSelectableAccountsMock: vi.fn(),
  refreshTradesMock: vi.fn(),
}));

vi.mock("./api", () => ({
  accountsApi: {
    getSelectableAccounts: getSelectableAccountsMock,
    refreshTrades: refreshTradesMock,
  },
}));

import { getSelectableAccounts, refreshTrades } from "./appShellApi";

describe("appShellApi", () => {
  it("delegates account loading to the shared accounts api", async () => {
    const accounts = [{ id: 1, name: "Main", account_state: "ACTIVE" }];
    getSelectableAccountsMock.mockResolvedValueOnce(accounts);

    await expect(getSelectableAccounts()).resolves.toBe(accounts);
    expect(getSelectableAccountsMock).toHaveBeenCalledTimes(1);
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
});
