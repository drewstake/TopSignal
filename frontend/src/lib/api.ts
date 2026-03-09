import type {
  AccountMainUpdateResult,
  AccountInfo,
  AccountLastTradeInfo,
  AccountRenameResult,
  AuthMe,
  JournalEntry,
  JournalEntryCreateResult,
  JournalEntryCreateInput,
  JournalEntryImage,
  JournalEntrySaveResult,
  JournalEntryUpdateInput,
  JournalEntriesQuery,
  JournalEntriesResponse,
  JournalDaysQuery,
  JournalDaysResponse,
  JournalPullTradeStatsInput,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountSummaryWithPointBases,
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
  PayoutCreateInput,
  PayoutListQuery,
  PayoutListResponse,
  PayoutRecord,
  PayoutTotals,
  HourPnlPoint,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
  ProjectXCredentialsInput,
  ProjectXCredentialsStatus,
} from "./types";
import { dispatchAccountDisplayNameUpdated } from "./accountSelection";
import { ENABLE_PERF_LOGS, logPerfInfo } from "./perf";
import { getAccessToken } from "./supabase";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const ACCOUNTS_CACHE_TTL_MS = 10 * 60_000;
const ACCOUNT_READ_CACHE_TTL_MS = 10 * 60_000;

type QueryValue = string | number | boolean | null | undefined;

interface RequestJsonOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
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

interface RequestBlobOptions {
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

interface TimedCachedRequestOptions<T> {
  cache: Map<string, TimedCache<T>>;
  inFlight: Map<string, Promise<T>>;
  cacheKey: string;
  ttlMs: number;
  load: () => Promise<T>;
  bypassCache?: boolean;
}

function getTimedCachedRequest<T>(options: TimedCachedRequestOptions<T>): Promise<T> {
  const { cache, inFlight, cacheKey, ttlMs, load, bypassCache = false } = options;
  if (!bypassCache) {
    const now = Date.now();
    const cached = cache.get(cacheKey);
    if (cached && cached.expiresAtMs > now) {
      return Promise.resolve(cached.value);
    }
    const pendingRequest = inFlight.get(cacheKey);
    if (pendingRequest) {
      return pendingRequest;
    }
  }

  const request = load().then((value) => {
    if (inFlight.get(cacheKey) === request) {
      cache.set(cacheKey, {
        value,
        expiresAtMs: Date.now() + ttlMs,
      });
    }
    return value;
  });

  inFlight.set(cacheKey, request);
  void request.finally(() => {
    if (inFlight.get(cacheKey) === request) {
      inFlight.delete(cacheKey);
    }
  });

  return request;
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

function toSortedQueryCacheKey(query?: Record<string, QueryValue>) {
  if (!query) {
    return "";
  }
  return Object.entries(query)
    .filter(([, value]) => value !== null && value !== undefined)
    .sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey))
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
    .join("&");
}

function toAbsoluteApiUrl(path: string) {
  return new URL(path, API_BASE_URL).toString();
}

interface RequestPerfContext {
  method: string;
  path: string;
  url: string;
  startedAtIso: string;
  startedAtMs: number;
}

function nowMs() {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

function parseServerTimeMs(response: Response): number | null {
  const direct = response.headers.get("x-server-time-ms");
  if (direct) {
    const parsed = Number.parseFloat(direct);
    if (Number.isFinite(parsed) && parsed >= 0) {
      return parsed;
    }
  }

  const serverTiming = response.headers.get("server-timing");
  if (!serverTiming) {
    return null;
  }
  const match = /(?:^|,)\s*app;dur=([0-9]+(?:\.[0-9]+)?)/i.exec(serverTiming);
  if (!match) {
    return null;
  }
  const parsed = Number.parseFloat(match[1]);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }
  return parsed;
}

function parseContentLength(response: Response): number | null {
  const raw = response.headers.get("content-length");
  if (!raw) {
    return null;
  }
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }
  return parsed;
}

