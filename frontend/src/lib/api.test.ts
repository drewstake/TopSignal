import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({
  getAccessToken: vi.fn(async () => null),
}));

import { accountsApi, botsApi, buildProjectXCandleRequestKey, buildUserScopedProjectXCandleRequestKey } from "./api";
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

  it("keeps CSV accounts selectable without resurrecting missing ProjectX accounts", async () => {
    installDemoModeStorage(false);
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse([
        { id: 1, account_state: "ACTIVE", trade_data_source: "projectx" },
        { id: 2, account_state: "MISSING", trade_data_source: "projectx" },
        { id: 3, account_state: "HIDDEN", trade_data_source: "projectx" },
        { id: 4, account_state: "MISSING", trade_data_source: "csv_import" },
      ]),
    );

    await expect(accountsApi.getSelectableAccounts()).resolves.toEqual([
      { id: 1, account_state: "ACTIVE", trade_data_source: "projectx" },
      { id: 4, account_state: "MISSING", trade_data_source: "csv_import" },
    ]);

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("show_inactive=true");
    expect(String(url)).toContain("show_missing=false");
  });

  it("loads saved accounts first and keeps local and provider cache lanes separate", async () => {
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("local-first-account-cache"));
    const liveAccount = {
      id: 88001,
      account_state: "ACTIVE",
      trade_data_source: "csv_import",
      is_main: true,
    };
    const expressAccount = {
      id: 77001,
      account_state: "ACTIVE",
      trade_data_source: "projectx",
      is_main: false,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([liveAccount, expressAccount]))
      .mockResolvedValueOnce(jsonResponse([liveAccount, expressAccount]));

    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([liveAccount, expressAccount]);
    await expect(accountsApi.getSelectableAccounts({ refreshProvider: false })).resolves.toEqual([
      liveAccount,
      expressAccount,
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);

    await expect(accountsApi.getSelectableAccounts({ refreshProvider: true })).resolves.toEqual([
      liveAccount,
      expressAccount,
    ]);
    expect(fetch).toHaveBeenCalledTimes(2);

    const [localUrl] = vi.mocked(fetch).mock.calls[0];
    const [providerUrl] = vi.mocked(fetch).mock.calls[1];
    expect(String(localUrl)).toContain("refresh_provider=false");
    expect(String(providerUrl)).toContain("refresh_provider=true");
  });

  it("falls back to provider discovery when no saved selectable account exists", async () => {
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("empty-local-account-cache"));
    const expressAccount = {
      id: 77002,
      account_state: "ACTIVE",
      trade_data_source: "projectx",
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([expressAccount]));

    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([expressAccount]);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("refresh_provider=false");
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toContain("refresh_provider=true");
  });

  it("creates a local Live import account when ProjectX cannot list it", async () => {
    installDemoModeStorage(false);
    const account = {
      id: 88001,
      name: "Topstep Live Funded",
      provider_name: "Topstep Live Funded",
      custom_display_name: null,
      trade_data_source: "csv_import",
      balance: null,
      status: "ACTIVE",
      account_state: "ACTIVE",
      is_main: true,
      can_trade: null,
      is_visible: true,
      last_trade_at: null,
      last_seen_at: null,
      provider_data_stale: false,
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(account));

    await expect(
      accountsApi.createLiveImportAccount({
        account_id: 88001,
        name: "Topstep Live Funded",
      }),
    ).resolves.toEqual(account);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/accounts/import-target");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      account_id: 88001,
      name: "Topstep Live Funded",
    });
  });

  it("previews and confirms a trade import with multipart file identity", async () => {
    installDemoModeStorage(false);
    const file = new File(["Id,PnL\n1,100"], "trades_export.csv", { type: "text/csv" });
    const previewPayload = {
      source_file_name: file.name,
      file_sha256: "abc123",
      total_rows: 1,
      new_rows: 1,
      duplicate_rows: 0,
      summary: {
        gross_pnl: 100,
        fees: 0.74,
        commissions: 0.5,
        net_pnl: 98.76,
        wins: 1,
        losses: 0,
        breakeven: 0,
      },
      trades: [],
    };
    const confirmPayload = {
      import_id: 17,
      source_file_name: file.name,
      imported_at: "2026-07-23T15:00:00Z",
      total_rows: 1,
      inserted_rows: 1,
      duplicate_rows: 0,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(previewPayload))
      .mockResolvedValueOnce(jsonResponse(confirmPayload));

    await expect(accountsApi.previewTradeImport(7301, file)).resolves.toEqual(previewPayload);
    await expect(accountsApi.confirmTradeImport(7301, file, previewPayload.file_sha256)).resolves.toEqual(confirmPayload);

    const previewCall = vi.mocked(fetch).mock.calls[0];
    expect(previewCall[0]).toBe("http://127.0.0.1:8000/api/accounts/7301/trade-imports/preview");
    expect(previewCall[1]?.method).toBe("POST");
    const previewBody = previewCall[1]?.body as FormData;
    expect((previewBody.get("file") as File).name).toBe(file.name);

    const confirmCall = vi.mocked(fetch).mock.calls[1];
    expect(confirmCall[0]).toBe("http://127.0.0.1:8000/api/accounts/7301/trade-imports/confirm");
    expect(confirmCall[1]?.method).toBe("POST");
    const confirmBody = confirmCall[1]?.body as FormData;
    expect((confirmBody.get("file") as File).name).toBe(file.name);
    expect(confirmBody.get("preview_sha256")).toBe(previewPayload.file_sha256);
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

  it("binds the bot list and cache scope to one captured authentication token", async () => {
    const token = jwt("user-one");
    vi.mocked(getAccessToken).mockResolvedValue(token);
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ items: [], total: 0 }));

    const result = await botsApi.listConfigsWithCacheScope();

    expect(result).toEqual({
      configs: { items: [], total: 0 },
      cacheScope: "user:https%3A%2F%2Fauth.example.test:user-one",
    });
    expect(getAccessToken).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({
      Authorization: `Bearer ${token}`,
    });
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

  it("streams exact server replay progress before returning the result", async () => {
    const onProgress = vi.fn();
    vi.mocked(fetch).mockResolvedValueOnce(new Response(
      [
        ": connected",
        "",
        "event: progress",
        'data: {"phase":"replaying","completed":610,"total":1000,"percent":61,"remaining_percent":39}',
        "",
        "event: result",
        'data: {"id":18}',
        "",
      ].join("\n"),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    ));

    const response = await botsApi.runBacktest(42, {
      starting_balance: 50_000,
      commission_per_contract: 1.2,
      slippage_ticks: 1,
    }, { onProgress });

    expect(response.id).toBe(18);
    expect(onProgress).toHaveBeenCalledWith({
      phase: "replaying",
      completed: 610,
      total: 1_000,
      percent: 61,
      remaining_percent: 39,
    });
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    });
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

  it("never shares an identical candle request across authenticated cache scopes", () => {
    const query = {
      contractId: "CON.F.US.MNQ.M26",
      unit: "minute" as const,
      unitNumber: 5,
      start: "2026-07-10T14:00:00.000Z",
      end: "2026-07-10T15:00:00.000Z",
      limit: 300,
    };

    expect(buildUserScopedProjectXCandleRequestKey("user:one", query)).not.toBe(
      buildUserScopedProjectXCandleRequestKey("user:two", query),
    );
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
