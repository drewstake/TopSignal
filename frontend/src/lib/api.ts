import type {
  AccountInfo,
  AccountLastTradeInfo,
  JournalEntry,
  JournalEntryCreateResult,
  JournalEntryCreateInput,
  JournalEntryImage,
  JournalEntryUpdateInput,
  JournalEntriesQuery,
  JournalEntriesResponse,
  JournalDaysQuery,
  JournalDaysResponse,
  JournalPullTradeStatsInput,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountTrade,
  AccountTradeRefreshResult,
  BehaviorMetrics,
  DayPnlPoint,
  ExpenseCreateInput,
  ExpenseListQuery,
  ExpenseListResponse,
  ExpenseRange,
  ExpenseRecord,
  ExpenseTotals,
  ExpenseUpdateInput,
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
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
}

interface RequestMultipartOptions {
  method?: "POST";
  query?: Record<string, QueryValue>;
  formData: FormData;
  signal?: AbortSignal;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly detail: unknown;

  constructor(message: string, status: number, body: unknown, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.detail = detail;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
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

function toAbsoluteApiUrl(path: string) {
  return new URL(path, API_BASE_URL).toString();
}

function normalizeJournalImage(image: JournalEntryImage): JournalEntryImage {
  return {
    ...image,
    url: toAbsoluteApiUrl(image.url),
  };
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
    let errorBody: unknown = null;
    let detailValue: unknown = undefined;

    try {
      errorBody = (await response.json()) as { detail?: unknown };
      const parsed = errorBody as { detail?: unknown };
      detailValue = parsed.detail;
      if (typeof parsed.detail === "string") {
        detail = parsed.detail;
      } else if (parsed.detail !== undefined) {
        detail = JSON.stringify(parsed.detail);
      }
    } catch {
      // Keep default fallback error text.
    }

    throw new ApiError(detail, response.status, errorBody, detailValue);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function requestMultipart<T>(path: string, options: RequestMultipartOptions): Promise<T> {
  const { method = "POST", query, formData, signal } = options;
  const response = await fetch(buildUrl(path, query), {
    method,
    body: formData,
    signal,
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status} ${response.statusText})`;
    let errorBody: unknown = null;
    let detailValue: unknown = undefined;

    try {
      errorBody = (await response.json()) as { detail?: unknown };
      const parsed = errorBody as { detail?: unknown };
      detailValue = parsed.detail;
      if (typeof parsed.detail === "string") {
        detail = parsed.detail;
      } else if (parsed.detail !== undefined) {
        detail = JSON.stringify(parsed.detail);
      }
    } catch {
      // Keep default fallback error text.
    }

    throw new ApiError(detail, response.status, errorBody, detailValue);
  }

  return (await response.json()) as T;
}

let accountsCache: TimedCache<AccountInfo[]> | null = null;
let inFlightAccountsRequest: Promise<AccountInfo[]> | null = null;

function getAccountsCached(onlyActiveAccounts: boolean): Promise<AccountInfo[]> {
  if (!onlyActiveAccounts) {
    return requestJson<AccountInfo[]>("/api/accounts", {
      query: { only_active_accounts: false },
    });
  }

  const now = Date.now();
  if (accountsCache && accountsCache.expiresAtMs > now) {
    return Promise.resolve(accountsCache.value);
  }
  if (inFlightAccountsRequest) {
    return inFlightAccountsRequest;
  }

  inFlightAccountsRequest = requestJson<AccountInfo[]>("/api/accounts", {
    query: { only_active_accounts: true },
  })
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
  getAccounts: (onlyActiveAccounts = true) => getAccountsCached(onlyActiveAccounts),
  getLastTrade: (accountId: number, refresh = false) =>
    requestJson<AccountLastTradeInfo>(`/api/accounts/${accountId}/last-trade`, {
      query: {
        refresh,
      },
    }),
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
  getJournalEntries: (accountId: number, query: JournalEntriesQuery = {}) =>
    requestJson<JournalEntriesResponse>(`/api/accounts/${accountId}/journal`, {
      query: {
        start_date: query.start_date,
        end_date: query.end_date,
        mood: query.mood,
        q: query.q,
        include_archived: query.include_archived,
        limit: query.limit ?? 20,
        offset: query.offset ?? 0,
      },
    }),
  createJournalEntry: (accountId: number, body: JournalEntryCreateInput) =>
    requestJson<JournalEntryCreateResult>(`/api/accounts/${accountId}/journal`, {
      method: "POST",
      body,
    }),
  updateJournalEntry: (accountId: number, entryId: number, body: JournalEntryUpdateInput) =>
    requestJson<JournalEntry>(`/api/accounts/${accountId}/journal/${entryId}`, {
      method: "PATCH",
      body,
    }),
  deleteJournalEntry: (accountId: number, entryId: number) =>
    requestJson<void>(`/api/accounts/${accountId}/journal/${entryId}`, {
      method: "DELETE",
    }),
  getJournalDays: (accountId: number, query: JournalDaysQuery) =>
    requestJson<JournalDaysResponse>(`/api/accounts/${accountId}/journal/days`, {
      query: {
        start_date: query.start_date,
        end_date: query.end_date,
        include_archived: query.include_archived,
      },
    }),
  uploadJournalImage: (accountId: number, entryId: number, file: File | Blob, filename?: string) => {
    const formData = new FormData();
    const fallbackName =
      typeof File !== "undefined" && file instanceof File ? file.name : "journal-image";
    formData.append("file", file, filename ?? fallbackName);
    return requestMultipart<JournalEntryImage>(`/api/accounts/${accountId}/journal/${entryId}/images`, {
      formData,
    }).then((image) => normalizeJournalImage(image));
  },
  listJournalImages: (accountId: number, entryId: number) =>
    requestJson<JournalEntryImage[]>(`/api/accounts/${accountId}/journal/${entryId}/images`).then((images) =>
      images.map((image) => normalizeJournalImage(image)),
    ),
  deleteJournalImage: (accountId: number, entryId: number, imageId: number) =>
    requestJson<void>(`/api/accounts/${accountId}/journal/${entryId}/images/${imageId}`, {
      method: "DELETE",
    }),
  pullJournalTradeStats: (accountId: number, entryId: number, body: JournalPullTradeStatsInput = {}) =>
    requestJson<JournalEntry>(`/api/accounts/${accountId}/journal/${entryId}/pull-trade-stats`, {
      method: "POST",
      body,
    }),
};

export function listExpenses(params: ExpenseListQuery = {}) {
  return requestJson<ExpenseListResponse>("/api/expenses", {
    query: {
      start_date: params.start_date,
      end_date: params.end_date,
      account_id: params.account_id,
      category: params.category,
      limit: params.limit ?? 200,
      offset: params.offset ?? 0,
    },
  });
}

export function createExpense(payload: ExpenseCreateInput) {
  return requestJson<ExpenseRecord>("/api/expenses", {
    method: "POST",
    body: payload,
  });
}

export function deleteExpense(id: number) {
  return requestJson<void>(`/api/expenses/${id}`, { method: "DELETE" });
}

export function updateExpense(id: number, payload: ExpenseUpdateInput) {
  return requestJson<ExpenseRecord>(`/api/expenses/${id}`, {
    method: "PATCH",
    body: payload,
  });
}

export function getExpenseTotals(range: ExpenseRange, accountId?: number) {
  return requestJson<ExpenseTotals>("/api/expenses/totals", {
    query: {
      range,
      account_id: accountId,
    },
  });
}
