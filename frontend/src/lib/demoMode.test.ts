import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDemoAccountLabel,
  isDemoModeEnabled,
  sanitizeDemoApiResponse,
  setDemoModeEnabled,
} from "./demoMode";

function installLocalStorage() {
  const store = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value);
    }),
    removeItem: vi.fn((key: string) => {
      store.delete(key);
    }),
    clear: vi.fn(() => {
      store.clear();
    }),
  });
}

describe("demoMode", () => {
  beforeEach(() => {
    installLocalStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("masks account display labels when enabled", () => {
    setDemoModeEnabled(true);

    const label = getDemoAccountLabel({
      id: 12345678,
      name: "Andrew personal combine",
    });

    expect(isDemoModeEnabled()).toBe(true);
    expect(label).toContain("Demo Account");
    expect(label).toContain("ACCT-");
    expect(label).not.toContain("Andrew");
    expect(label).not.toContain("12345678");
  });

  it("sanitizes account, journal, trade, and money response fields without changing API ids", () => {
    setDemoModeEnabled(true);

    const response = sanitizeDemoApiResponse("/api/accounts/12345678/journal", {
      account: {
        id: 12345678,
        name: "Andrew personal combine",
        provider_name: "50KTC-Andrew-12345678",
        custom_display_name: "My main account",
        balance: 50123.45,
        account_state: "ACTIVE",
      },
      entry: {
        id: 44,
        account_id: 12345678,
        entry_date: "2026-06-29",
        title: "Very personal recap",
        mood: "Focused",
        tags: ["personal"],
        body: "Private journal notes",
        net_pnl: 728.5,
      },
      trade: {
        order_id: "real-order-123",
        source_trade_id: "real-source-456",
        net_pnl: 728.5,
      },
    });

    expect(response.account.id).toBe(12345678);
    expect(response.account.name).toContain("Demo Account");
    expect(response.account.provider_name).toContain("Demo Account");
    expect(response.account.custom_display_name).toBeNull();
    expect(response.account.balance).not.toBe(50123.45);
    expect(response.entry.title).toBe("Demo journal 2026-06-29");
    expect(response.entry.tags).toEqual(["demo"]);
    expect(response.entry.body).not.toContain("Private");
    expect(response.trade.order_id).toMatch(/^TRD-/);
    expect(response.trade.source_trade_id).toMatch(/^TRD-/);
    expect(response.trade.net_pnl).not.toBe(728.5);
  });
});
