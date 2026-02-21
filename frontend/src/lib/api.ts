import type {
  AccountInfo,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountTrade,
  AccountTradeRefreshResult,
  BehaviorMetrics,
  DayPnlPoint,
  HourPnlPoint,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ACCOUNTS_CACHE_TTL_MS = 30_000;

type QueryValue = string | number | boolean | null | undefined;

interface RequestJsonOptions {
  method?: "GET" | "POST";
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
}

interface TimedCache<T> {
  value: T;
  expiresAtMs: number;
}

function buildUrl(path: string, query?: Record<string, QueryValue>) {
  const url = new URL(path, API_BASE_URL);
  if (query) {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value === null || value === undefined) {
        return;
      }
      params.set(key, String(value));
    });
    const queryString = params.toString();
    if (queryString.length > 0) {
      url.search = queryString;
    }
  }
  return url.toString();
}

async function requestJson<T>(path: string, options: RequestJsonOptions = {}): Promise<T> {
  const { method = "GET", query, body, signal } = options;
  const response = await fetch(buildUrl(path, query), {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status} ${response.statusText})`;

    try {
      const errorBody = (await response.json()) as { detail?: unknown };
      if (typeof errorBody.detail === "string") {
        detail = errorBody.detail;
      } else if (errorBody.detail !== undefined) {
        detail = JSON.stringify(errorBody.detail);
      }
    } catch {
      // Keep default fallback error text.
    }

    throw new Error(detail);
  }

  return (await response.json()) as T;
}

let accountsCache: TimedCache<AccountInfo[]> | null = null;
let inFlightAccountsRequest: Promise<AccountInfo[]> | null = null;

function getAccountsCached(): Promise<AccountInfo[]> {
  const now = Date.now();
  if (accountsCache && accountsCache.expiresAtMs > now) {
    return Promise.resolve(accountsCache.value);
  }
  if (inFlightAccountsRequest) {
    return inFlightAccountsRequest;
  }

  inFlightAccountsRequest = requestJson<AccountInfo[]>("/api/accounts")
    .then((accounts) => {
      accountsCache = {
        value: accounts,
        expiresAtMs: Date.now() + ACCOUNTS_CACHE_TTL_MS,
      };
      return accounts;
    })
    .finally(() => {
      inFlightAccountsRequest = null;
    });

  return inFlightAccountsRequest;
}

export const metricsApi = {
  getSummary: (accountId?: number) =>
    requestJson<SummaryMetrics>("/metrics/summary", { query: { account_id: accountId } }),
  getPnlByHour: (accountId?: number) =>
    requestJson<HourPnlPoint[]>("/metrics/pnl-by-hour", { query: { account_id: accountId } }),
  getPnlByDay: (accountId?: number) =>
    requestJson<DayPnlPoint[]>("/metrics/pnl-by-day", { query: { account_id: accountId } }),
  getPnlBySymbol: (accountId?: number) =>
    requestJson<SymbolPnlPoint[]>("/metrics/pnl-by-symbol", { query: { account_id: accountId } }),
  getStreaks: (accountId?: number) =>
    requestJson<StreakMetrics>("/metrics/streaks", { query: { account_id: accountId } }),
  getBehavior: (accountId?: number) =>
    requestJson<BehaviorMetrics>("/metrics/behavior", { query: { account_id: accountId } }),
  getTrades: (limit = 100, accountId?: number) =>
    requestJson<TradeRecord[]>("/trades", { query: { limit, account_id: accountId } }),
};

interface AccountTradesQuery {
  limit?: number;
  start?: string;
  end?: string;
  symbol?: string;
  refresh?: boolean;
}

interface AccountSummaryQuery {
  start?: string;
  end?: string;
  refresh?: boolean;
}

interface AccountPnlCalendarQuery extends AccountSummaryQuery {
  all_time?: boolean;
}

export const accountsApi = {
  getAccounts: () => getAccountsCached(),
  getTrades: (accountId: number, query: AccountTradesQuery = {}) =>
    requestJson<AccountTrade[]>(`/api/accounts/${accountId}/trades`, {
      query: {
        limit: query.limit ?? 200,
        start: query.start,
        end: query.end,
        symbol: query.symbol,
        refresh: query.refresh,
      },
    }),
  getSummary: (accountId: number, query: AccountSummaryQuery = {}) =>
    requestJson<AccountSummary>(`/api/accounts/${accountId}/summary`, {
      query: {
        start: query.start,
        end: query.end,
        refresh: query.refresh,
      },
    }),
  getPnlCalendar: (accountId: number, query: AccountPnlCalendarQuery = {}) =>
    requestJson<AccountPnlCalendarDay[]>(`/api/accounts/${accountId}/pnl-calendar`, {
      query: {
        start: query.start,
        end: query.end,
        all_time: query.all_time,
        refresh: query.refresh,
      },
    }),
  refreshTrades: (accountId: number, query: Pick<AccountSummaryQuery, "start" | "end"> = {}) =>
    requestJson<AccountTradeRefreshResult>(`/api/accounts/${accountId}/trades/refresh`, {
      method: "POST",
      query: {
        start: query.start,
        end: query.end,
      },
    }),
};
