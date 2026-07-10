import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({
  getAccessToken: vi.fn(async () => null),
}));

import { accountsApi, botsApi, buildProjectXCandleRequestKey } from "./api";
import { getAccessToken } from "./supabase";

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
    vi.mocked(getAccessToken).mockResolvedValue(null);
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

  it("deduplicates within one user while isolating cache and in-flight work across auth switches", async () => {
    installDemoModeStorage(false);
    const tokenOne = jwt("user-one");
    const tokenTwo = jwt("user-two");
    const pending = new Map<string, (response: Response) => void>();
    const fetchMock = vi.fn((_url: string | URL | Request, init?: RequestInit) => {
      const authorization = (init?.headers as Record<string, string> | undefined)?.Authorization ?? "";
      return new Promise<Response>((resolve) => pending.set(authorization, resolve));
    });
    vi.stubGlobal("fetch", fetchMock);

    vi.mocked(getAccessToken).mockResolvedValue(tokenOne);
    const userOneFirst = accountsApi.getAccounts({ showInactive: true, showMissing: true });
    const userOneDeduped = accountsApi.getAccounts({ showInactive: true, showMissing: true });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    vi.mocked(getAccessToken).mockResolvedValue(tokenTwo);
    const userTwoFirst = accountsApi.getAccounts({ showInactive: true, showMissing: true });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    pending.get(`Bearer ${tokenTwo}`)?.(jsonResponse([{ id: 2, account_state: "ACTIVE" }]));
    await expect(userTwoFirst).resolves.toEqual([{ id: 2, account_state: "ACTIVE" }]);
    pending.get(`Bearer ${tokenOne}`)?.(jsonResponse([{ id: 1, account_state: "ACTIVE" }]));
    await expect(Promise.all([userOneFirst, userOneDeduped])).resolves.toEqual([
      [{ id: 1, account_state: "ACTIVE" }],
      [{ id: 1, account_state: "ACTIVE" }],
    ]);

    await expect(accountsApi.getAccounts({ showInactive: true, showMissing: true })).resolves.toEqual([
      { id: 2, account_state: "ACTIVE" },
    ]);
    vi.mocked(getAccessToken).mockResolvedValue(tokenOne);
    await expect(accountsApi.getAccounts({ showInactive: true, showMissing: true })).resolves.toEqual([
      { id: 1, account_state: "ACTIVE" },
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("botsApi", () => {
  beforeEach(() => {
    vi.mocked(getAccessToken).mockResolvedValue(null);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ id: 17 }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("posts one date-free full-history request to the plural backend route", async () => {
    const payload = {
      starting_balance: 50_000,
      commission_per_contract: 1.2,
      slippage_ticks: 1,
      force_close_at_end: true,
    };

    await botsApi.runBacktest(42, payload);

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/bots/42/backtests");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(payload);
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("limit");
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("max_bars");
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("confirm_live_order_routing");
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("start");
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("end");
  });

  it("deduplicates closed-history identities within the same candle bucket", () => {
    const base = {
      contractId: " con.f.us.mnq.m26 ",
      symbol: " mnq ",
      unit: "minute" as const,
      unitNumber: 5,
      limit: 300,
      includePartialBar: false,
    };

    expect(
      buildProjectXCandleRequestKey({
        ...base,
        start: "2026-07-10T13:00:01.000Z",
        end: "2026-07-10T14:04:01.000Z",
      }),
    ).toBe(
      buildProjectXCandleRequestKey({
        ...base,
        contractId: "CON.F.US.MNQ.M26",
        symbol: "MNQ",
        start: "2026-07-10T13:00:59.000Z",
        end: "2026-07-10T14:04:59.000Z",
      }),
    );
  });

  it("keeps partial, repair, and authoritative refresh requests distinct", () => {
    const query = {
      contractId: "CON.F.US.MNQ.M26",
      unit: "minute" as const,
      unitNumber: 1,
      start: "2026-07-10T14:00:00.000Z",
      end: "2026-07-10T14:05:00.000Z",
      limit: 5,
    };
    const normal = buildProjectXCandleRequestKey(query);

    expect(buildProjectXCandleRequestKey({ ...query, includePartialBar: true })).not.toBe(normal);
    expect(buildProjectXCandleRequestKey({ ...query, repair: true })).not.toBe(normal);
    expect(buildProjectXCandleRequestKey({ ...query, refresh: true })).not.toBe(normal);
  });
});

function jwt(subject: string): string {
  const header = globalThis.btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const payload = globalThis.btoa(JSON.stringify({ iss: "https://auth.example.test", sub: subject }));
  return `${header}.${payload}.signature`;
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
