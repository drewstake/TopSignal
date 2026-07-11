import { describe, expect, it } from "vitest";

import { formatAccountBalance, getAccountProviderState, getAvailableAccountBalance } from "./accountProviderState";

describe("account provider state", () => {
  it("preserves a missing balance as unavailable rather than zero", () => {
    expect(getAvailableAccountBalance(null)).toBeNull();
    expect(getAvailableAccountBalance(0)).toBe(0);
    expect(formatAccountBalance(null)).toBe("Unavailable");
    expect(formatAccountBalance(0)).toBe("$0.00");
  });

  it("keeps provider staleness and last-seen metadata", () => {
    expect(
      getAccountProviderState({
        balance: null,
        provider_data_stale: true,
        last_seen_at: "2026-07-10T14:30:00.000Z",
      }),
    ).toEqual({
      balance: null,
      stale: true,
      lastSeenAt: "2026-07-10T14:30:00.000Z",
    });
  });
});
