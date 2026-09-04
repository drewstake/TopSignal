import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({
  getAccessToken: vi.fn(async () => null),
}));

import {
  accountsApi,
  botsApi,
  buildProjectXCandleRequestKey,
  buildUserScopedProjectXCandleRequestKey,
  deleteExpense,
  getCombineTrackerExpenseSuppressions,
} from "./api";
import { DEMO_AS_OF_DATE } from "./demoScenario";
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
    vi.useRealTimers();
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

  it("requests one fresh account automation classification", async () => {
    const observed = {
      account_id: 7301,
      provider_simulated: true,
      provider_classification_observed_at: "2026-09-04T02:15:00Z",
      source: "projectx_user_hub",
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(observed));

    await expect(accountsApi.refreshAutomationClassification(7301)).resolves.toEqual(observed);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/accounts/7301/automation-classification/refresh",
    );
    expect(init?.method).toBe("POST");
  });

  it("reads persisted combine suppressions and distinguishes user deletion from duplicate cleanup", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ account_ids: [101, 202] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(getCombineTrackerExpenseSuppressions()).resolves.toEqual({
      account_ids: [101, 202],
    });
    await deleteExpense(7);
    await deleteExpense(8, { suppressAutoRecreation: false });

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:8000/api/expenses/combine-tracker-suppressions",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:8000/api/expenses/7?suppress_auto_recreation=true",
    );
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "http://127.0.0.1:8000/api/expenses/8?suppress_auto_recreation=false",
    );
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
      start: "2026-07-01",
      end: DEMO_AS_OF_DATE,
      refresh: true,
    });
    const trades = await accountsApi.getTrades(accounts[0].id, {
      start: "2026-07-01",
      end: DEMO_AS_OF_DATE,
      limit: 200,
      refresh: true,
    });
    const calendar = await accountsApi.getPnlCalendar(accounts[0].id, {
      start: "2026-07-01",
      end: DEMO_AS_OF_DATE,
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

  it("invalidates cached account reads after a provider-backed calendar refresh", async () => {
    installDemoModeStorage(false);
    const accountId = 93_001;
    let refreshed = false;
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes(`/api/accounts/${accountId}/pnl-calendar`)) {
        refreshed = true;
        return jsonResponse([]);
      }
      if (url.includes(`/api/accounts/${accountId}/summary`)) {
        return jsonResponse({ net_pnl: refreshed ? 325 : -146 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    await expect(accountsApi.getSummary(accountId)).resolves.toMatchObject({ net_pnl: -146 });
    await expect(accountsApi.getSummary(accountId)).resolves.toMatchObject({ net_pnl: -146 });
    expect(fetch).toHaveBeenCalledTimes(1);

    await accountsApi.getPnlCalendar(accountId, {
      start: "2026-07-31T22:00:00.000Z",
      end: "2026-08-31T20:59:59.999999Z",
      all_time: false,
      refresh: true,
    });

    await expect(accountsApi.getSummary(accountId)).resolves.toMatchObject({ net_pnl: 325 });
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("keeps CSV accounts selectable without resurrecting missing ProjectX accounts", async () => {
    installDemoModeStorage(false);
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse([
        { id: 1, account_state: "ACTIVE", trade_data_source: "projectx", is_archived: false },
        { id: 2, account_state: "MISSING", trade_data_source: "projectx", is_archived: false },
        { id: 3, account_state: "HIDDEN", trade_data_source: "projectx", is_archived: false },
        {
          id: 4,
          account_state: "MISSING",
          trade_data_source: "csv_import",
          is_archived: false,
          provider_data_stale: false,
          provider_sync_status: "not_applicable",
        },
        { id: 5, account_state: "ACTIVE", trade_data_source: "csv_import", is_archived: true },
      ]),
    );

    await expect(accountsApi.getSelectableAccounts()).resolves.toEqual([
      { id: 1, account_state: "ACTIVE", trade_data_source: "projectx", is_archived: false },
      {
        id: 4,
        account_state: "MISSING",
        trade_data_source: "csv_import",
        is_archived: false,
        provider_data_stale: false,
        provider_sync_status: "not_applicable",
      },
    ]);

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("show_inactive=true");
    expect(String(url)).toContain("show_missing=false");
    expect(String(url)).toContain("include_archived=false");
  });

  it("replaces the local-first cache lane after a successful provider refresh", async () => {
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("local-first-account-cache"));
    const liveAccount = {
      id: 88001,
      account_state: "ACTIVE",
      trade_data_source: "csv_import",
      is_archived: false,
      is_main: true,
      provider_data_stale: false,
      provider_sync_status: "not_applicable",
    };
    const savedExpressAccount = {
      id: 77001,
      name: "Saved Express",
      account_state: "ACTIVE",
      trade_data_source: "projectx",
      is_archived: false,
      is_main: false,
      provider_data_stale: true,
      provider_sync_status: "cache_stale",
    };
    const refreshedExpressAccount = {
      ...savedExpressAccount,
      name: "Refreshed Express",
      provider_data_stale: false,
      provider_sync_status: "provider_fresh",
      provider_last_successful_refresh_at: "2026-07-29T15:00:00Z",
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([liveAccount, savedExpressAccount]))
      .mockResolvedValueOnce(jsonResponse([liveAccount, refreshedExpressAccount]));

    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([liveAccount, savedExpressAccount]);
    await expect(accountsApi.getSelectableAccounts({ refreshProvider: false })).resolves.toEqual([
      liveAccount,
      savedExpressAccount,
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);

    await expect(accountsApi.getSelectableAccounts({ refreshProvider: true })).resolves.toEqual([
      liveAccount,
      refreshedExpressAccount,
    ]);
    expect(fetch).toHaveBeenCalledTimes(2);

    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([
      liveAccount,
      refreshedExpressAccount,
    ]);
    expect(fetch).toHaveBeenCalledTimes(2);

    const [localUrl] = vi.mocked(fetch).mock.calls[0];
    const [providerUrl] = vi.mocked(fetch).mock.calls[1];
    expect(String(localUrl)).toContain("refresh_provider=false");
    expect(String(providerUrl)).toContain("refresh_provider=true");
  });

  it("reclassifies a cached account after its deadline without another request", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-29T15:00:00.000Z"));
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("account-cache-deadline"));
    const refreshedAccount = {
      id: 77004,
      name: "Express",
      account_state: "ACTIVE",
      trade_data_source: "projectx",
      is_archived: false,
      is_main: true,
      provider_data_stale: false,
      provider_data_stale_at: "2026-07-29T15:00:01.000Z",
      provider_sync_status: "provider_fresh",
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([refreshedAccount]));

    await expect(
      accountsApi.getSelectableAccounts({ refreshProvider: true }),
    ).resolves.toEqual([refreshedAccount]);

    vi.setSystemTime(new Date("2026-07-29T15:00:01.000Z"));
    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([
      {
        ...refreshedAccount,
        provider_data_stale: true,
        provider_sync_status: "cache_stale",
      },
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not cache an HTTP-200 cached fallback as a successful provider refresh", async () => {
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("provider-fallback-retry"));
    const fallbackAccount = {
      id: 77002,
      name: "Cached Express",
      account_state: "ACTIVE",
      trade_data_source: "projectx",
      is_archived: false,
      provider_data_stale: false,
      provider_sync_status: "cached_fallback",
      provider_sync_error_code: "projectx_network_error",
      provider_sync_error_message: "ProjectX could not be reached.",
    };
    const refreshedAccount = {
      ...fallbackAccount,
      name: "Provider Express",
      provider_sync_status: "provider_fresh",
      provider_sync_error_code: null,
      provider_sync_error_message: null,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse([fallbackAccount]))
      .mockResolvedValueOnce(jsonResponse([refreshedAccount]));

    await expect(accountsApi.getSelectableAccounts({ refreshProvider: true })).resolves.toEqual([fallbackAccount]);
    await expect(accountsApi.getSelectableAccounts({ refreshProvider: true })).resolves.toEqual([refreshedAccount]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("does not let an older provider refresh overwrite the local cache lane", async () => {
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("provider-refresh-race"));
    const pendingResponses: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve) => pendingResponses.push(resolve)),
    );
    vi.stubGlobal("fetch", fetchMock);

    const olderAccount = {
      id: 77003,
      name: "Older Express Snapshot",
      account_state: "ACTIVE",
      trade_data_source: "projectx",
      is_archived: false,
      provider_data_stale: false,
      provider_sync_status: "provider_fresh",
      provider_last_successful_refresh_at: "2026-07-29T14:00:00Z",
    };
    const newerAccount = {
      ...olderAccount,
      name: "Newer Express Snapshot",
      provider_last_successful_refresh_at: "2026-07-29T15:00:00Z",
    };

    const olderRequest = accountsApi.getSelectableAccounts({
      refreshProvider: true,
      bypassCache: true,
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const newerRequest = accountsApi.getSelectableAccounts({
      refreshProvider: true,
      bypassCache: true,
    });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    pendingResponses[1](jsonResponse([newerAccount]));
    await expect(newerRequest).resolves.toEqual([newerAccount]);
    pendingResponses[0](jsonResponse([olderAccount]));
    await expect(olderRequest).resolves.toEqual([olderAccount]);

    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([newerAccount]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not start provider discovery when the saved selectable snapshot is empty", async () => {
    installDemoModeStorage(false);
    vi.mocked(getAccessToken).mockResolvedValue(jwt("empty-local-account-cache"));
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]));

    await expect(accountsApi.getSelectableAccountsLocalFirst()).resolves.toEqual([]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("refresh_provider=false");
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
      is_archived: false,
      can_trade: null,
      is_visible: true,
      last_trade_at: null,
      last_seen_at: null,
      provider_data_stale: false,
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(account));

    await expect(
      accountsApi.createLiveImportAccount({
        name: "Topstep Live Funded",
        starting_balance: 10000,
      }),
    ).resolves.toEqual(account);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/accounts/import-target");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      name: "Topstep Live Funded",
      starting_balance: 10000,
    });
  });

  it("previews once, confirms by staged token, and checks the durable outcome", async () => {
    installDemoModeStorage(false);
    const file = new File(["Id,PnL\n1,100"], "trades_export.csv", { type: "text/csv" });
    const previewPayload = {
      preview_token: "opaque-preview-token",
      expires_at: "2026-07-23T15:30:00Z",
      source_file_name: file.name,
      file_sha256: "abc123",
      total_rows: 1,
      new_rows: 1,
      duplicate_rows: 0,
      conflict_rows: 0,
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
    const statusPayload = {
      status: "committed",
      confirmation_retryable: false,
      outcome_code: "committed",
      source_file_name: file.name,
      created_at: "2026-07-23T15:00:00Z",
      expires_at: "2026-07-23T15:30:00Z",
      confirmed_at: "2026-07-23T15:01:00Z",
      total_rows: 1,
      new_rows: 1,
      duplicate_rows: 0,
      conflict_rows: 0,
      result: confirmPayload,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(previewPayload))
      .mockResolvedValueOnce(jsonResponse(confirmPayload))
      .mockResolvedValueOnce(jsonResponse(statusPayload));

    await expect(accountsApi.previewTradeImport(7301, file)).resolves.toEqual(previewPayload);
    await expect(accountsApi.confirmTradeImport(7301, previewPayload.preview_token)).resolves.toEqual(confirmPayload);
    await expect(accountsApi.getTradeImportStatus(7301, previewPayload.preview_token)).resolves.toEqual(statusPayload);

    const previewCall = vi.mocked(fetch).mock.calls[0];
    expect(previewCall[0]).toBe("http://127.0.0.1:8000/api/accounts/7301/trade-imports/preview");
    expect(previewCall[1]?.method).toBe("POST");
    const previewBody = previewCall[1]?.body as FormData;
    expect((previewBody.get("file") as File).name).toBe(file.name);

    const confirmCall = vi.mocked(fetch).mock.calls[1];
    expect(confirmCall[0]).toBe("http://127.0.0.1:8000/api/accounts/7301/trade-imports/confirm");
    expect(confirmCall[1]?.method).toBe("POST");
    const confirmBody = confirmCall[1]?.body as FormData;
    expect(confirmBody.get("file")).toBeNull();
    expect(confirmBody.get("preview_token")).toBe(previewPayload.preview_token);

    const statusCall = vi.mocked(fetch).mock.calls[2];
    expect(statusCall[0]).toBe("http://127.0.0.1:8000/api/accounts/7301/trade-imports/status");
    expect(statusCall[1]?.method).toBe("POST");
    expect(JSON.parse(String(statusCall[1]?.body))).toEqual({ preview_token: "opaque-preview-token" });
    expect(String(statusCall[0])).not.toContain("opaque-preview-token");
  });

  it("archives and restores Live accounts through explicit lifecycle endpoints", async () => {
    installDemoModeStorage(false);
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          account_id: 88001,
          is_archived: true,
          is_main: false,
          replacement_main_account_id: 88002,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          account_id: 88001,
          is_archived: false,
          is_main: false,
          replacement_main_account_id: null,
        }),
      );

    await expect(accountsApi.archiveLiveAccount(88001, 88002)).resolves.toMatchObject({
      is_archived: true,
      replacement_main_account_id: 88002,
    });
    await expect(accountsApi.unarchiveLiveAccount(88001)).resolves.toMatchObject({
      is_archived: false,
    });

    const [archiveUrl, archiveInit] = vi.mocked(fetch).mock.calls[0];
    expect(archiveUrl).toBe("http://127.0.0.1:8000/api/accounts/88001/archive");
    expect(archiveInit?.method).toBe("POST");
    expect(JSON.parse(String(archiveInit?.body))).toEqual({ replacement_account_id: 88002 });
    const [restoreUrl, restoreInit] = vi.mocked(fetch).mock.calls[1];
    expect(restoreUrl).toBe("http://127.0.0.1:8000/api/accounts/88001/unarchive");
    expect(restoreInit?.method).toBe("POST");
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

  it("returns the structured emergency-flatten safety result when the provider cannot verify flat", async () => {
    const unconfirmed = {
      run: {
        id: 90,
        bot_config_id: 42,
        account_id: 7301,
        status: "stopped",
        dry_run: false,
        started_at: "2026-09-03T12:00:00Z",
        stopped_at: "2026-09-03T12:01:00Z",
        stop_reason: "manual_emergency_flatten",
        last_heartbeat_at: null,
      },
      confirmed_flat: false,
      status: "unconfirmed",
      risk_block: {
        code: "broker_account_flatten_unconfirmed",
        message: "The provider still reports exposure.",
        severity: "critical",
      },
      audit: { scope: "entire_account" },
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(unconfirmed), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(botsApi.emergencyFlatten(42, true)).resolves.toEqual(unconfirmed);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/bots/42/emergency-flatten");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ confirm_broker_flatten: true });
  });

  it("uses the account kill switch without requiring a bot config", async () => {
    const unconfirmed = {
      account_id: 7301,
      audit_id: 91,
      confirmed_flat: false,
      status: "unconfirmed",
      risk_block: {
        code: "broker_account_flatten_unconfirmed",
        message: "The provider still reports exposure.",
        severity: "critical",
      },
      audit: { scope: "entire_account" },
      disabled_bot_config_ids: [42, 43],
      stopped_bot_run_ids: [90],
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(unconfirmed), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(botsApi.emergencyFlattenAccount(7301, true)).resolves.toEqual(unconfirmed);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/accounts/7301/emergency-flatten");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ confirm_broker_flatten: true });
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