function logApiPerfStart(context: RequestPerfContext) {
  if (!ENABLE_PERF_LOGS) {
    return;
  }
  logPerfInfo("[perf][api] start", {
    method: context.method,
    path: context.path,
    url: context.url,
    started_at: context.startedAtIso,
  });
}

function logApiPerfEnd(context: RequestPerfContext, response: Response) {
  if (!ENABLE_PERF_LOGS) {
    return;
  }
  const finishedAtMs = nowMs();
  const totalMs = Math.max(finishedAtMs - context.startedAtMs, 0);
  const serverMs = parseServerTimeMs(response);
  const networkMs = serverMs !== null ? Math.max(totalMs - serverMs, 0) : null;
  logPerfInfo("[perf][api] end", {
    method: context.method,
    path: context.path,
    status: response.status,
    started_at: context.startedAtIso,
    finished_at: new Date().toISOString(),
    total_ms: Number(totalMs.toFixed(2)),
    server_ms: serverMs !== null ? Number(serverMs.toFixed(2)) : null,
    network_ms: networkMs !== null ? Number(networkMs.toFixed(2)) : null,
    response_bytes: parseContentLength(response),
  });
}

function normalizeJournalImage(image: JournalEntryImage): JournalEntryImage {
  return {
    ...image,
    url: toAbsoluteApiUrl(image.url),
  };
}

