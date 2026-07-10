import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountInfo, BotConfig, ProjectXMarketCandle } from "./types";

vi.mock("./supabase", () => ({
  getAccessToken: vi.fn(async () => null),
}));

const SELECTED_BOT_STORAGE_KEY = "topsignal.bot.selected-config-id";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installStorage(): Map<string, string> {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
    removeItem: vi.fn((key: string) => values.delete(key)),
    clear: vi.fn(() => values.clear()),
  });
  return values;
}

function makeAccount(id: number, accountState: AccountInfo["account_state"]): AccountInfo {
  return {
    id,
    name: `Account ${id}`,
    provider_name: "projectx",
    custom_display_name: null,
    balance: 50_000,
    status: accountState,
    account_state: accountState,
    is_main: id === 1,
    can_trade: accountState === "ACTIVE",
    is_visible: accountState !== "HIDDEN",
    last_trade_at: null,
  };
}

function makeBot(id: number, overrides: Partial<BotConfig> = {}): BotConfig {
  return {
    id,
    name: `Bot ${id}`,
    account_id: 1,
    provider: "projectx",
    enabled: true,
    execution_mode: "dry_run",
    strategy_type: "sma_cross",
    strategy_params: {},
    contract_id: `CONTRACT-${id}`,
    symbol: "MNQ",
    timeframe_unit: "minute",
    timeframe_unit_number: 5,
    lookback_bars: 100,
    fast_period: 9,
    slow_period: 21,
    order_size: 1,
    max_contracts: 1,
    max_daily_loss: 500,
    max_trades_per_day: 5,
    max_open_position: 1,
    allowed_contracts: [],
    trading_start_time: "09:30",
    trading_end_time: "16:00",
    cooldown_seconds: 0,
    max_data_staleness_seconds: 60,
    allow_market_depth: false,
    created_at: "2026-07-10T12:00:00Z",
    updated_at: "2026-07-10T12:00:00Z",
    ...overrides,
  };
}

