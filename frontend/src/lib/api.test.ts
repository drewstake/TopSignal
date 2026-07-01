import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({
  getAccessToken: vi.fn(async () => null),
}));

import { accountsApi } from "./api";

function installDemoModeStorage(enabled: boolean) {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(() => (enabled ? "true" : "false")),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  });
}

describe("accountsApi", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            account_id: 7301,
            entry_date: "2026-05-12",
            journal_entry_id: 42,
            created: true,
            updated: false,
            skipped: false,
            skip_reason: null,
            source_trade_count: 1,
            recap_markdown: "# Daily Recap",
            generated_at: "2026-05-13T12:00:00Z",
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("calls the ProjectX AI recap route", async () => {
    await accountsApi.generateAIJournalRecap(7301, {
      entry_date: "2026-05-12",
      mode: "append_or_create",
      include_existing_notes: true,
    });

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/projectx/accounts/7301/journal/ai-recap");
    expect(init?.method).toBe("POST");
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(String(init?.body))).toEqual({
      entry_date: "2026-05-12",
      mode: "append_or_create",
      include_existing_notes: true,
    });
  });

  it("blocks write requests while demo mode is enabled", async () => {
    installDemoModeStorage(true);

    await expect(
      accountsApi.generateAIJournalRecap(7301, {
        entry_date: "2026-05-12",
        mode: "append_or_create",
        include_existing_notes: true,
      }),
    ).rejects.toThrow("Demo mode is read-only");

    expect(fetch).not.toHaveBeenCalled();
  });

  it("serves demo dashboard data without calling the backend", async () => {
    installDemoModeStorage(true);

    const accounts = await accountsApi.getSelectableAccounts();
    const summary = await accountsApi.getSummary(accounts[0].id, {
      start: "2026-06-01",
      end: "2026-06-30",
      refresh: true,
    });
    const trades = await accountsApi.getTrades(accounts[0].id, {
      start: "2026-06-01",
      end: "2026-06-30",
      limit: 200,
      refresh: true,
    });
    const calendar = await accountsApi.getPnlCalendar(accounts[0].id, {
      start: "2026-06-01",
      end: "2026-06-30",
      refresh: true,
    });

    expect(accounts.length).toBeGreaterThan(2);
    expect(summary.trade_count).toBeGreaterThan(25);
    expect(summary.win_count).toBeGreaterThan(0);
    expect(summary.loss_count).toBeGreaterThan(0);
    expect(trades.length).toBe(summary.trade_count);
    expect(calendar.length).toBeGreaterThan(10);
    expect(fetch).not.toHaveBeenCalled();
  });
});