async function requestJson<T>(path: string, options: RequestJsonOptions = {}): Promise<T> {
  const { method = "GET", query, body, signal } = options;
  const accessToken = await getAccessToken();
  const url = buildUrl(path, query);
  const perfContext: RequestPerfContext = {
    method,
    path,
    url,
    startedAtIso: new Date().toISOString(),
    startedAtMs: nowMs(),
  };
  logApiPerfStart(perfContext);
  const headers: Record<string, string> = {};
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(url, {
    method,
    headers: Object.keys(headers).length === 0 ? undefined : headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  logApiPerfEnd(perfContext, response);

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
  const accessToken = await getAccessToken();
  const url = buildUrl(path, query);
  const perfContext: RequestPerfContext = {
    method,
    path,
    url,
    startedAtIso: new Date().toISOString(),
    startedAtMs: nowMs(),
  };
  logApiPerfStart(perfContext);
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(url, {
    method,
    headers: Object.keys(headers).length === 0 ? undefined : headers,
    body: formData,
    signal,
  });
  logApiPerfEnd(perfContext, response);

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

export async function requestBlob(path: string, options: RequestBlobOptions = {}): Promise<Blob> {
  const { signal } = options;
  const accessToken = await getAccessToken();
  const url = toAbsoluteApiUrl(path);
  const perfContext: RequestPerfContext = {
    method: "GET",
    path,
    url,
    startedAtIso: new Date().toISOString(),
    startedAtMs: nowMs(),
  };
  logApiPerfStart(perfContext);

  const headers: Record<string, string> = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(url, {
    method: "GET",
    headers: Object.keys(headers).length === 0 ? undefined : headers,
    signal,
  });
  logApiPerfEnd(perfContext, response);

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

  return await response.blob();
}

const accountsCacheByQuery = new Map<string, TimedCache<AccountInfo[]>>();
const inFlightAccountsByQuery = new Map<string, Promise<AccountInfo[]>>();
const accountTradesCacheByQuery = new Map<string, TimedCache<AccountTrade[]>>();
const inFlightAccountTradesByQuery = new Map<string, Promise<AccountTrade[]>>();
const accountSummaryCacheByQuery = new Map<string, TimedCache<AccountSummary>>();
const inFlightAccountSummaryByQuery = new Map<string, Promise<AccountSummary>>();
const accountSummaryWithPointBasesCacheByQuery = new Map<string, TimedCache<AccountSummaryWithPointBases>>();
const inFlightAccountSummaryWithPointBasesByQuery = new Map<string, Promise<AccountSummaryWithPointBases>>();
const accountPnlCalendarCacheByQuery = new Map<string, TimedCache<AccountPnlCalendarDay[]>>();
const inFlightAccountPnlCalendarByQuery = new Map<string, Promise<AccountPnlCalendarDay[]>>();
const accountJournalDaysCacheByQuery = new Map<string, TimedCache<JournalDaysResponse>>();
const inFlightAccountJournalDaysByQuery = new Map<string, Promise<JournalDaysResponse>>();
const accountCacheVersionById = new Map<number, number>();
const accountJournalCacheVersionById = new Map<number, number>();

interface RequestSignalOptions {
  signal?: AbortSignal;
}

interface GetAccountsOptions {
  showInactive?: boolean;
  showMissing?: boolean;
}

function resolveGetAccountsOptions(optionsOrOnlyActive?: GetAccountsOptions | boolean): Required<GetAccountsOptions> {
  if (typeof optionsOrOnlyActive === "boolean") {
    return optionsOrOnlyActive
      ? { showInactive: false, showMissing: false }
      : { showInactive: true, showMissing: true };
  }
  return {
    showInactive: optionsOrOnlyActive?.showInactive ?? false,
    showMissing: optionsOrOnlyActive?.showMissing ?? false,
  };
}

function accountsQueryCacheKey(options: Required<GetAccountsOptions>) {
  return `${options.showInactive ? 1 : 0}:${options.showMissing ? 1 : 0}`;
}

function getAccountCacheVersion(accountId: number) {
  return accountCacheVersionById.get(accountId) ?? 0;
}

function getAccountJournalCacheVersion(accountId: number) {
  return accountJournalCacheVersionById.get(accountId) ?? 0;
}

function accountReadQueryCacheKey(accountId: number, query?: Record<string, QueryValue>) {
  const version = getAccountCacheVersion(accountId);
  const serializedQuery = toSortedQueryCacheKey(query);
  return serializedQuery.length > 0
    ? `account|${accountId}|v${version}|${serializedQuery}`
    : `account|${accountId}|v${version}`;
}

function accountJournalReadQueryCacheKey(accountId: number, query?: Record<string, QueryValue>) {
  const version = getAccountJournalCacheVersion(accountId);
  const serializedQuery = toSortedQueryCacheKey(query);
  return serializedQuery.length > 0
    ? `account-journal|${accountId}|v${version}|${serializedQuery}`
    : `account-journal|${accountId}|v${version}`;
}

function clearMapByAccountPrefix<T>(map: Map<string, T>, accountId: number) {
  const prefix = `account|${accountId}|`;
  for (const key of map.keys()) {
    if (key.startsWith(prefix)) {
      map.delete(key);
    }
  }
}

function clearMapByPrefix<T>(map: Map<string, T>, prefix: string) {
  for (const key of map.keys()) {
    if (key.startsWith(prefix)) {
      map.delete(key);
    }
  }
}

function invalidateAccountReadCaches(accountId?: number) {
  if (typeof accountId !== "number") {
    accountCacheVersionById.clear();
    accountTradesCacheByQuery.clear();
    inFlightAccountTradesByQuery.clear();
    accountSummaryCacheByQuery.clear();
    inFlightAccountSummaryByQuery.clear();
    accountSummaryWithPointBasesCacheByQuery.clear();
    inFlightAccountSummaryWithPointBasesByQuery.clear();
    accountPnlCalendarCacheByQuery.clear();
    inFlightAccountPnlCalendarByQuery.clear();
    invalidateAccountJournalCaches();
    return;
  }

  accountCacheVersionById.set(accountId, getAccountCacheVersion(accountId) + 1);
  clearMapByAccountPrefix(accountTradesCacheByQuery, accountId);
  clearMapByAccountPrefix(inFlightAccountTradesByQuery, accountId);
  clearMapByAccountPrefix(accountSummaryCacheByQuery, accountId);
  clearMapByAccountPrefix(inFlightAccountSummaryByQuery, accountId);
  clearMapByAccountPrefix(accountSummaryWithPointBasesCacheByQuery, accountId);
  clearMapByAccountPrefix(inFlightAccountSummaryWithPointBasesByQuery, accountId);
  clearMapByAccountPrefix(accountPnlCalendarCacheByQuery, accountId);
  clearMapByAccountPrefix(inFlightAccountPnlCalendarByQuery, accountId);
}

function invalidateAccountJournalCaches(accountId?: number) {
  if (typeof accountId !== "number") {
    accountJournalCacheVersionById.clear();
    accountJournalDaysCacheByQuery.clear();
    inFlightAccountJournalDaysByQuery.clear();
    return;
  }

  accountJournalCacheVersionById.set(accountId, getAccountJournalCacheVersion(accountId) + 1);
  clearMapByPrefix(accountJournalDaysCacheByQuery, `account-journal|${accountId}|`);
  clearMapByPrefix(inFlightAccountJournalDaysByQuery, `account-journal|${accountId}|`);
}

function invalidateAccountsListCaches() {
  accountsCacheByQuery.clear();
  inFlightAccountsByQuery.clear();
}

function getAccountsFromApi(options: Required<GetAccountsOptions>): Promise<AccountInfo[]> {
  const cacheKey = accountsQueryCacheKey(options);
  const now = Date.now();
  const cached = accountsCacheByQuery.get(cacheKey);
  if (cached && cached.expiresAtMs > now) {
    return Promise.resolve(cached.value);
  }

  const inFlight = inFlightAccountsByQuery.get(cacheKey);
  if (inFlight) {
    return inFlight;
  }

  const request = requestJson<AccountInfo[]>("/api/accounts", {
    query: {
      show_inactive: options.showInactive,
      show_missing: options.showMissing,
    },
  })
    .then((accounts) => {
      accountsCacheByQuery.set(cacheKey, {
        value: accounts,
        expiresAtMs: Date.now() + ACCOUNTS_CACHE_TTL_MS,
      });
      return accounts;
    })
    .finally(() => {
      inFlightAccountsByQuery.delete(cacheKey);
    });

  inFlightAccountsByQuery.set(cacheKey, request);
  return request;
}

function getAccountsCached(optionsOrOnlyActive?: GetAccountsOptions | boolean): Promise<AccountInfo[]> {
  const options = resolveGetAccountsOptions(optionsOrOnlyActive);
  return getAccountsFromApi(options);
}

function isSelectableAccount(account: Pick<AccountInfo, "account_state">): boolean {
  return account.account_state === "ACTIVE" || account.account_state === "LOCKED_OUT";
}

function getSelectableAccountsFromApi(): Promise<AccountInfo[]> {
  return getAccountsFromApi({ showInactive: true, showMissing: false }).then((accounts) =>
    accounts.filter((account) => isSelectableAccount(account)),
  );
}

export function getAccounts(optionsOrOnlyActive?: GetAccountsOptions | boolean): Promise<AccountInfo[]> {
  return getAccountsCached(optionsOrOnlyActive);
}

export function getSelectableAccounts(): Promise<AccountInfo[]> {
  return getSelectableAccountsFromApi();
}

export function refreshTrades(accountId: number, query: Pick<AccountSummaryQuery, "start" | "end"> = {}) {
  return requestJson<AccountTradeRefreshResult>(`/api/accounts/${accountId}/trades/refresh`, {
    method: "POST",
    query: {
      start: query.start,
      end: query.end,
    },
  }).then((result) => {
    invalidateAccountReadCaches(accountId);
    return result;
  });
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
  pointsBasis?: "auto" | "MNQ" | "MES" | "MGC" | "SIL";
}

interface AccountPnlCalendarQuery extends AccountSummaryQuery {
  all_time?: boolean;
}

export const accountsApi = {
  getAccounts,
  getSelectableAccounts,
  getAuthMe: () => requestJson<AuthMe>("/api/auth/me"),
  getProjectXCredentialsStatus: () =>
    requestJson<ProjectXCredentialsStatus>("/api/me/providers/projectx/credentials/status"),
  putProjectXCredentials: (payload: ProjectXCredentialsInput) =>
    requestJson<void>("/api/me/providers/projectx/credentials", {
      method: "PUT",
      body: payload,
    }),
  deleteProjectXCredentials: () =>
    requestJson<void>("/api/me/providers/projectx/credentials", {
      method: "DELETE",
    }),
  setMainAccount: (accountId: number) =>
    requestJson<AccountMainUpdateResult>(`/api/accounts/${accountId}/main`, {
      method: "POST",
    }).then((payload) => {
      invalidateAccountsListCaches();
      invalidateAccountReadCaches();
      return payload;
    }),
  renameAccountDisplayName: (accountId: number, displayName: string) =>
    requestJson<AccountRenameResult>(`/api/accounts/${accountId}/display-name`, {
      method: "PATCH",
      body: {
        display_name: displayName,
      },
    }).then((payload) => {
      invalidateAccountsListCaches();
      dispatchAccountDisplayNameUpdated(accountId);
      return payload;
    }),
  getLastTrade: (accountId: number, refresh = false) =>
    requestJson<AccountLastTradeInfo>(`/api/accounts/${accountId}/last-trade`, {
      query: {
        refresh,
      },
    }),
  getTrades: (accountId: number, query: AccountTradesQuery = {}) => {
    const requestQuery = {
      limit: query.limit ?? 200,
      start: query.start,
      end: query.end,
      symbol: query.symbol,
      refresh: query.refresh,
    };
    const cacheKey = accountReadQueryCacheKey(accountId, {
      limit: requestQuery.limit,
      start: requestQuery.start,
      end: requestQuery.end,
      symbol: requestQuery.symbol,
    });
    return getTimedCachedRequest({
      cache: accountTradesCacheByQuery,
      inFlight: inFlightAccountTradesByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: () =>
        requestJson<AccountTrade[]>(`/api/accounts/${accountId}/trades`, {
          query: requestQuery,
        }),
    });
  },
  getSummary: (accountId: number, query: AccountSummaryQuery = {}) => {
    const requestQuery = {
      start: query.start,
      end: query.end,
      refresh: query.refresh,
      pointsBasis: query.pointsBasis,
    };
    const cacheKey = accountReadQueryCacheKey(accountId, {
      start: requestQuery.start,
      end: requestQuery.end,
      pointsBasis: requestQuery.pointsBasis,
    });
    return getTimedCachedRequest({
      cache: accountSummaryCacheByQuery,
      inFlight: inFlightAccountSummaryByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: () =>
        requestJson<AccountSummary>(`/api/accounts/${accountId}/summary`, {
          query: requestQuery,
        }),
    });
  },
  getSummaryWithPointBases: (accountId: number, query: Pick<AccountSummaryQuery, "start" | "end" | "refresh"> = {}) => {
    const requestQuery = {
      start: query.start,
      end: query.end,
      refresh: query.refresh,
    };
    const cacheKey = accountReadQueryCacheKey(accountId, {
      start: requestQuery.start,
      end: requestQuery.end,
    });
    return getTimedCachedRequest({
      cache: accountSummaryWithPointBasesCacheByQuery,
      inFlight: inFlightAccountSummaryWithPointBasesByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: () =>
        requestJson<AccountSummaryWithPointBases>(`/api/accounts/${accountId}/summary-with-point-bases`, {
          query: requestQuery,
        }),
    });
  },
  getPnlCalendar: (accountId: number, query: AccountPnlCalendarQuery = {}) => {
    const requestQuery = {
      start: query.start,
      end: query.end,
      all_time: query.all_time,
      refresh: query.refresh,
    };
    const cacheKey = accountReadQueryCacheKey(accountId, {
      start: requestQuery.start,
      end: requestQuery.end,
      all_time: requestQuery.all_time,
    });
    return getTimedCachedRequest({
      cache: accountPnlCalendarCacheByQuery,
      inFlight: inFlightAccountPnlCalendarByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: () =>
        requestJson<AccountPnlCalendarDay[]>(`/api/accounts/${accountId}/pnl-calendar`, {
          query: requestQuery,
        }),
    });
  },
  refreshTrades,
  getJournalEntries: (accountId: number, query: JournalEntriesQuery = {}, options: RequestSignalOptions = {}) =>
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
      signal: options.signal,
    }),
  createJournalEntry: (accountId: number, body: JournalEntryCreateInput) =>
    requestJson<JournalEntryCreateResult>(`/api/accounts/${accountId}/journal`, {
      method: "POST",
      body,
    }).then((result) => {
      invalidateAccountJournalCaches(accountId);
      return result;
    }),
  updateJournalEntry: (accountId: number, entryId: number, body: JournalEntryUpdateInput) =>
    requestJson<JournalEntrySaveResult>(`/api/accounts/${accountId}/journal/${entryId}`, {
      method: "PATCH",
      body,
    }).then((result) => {
      invalidateAccountJournalCaches(accountId);
      return result;
    }),
  deleteJournalEntry: (accountId: number, entryId: number) =>
    requestJson<void>(`/api/accounts/${accountId}/journal/${entryId}`, {
      method: "DELETE",
    }).then((result) => {
      invalidateAccountJournalCaches(accountId);
      return result;
    }),
  getJournalDays: (accountId: number, query: JournalDaysQuery) => {
    const requestQuery = {
      start_date: query.start_date,
      end_date: query.end_date,
      include_archived: query.include_archived,
    };
    const cacheKey = accountJournalReadQueryCacheKey(accountId, requestQuery);
    return getTimedCachedRequest({
      cache: accountJournalDaysCacheByQuery,
      inFlight: inFlightAccountJournalDaysByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      load: () =>
        requestJson<JournalDaysResponse>(`/api/accounts/${accountId}/journal/days`, {
          query: requestQuery,
        }),
    });
  },
  uploadJournalImage: (accountId: number, entryId: number, file: File | Blob, filename?: string) => {
    const formData = new FormData();
    const fallbackName =
      typeof File !== "undefined" && file instanceof File ? file.name : "journal-image";
    formData.append("file", file, filename ?? fallbackName);
    return requestMultipart<JournalEntryImage>(`/api/accounts/${accountId}/journal/${entryId}/images`, {
      formData,
    }).then((image) => normalizeJournalImage(image));
  },
  listJournalImages: (accountId: number, entryId: number, options: RequestSignalOptions = {}) =>
    requestJson<JournalEntryImage[]>(`/api/accounts/${accountId}/journal/${entryId}/images`, {
      signal: options.signal,
    }).then((images) =>
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
    }).then((result) => {
      invalidateAccountJournalCaches(accountId);
      return result;
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

interface ExpenseTotalsQuery {
  accountId?: number;
  startDate?: string;
  endDate?: string;
  startCreatedAt?: string;
  endCreatedAt?: string;
}

export function getExpenseTotals(range: ExpenseRange, options: ExpenseTotalsQuery = {}) {
  return requestJson<ExpenseTotals>("/api/expenses/totals", {
    query: {
      range,
      account_id: options.accountId,
      start_date: options.startDate,
      end_date: options.endDate,
      start_created_at: options.startCreatedAt,
      end_created_at: options.endCreatedAt,
    },
  });
}

export function listPayouts(params: PayoutListQuery = {}) {
  return requestJson<PayoutListResponse>("/api/payouts", {
    query: {
      start_date: params.start_date,
      end_date: params.end_date,
      limit: params.limit ?? 200,
      offset: params.offset ?? 0,
    },
  });
}

export function createPayout(payload: PayoutCreateInput) {
  return requestJson<PayoutRecord>("/api/payouts", {
    method: "POST",
    body: payload,
  });
}

export function deletePayout(id: number) {
  return requestJson<void>(`/api/payouts/${id}`, { method: "DELETE" });
}

interface PayoutTotalsQuery {
  startDate?: string;
  endDate?: string;
}

export function getPayoutTotals(options: PayoutTotalsQuery = {}) {
  return requestJson<PayoutTotals>("/api/payouts/totals", {
    query: {
      start_date: options.startDate,
      end_date: options.endDate,
    },
  });
}