describe("api request reuse", () => {
  let storage: Map<string, string>;

  beforeEach(() => {
    vi.resetModules();
    storage = installStorage();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("deduplicates concurrent config lists and serves later reads from cache", async () => {
    const networkResponse = deferred<Response>();
    const fetchMock = vi.fn<typeof fetch>(() => networkResponse.promise);
    vi.stubGlobal("fetch", fetchMock);
    const { botsApi } = await import("./api");
    const payload = { items: [makeBot(7)], total: 1 };

    const first = botsApi.listConfigs(41);
    const second = botsApi.listConfigs(41);

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    networkResponse.resolve(jsonResponse(payload));
    const [firstResult, secondResult] = await Promise.all([first, second]);

    expect(secondResult).toBe(firstResult);
    await expect(botsApi.listConfigs(41)).resolves.toBe(firstResult);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("clears a failed config request so the next caller can retry", async () => {
    const payload = { items: [makeBot(8)], total: 1 };
    let attempt = 0;
    const fetchMock = vi.fn<typeof fetch>(async () => {
      attempt += 1;
      return attempt === 1
        ? jsonResponse({ detail: "temporarily unavailable" }, 503)
        : jsonResponse(payload);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { botsApi } = await import("./api");

    await expect(botsApi.listConfigs()).rejects.toThrow("temporarily unavailable");
    await expect(botsApi.listConfigs()).resolves.toEqual(payload);

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("invalidates the config list cache after a successful mutation", async () => {
    const original = makeBot(7);
    const updated = makeBot(7, { name: "Updated bot" });
    let listReads = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/bots" && init?.method === "GET") {
        listReads += 1;
        return jsonResponse({ items: [listReads === 1 ? original : updated], total: 1 });
      }
      if (url.pathname === "/api/bots/7" && init?.method === "PATCH") {
        return jsonResponse(updated);
      }
      throw new Error(`Unexpected request: ${init?.method} ${url.pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { botsApi } = await import("./api");

    await expect(botsApi.listConfigs()).resolves.toEqual({ items: [original], total: 1 });
    await expect(botsApi.listConfigs()).resolves.toEqual({ items: [original], total: 1 });
    expect(listReads).toBe(1);

    await botsApi.updateConfig(7, { name: updated.name });
    await expect(botsApi.listConfigs()).resolves.toEqual({ items: [updated], total: 1 });

    expect(listReads).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("shares account list in-flight work and cache across account consumers", async () => {
    const accounts = [makeAccount(1, "ACTIVE"), makeAccount(2, "LOCKED_OUT"), makeAccount(3, "HIDDEN")];
    const networkResponse = deferred<Response>();
    const fetchMock = vi.fn<typeof fetch>(() => networkResponse.promise);
    vi.stubGlobal("fetch", fetchMock);
    const { accountsApi } = await import("./api");

    const allAccounts = accountsApi.getAccounts({ showInactive: true, showMissing: false });
    const selectableAccounts = accountsApi.getSelectableAccounts();

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    networkResponse.resolve(jsonResponse(accounts));
    await expect(allAccounts).resolves.toEqual(accounts);
    await expect(selectableAccounts).resolves.toEqual(accounts.slice(0, 2));

    await expect(accountsApi.getAccounts({ showInactive: true, showMissing: false })).resolves.toEqual(accounts);
    await expect(accountsApi.getSelectableAccounts()).resolves.toEqual(accounts.slice(0, 2));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const requestedUrl = new URL(String(fetchMock.mock.calls[0][0]));
    expect(requestedUrl.pathname).toBe("/api/accounts");
    expect(requestedUrl.searchParams.get("show_inactive")).toBe("true");
    expect(requestedUrl.searchParams.get("show_missing")).toBe("false");
  });

  it("keeps a shared candle request alive when one consumer aborts", async () => {
    const candles: ProjectXMarketCandle[] = [
      {
        id: 1,
        contract_id: "CONTRACT-7",
        symbol: "MNQ",
        live: false,
        unit: "minute",
        unit_number: 5,
        timestamp: "2026-07-10T14:30:00Z",
        open: 20_000,
        high: 20_010,
        low: 19_995,
        close: 20_005,
        volume: 100,
        is_partial: false,
        fetched_at: "2026-07-10T14:31:00Z",
      },
    ];
    const networkResponse = deferred<Response>();
    const fetchMock = vi.fn<typeof fetch>(() => networkResponse.promise);
    vi.stubGlobal("fetch", fetchMock);
    const { botsApi } = await import("./api");
    const query = {
      contractId: "CONTRACT-7",
      symbol: "MNQ",
      start: "2026-07-10T13:00:00Z",
      end: "2026-07-10T15:00:00Z",
      unit: "minute" as const,
      unitNumber: 5,
      limit: 500,
    };
    const controller = new AbortController();
    const nearbyQuery = {
      ...query,
      start: "2026-07-10T13:00:01Z",
      end: "2026-07-10T15:00:01Z",
    };

    const abortingConsumer = botsApi.getCandles(query, { signal: controller.signal });
    const survivingConsumer = botsApi.getCandles(nearbyQuery);
    controller.abort();

    await expect(abortingConsumer).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    networkResponse.resolve(jsonResponse(candles));

    await expect(survivingConsumer).resolves.toEqual(candles);
    await expect(botsApi.getCandles(nearbyQuery)).resolves.toEqual(candles);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.signal).toBeUndefined();
  });

  it("warms only the selected bot, configured timeframe first, with unique bounded strategy timeframes", async () => {
    storage.set(SELECTED_BOT_STORAGE_KEY, "22");
    const unselectedBot = makeBot(11, { contract_id: "UNSELECTED-CONTRACT" });
    const selectedBot = makeBot(22, {
      contract_id: "SELECTED-CONTRACT",
      timeframe_unit: "minute",
      timeframe_unit_number: 5,
      strategy_type: "topbot_adaptive",
      strategy_params: {
        source_strategies: [
          "support_resistance",
          "relative_strength_spy",
          "supertrend_pivot",
          "delayed_orb_confirmation",
        ],
      },
    });
    const configuredCandleResponse = deferred<Response>();
    const candleUrls: URL[] = [];
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/bots") {
        return jsonResponse({ items: [unselectedBot, selectedBot], total: 2 });
      }
      if (url.pathname === "/api/projectx/candles") {
        candleUrls.push(url);
        if (candleUrls.length === 1) {
          return configuredCandleResponse.promise;
        }
        return jsonResponse([]);
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { botsApi } = await import("./api");

    const firstWarmup = botsApi.warmSelected();
    const secondWarmup = botsApi.warmSelected();
    expect(secondWarmup).toBe(firstWarmup);

    await vi.waitFor(() => expect(candleUrls).toHaveLength(1));
    expect(candleUrls[0].searchParams.get("contract_id")).toBe("SELECTED-CONTRACT");
    expect(candleUrls[0].searchParams.get("unit")).toBe("minute");
    expect(candleUrls[0].searchParams.get("unit_number")).toBe("5");

    configuredCandleResponse.resolve(jsonResponse([]));
    await firstWarmup;

    expect(candleUrls).toHaveLength(4);
    expect(candleUrls.every((url) => url.searchParams.get("contract_id") === "SELECTED-CONTRACT")).toBe(true);
    const warmedTimeframes = candleUrls.map(
      (url) => `${url.searchParams.get("unit")}:${url.searchParams.get("unit_number")}`,
    );
    expect(warmedTimeframes[0]).toBe("minute:5");
    expect(new Set(warmedTimeframes)).toEqual(new Set(["minute:5", "hour:1", "hour:4", "day:1"]));
  });
});
