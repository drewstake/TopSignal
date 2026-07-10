import type {
  AccountMainUpdateResult,
  AccountInfo,
  AccountLastTradeInfo,
  AccountRenameResult,
  AIJournalRecapInput,
  AIJournalRecapResult,
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
  JournalMergeInput,
  JournalMergeResult,
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
  BotActivity,
  BotBacktestInput,
  BotBacktestResult,
  BotConfig,
  BotConfigInput,
  BotConfigListResponse,
  BotConfigUpdateInput,
  BotEvaluation,
  BotTimeframeUnit,
  ProjectXContract,
  ProjectXMarketCandle,
  ProjectXMarketPrice,
  TradeEvaluationResult,
  TradePlanEvaluationInput,
} from "./types";
import { dispatchAccountDisplayNameUpdated } from "./accountSelection";
import { getDemoApiResponse } from "./demoData";
import { isDemoModeEnabled, sanitizeDemoApiResponse } from "./demoMode";
import { ENABLE_PERF_LOGS, logPerfInfo } from "./perf";
import { getAccessToken } from "./supabase";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const ACCOUNTS_CACHE_TTL_MS = 10 * 60_000;
const ACCOUNT_READ_CACHE_TTL_MS = 10 * 60_000;
const BOT_CONFIG_CACHE_TTL_MS = 60_000;
const BOT_ACTIVITY_CACHE_TTL_MS = 5_000;
const BOT_CANDLE_CACHE_TTL_MS = 30_000;
const SELECTED_BOT_STORAGE_KEY = "topsignal.bot.selected-config-id";
const BACKTEST_REQUEST_TIMEOUT_MS = 5 * 60_000;

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
  const clearInFlight = () => {
    if (inFlight.get(cacheKey) === request) {
      inFlight.delete(cacheKey);
    }
  };
  void request.then(clearInFlight, clearInFlight);

  return request;
}

function getSharedInFlightRequest<T>(
  inFlight: Map<string, Promise<T>>,
  cacheKey: string,
  load: () => Promise<T>,
): Promise<T> {
  const pendingRequest = inFlight.get(cacheKey);
  if (pendingRequest) {
    return pendingRequest;
  }
  const request = load();
  inFlight.set(cacheKey, request);
  const clearInFlight = () => {
    if (inFlight.get(cacheKey) === request) {
      inFlight.delete(cacheKey);
    }
  };
  void request.then(clearInFlight, clearInFlight);
  return request;
}

function abortError(): Error {
  const error = new Error("The operation was aborted");
  error.name = "AbortError";
  return error;
}

function withConsumerAbort<T>(request: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) {
    return request;
  }
  if (signal.aborted) {
    return Promise.reject(abortError());
  }

  return new Promise<T>((resolve, reject) => {
    const handleAbort = () => reject(abortError());
    signal.addEventListener("abort", handleAbort, { once: true });
    void request.then(
      (value) => {
        signal.removeEventListener("abort", handleAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", handleAbort);
        reject(error);
      },
    );
  });
}

function requestCacheScope(): "demo" | "live" {
  return isDemoModeEnabled() ? "demo" : "live";
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
  if (method !== "GET" && isDemoModeEnabled()) {
    throw new ApiError("Demo mode is read-only. Turn it off to sync or save changes.", 409, null, null);
  }
  if (method === "GET" && isDemoModeEnabled()) {
    const demoResponse = getDemoApiResponse<T>(path, query);
    if (demoResponse) {
      return demoResponse.data;
    }
  }
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

  return sanitizeDemoApiResponse(path, (await response.json()) as T);
}

async function requestMultipart<T>(path: string, options: RequestMultipartOptions): Promise<T> {
  const { method = "POST", query, formData, signal } = options;
  if (isDemoModeEnabled()) {
    throw new ApiError("Demo mode is read-only. Turn it off to upload or save changes.", 409, null, null);
  }
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

  return sanitizeDemoApiResponse(path, (await response.json()) as T);
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
  bypassCache?: boolean;
}

function resolveGetAccountsOptions(optionsOrOnlyActive?: GetAccountsOptions | boolean): Required<GetAccountsOptions> {
  if (typeof optionsOrOnlyActive === "boolean") {
    return optionsOrOnlyActive
      ? { showInactive: false, showMissing: false, bypassCache: false }
      : { showInactive: true, showMissing: true, bypassCache: false };
  }
  return {
    showInactive: optionsOrOnlyActive?.showInactive ?? false,
    showMissing: optionsOrOnlyActive?.showMissing ?? false,
    bypassCache: optionsOrOnlyActive?.bypassCache ?? false,
  };
}

function accountsQueryCacheKey(options: Required<GetAccountsOptions>) {
  return `${requestCacheScope()}:${options.showInactive ? 1 : 0}:${options.showMissing ? 1 : 0}`;
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
  return getTimedCachedRequest({
    cache: accountsCacheByQuery,
    inFlight: inFlightAccountsByQuery,
    cacheKey,
    ttlMs: ACCOUNTS_CACHE_TTL_MS,
    bypassCache: options.bypassCache,
    load: () =>
      requestJson<AccountInfo[]>("/api/accounts", {
        query: {
          show_inactive: options.showInactive,
          show_missing: options.showMissing,
        },
      }),
  });
}

function getAccountsCached(optionsOrOnlyActive?: GetAccountsOptions | boolean): Promise<AccountInfo[]> {
  const options = resolveGetAccountsOptions(optionsOrOnlyActive);
  return getAccountsFromApi(options);
}

function isSelectableAccount(account: Pick<AccountInfo, "account_state">): boolean {
  return account.account_state === "ACTIVE" || account.account_state === "LOCKED_OUT";
}

function getSelectableAccountsFromApi(): Promise<AccountInfo[]> {
  return getAccountsFromApi({ showInactive: true, showMissing: false, bypassCache: false }).then((accounts) =>
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
  includeLifecycle?: boolean;
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
      include_lifecycle: query.includeLifecycle,
    };
    const cacheKey = accountReadQueryCacheKey(accountId, {
      limit: requestQuery.limit,
      start: requestQuery.start,
      end: requestQuery.end,
      symbol: requestQuery.symbol,
      include_lifecycle: requestQuery.include_lifecycle,
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
  generateAIJournalRecap: (accountId: number, body: AIJournalRecapInput) =>
    requestJson<AIJournalRecapResult>(`/projectx/accounts/${accountId}/journal/ai-recap`, {
      method: "POST",
      body,
    }).then((result) => {
      if (result.created || result.updated) {
        invalidateAccountJournalCaches(accountId);
      }
      return result;
    }),
  mergeJournalEntries: (body: JournalMergeInput) =>
    requestJson<JournalMergeResult>("/api/journal/merge", {
      method: "POST",
      body,
    }).then((result) => {
      invalidateAccountsListCaches();
      invalidateAccountReadCaches(body.from_account_id);
      invalidateAccountReadCaches(body.to_account_id);
      invalidateAccountJournalCaches(body.from_account_id);
      invalidateAccountJournalCaches(body.to_account_id);
      return result;
  }),
};

interface ContractSearchQuery {
  searchText: string;
  live?: boolean;
}

interface CandleQuery {
  contractId: string;
  symbol?: string;
  start?: string;
  end?: string;
  live?: boolean;
  unit?: BotTimeframeUnit;
  unitNumber?: number;
  limit?: number;
  includePartialBar?: boolean;
  refresh?: boolean;
  /** Force a full-window upstream fetch to backfill missing bars without pruning cache. */
  repair?: boolean;
}

interface BotConfigListOptions {
  bypassCache?: boolean;
}

export interface BotWarmupTimeframe {
  unit: BotTimeframeUnit;
  unitNumber: number;
}

const BOT_WARMUP_MAX_TIMEFRAMES = 4;
const BOT_CHART_MIN_BARS = 300;
const BOT_CHART_MAX_BARS = 2_000;
const BOT_CHART_LOOKBACK_MULTIPLIER = 3;
const BOT_TIMEFRAME_SECONDS: Record<BotTimeframeUnit, number> = {
  second: 1,
  minute: 60,
  hour: 60 * 60,
  day: 24 * 60 * 60,
  week: 7 * 24 * 60 * 60,
  month: 31 * 24 * 60 * 60,
};

const botConfigsCacheByQuery = new Map<string, TimedCache<BotConfigListResponse>>();
const inFlightBotConfigsByQuery = new Map<string, Promise<BotConfigListResponse>>();
const botActivityCacheByQuery = new Map<string, TimedCache<BotActivity>>();
const inFlightBotActivityByQuery = new Map<string, Promise<BotActivity>>();
const botCandlesCacheByQuery = new Map<string, TimedCache<ProjectXMarketCandle[]>>();
const inFlightBotCandlesByQuery = new Map<string, Promise<ProjectXMarketCandle[]>>();
let selectedBotWarmupRequest: Promise<void> | null = null;

function normalizeTimeframe(unit: BotTimeframeUnit, unitNumber: number): BotWarmupTimeframe {
  return {
    unit,
    unitNumber: Math.max(1, Math.trunc(unitNumber)),
  };
}

function deriveLowerTimeframe(unit: BotTimeframeUnit, unitNumber: number): BotWarmupTimeframe {
  const totalSeconds = BOT_TIMEFRAME_SECONDS[unit] * Math.max(1, Math.trunc(unitNumber));
  const units: BotTimeframeUnit[] = ["month", "week", "day", "hour", "minute"];
  for (const divisor of [4, 3, 5, 2]) {
    if (totalSeconds % divisor !== 0) {
      continue;
    }
    const candidateSeconds = totalSeconds / divisor;
    for (const candidateUnit of units) {
      const seconds = BOT_TIMEFRAME_SECONDS[candidateUnit];
      if (candidateSeconds % seconds === 0) {
        return normalizeTimeframe(candidateUnit, candidateSeconds / seconds);
      }
    }
    if (unit === "second") {
      return normalizeTimeframe("second", candidateSeconds);
    }
  }
  return normalizeTimeframe(unit, unitNumber);
}

function addWarmupTimeframe(target: BotWarmupTimeframe[], timeframe: BotWarmupTimeframe) {
  const normalized = normalizeTimeframe(timeframe.unit, timeframe.unitNumber);
  if (target.some((item) => item.unit === normalized.unit && item.unitNumber === normalized.unitNumber)) {
    return;
  }
  if (target.length < BOT_WARMUP_MAX_TIMEFRAMES) {
    target.push(normalized);
  }
}

function addStrategyWarmupTimeframes(
  target: BotWarmupTimeframe[],
  bot: BotConfig,
  strategyType: BotConfig["strategy_type"],
) {
  if (strategyType === "topbot_adaptive") {
    for (const sourceStrategy of bot.strategy_params?.source_strategies ?? []) {
      if (sourceStrategy !== "topbot_adaptive") {
        addStrategyWarmupTimeframes(target, bot, sourceStrategy);
      }
      if (target.length >= BOT_WARMUP_MAX_TIMEFRAMES) {
        break;
      }
    }
    return;
  }
  if (strategyType === "delayed_orb_confirmation") {
    addWarmupTimeframe(target, { unit: "minute", unitNumber: 1 });
    return;
  }
  if (strategyType === "support_resistance" || strategyType === "liquidity_sweep_retest" || strategyType === "macd_support_resistance") {
    addWarmupTimeframe(target, { unit: "hour", unitNumber: 1 });
    addWarmupTimeframe(target, { unit: "hour", unitNumber: 4 });
    return;
  }
  if (strategyType === "supertrend_pivot") {
    addWarmupTimeframe(target, { unit: "day", unitNumber: 1 });
    return;
  }
  if (strategyType === "fvg_sweep_mss") {
    addWarmupTimeframe(target, deriveLowerTimeframe(bot.timeframe_unit, bot.timeframe_unit_number));
    return;
  }
  if (strategyType === "relative_strength_spy" || strategyType === "opening_rvol_breakout" || strategyType === "vwap_gap_retrace") {
    addWarmupTimeframe(target, { unit: "minute", unitNumber: 5 });
  }
}

export function getBotWarmupTimeframes(bot: BotConfig): BotWarmupTimeframe[] {
  const timeframes: BotWarmupTimeframe[] = [];
  addWarmupTimeframe(timeframes, { unit: bot.timeframe_unit, unitNumber: bot.timeframe_unit_number });
  addStrategyWarmupTimeframes(timeframes, bot, bot.strategy_type);
  return timeframes;
}

export function readSelectedBotConfigId(): number | null {
  try {
    if (typeof localStorage === "undefined") {
      return null;
    }
    const value = Number.parseInt(localStorage.getItem(SELECTED_BOT_STORAGE_KEY) ?? "", 10);
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function rememberSelectedBotConfigId(botConfigId: number | null): void {
  try {
    if (typeof localStorage === "undefined") {
      return;
    }
    if (botConfigId && Number.isFinite(botConfigId) && botConfigId > 0) {
      localStorage.setItem(SELECTED_BOT_STORAGE_KEY, String(Math.trunc(botConfigId)));
    } else {
      localStorage.removeItem(SELECTED_BOT_STORAGE_KEY);
    }
  } catch {
    // Selection persistence is only an optimization hint.
  }
}

function botConfigCacheKey(accountId?: number): string {
  return `${requestCacheScope()}:${accountId ?? "all"}`;
}

function invalidateBotConfigCaches() {
  botConfigsCacheByQuery.clear();
  inFlightBotConfigsByQuery.clear();
}

function botActivityCacheKey(botConfigId: number, limit: number): string {
  return `${requestCacheScope()}:${botConfigId}:${limit}`;
}

function invalidateBotActivityCaches(botConfigId?: number) {
  if (typeof botConfigId !== "number") {
    botActivityCacheByQuery.clear();
    inFlightBotActivityByQuery.clear();
    return;
  }
  const prefix = `${requestCacheScope()}:${botConfigId}:`;
  clearMapByPrefix(botActivityCacheByQuery, prefix);
  clearMapByPrefix(inFlightBotActivityByQuery, prefix);
}

function listBotConfigs(accountId?: number, options: BotConfigListOptions = {}): Promise<BotConfigListResponse> {
  const cacheKey = botConfigCacheKey(accountId);
  return getTimedCachedRequest({
    cache: botConfigsCacheByQuery,
    inFlight: inFlightBotConfigsByQuery,
    cacheKey,
    ttlMs: BOT_CONFIG_CACHE_TTL_MS,
    bypassCache: options.bypassCache,
    load: () =>
      requestJson<BotConfigListResponse>("/api/bots", {
        query: {
          account_id: accountId,
        },
      }),
  });
}

function getBotActivity(botConfigId: number, limit = 50): Promise<BotActivity> {
  const normalizedLimit = Math.max(1, Math.trunc(limit));
  const cacheKey = botActivityCacheKey(botConfigId, normalizedLimit);
  return getTimedCachedRequest({
    cache: botActivityCacheByQuery,
    inFlight: inFlightBotActivityByQuery,
    cacheKey,
    ttlMs: BOT_ACTIVITY_CACHE_TTL_MS,
    load: () =>
      requestJson<BotActivity>(`/api/bots/${botConfigId}/activity`, {
        query: {
          limit: normalizedLimit,
        },
      }),
  });
}

function normalizedCandleBoundary(value: string | undefined, bucketMs: number): string {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? String(Math.floor(parsed / bucketMs)) : value ?? "";
}

function botCandleCacheKey(query: CandleQuery): string {
  const unit = query.unit ?? "minute";
  const unitNumber = Math.max(1, Math.trunc(query.unitNumber ?? 5));
  const bucketMs = BOT_TIMEFRAME_SECONDS[unit] * unitNumber * 1_000;
  return [
    requestCacheScope(),
    query.contractId.trim(),
    query.symbol?.trim().toUpperCase() ?? "",
    query.live ?? false,
    unit,
    unitNumber,
    Math.max(1, Math.trunc(query.limit ?? 500)),
    query.includePartialBar ?? false,
    query.refresh ?? false,
    query.repair ?? false,
    normalizedCandleBoundary(query.start, bucketMs),
    normalizedCandleBoundary(query.end, bucketMs),
  ].join("|");
}

function requestBotCandles(query: CandleQuery, options: RequestSignalOptions = {}): Promise<ProjectXMarketCandle[]> {
  if (options.signal?.aborted) {
    return Promise.reject(abortError());
  }
  const request = (signal?: AbortSignal) =>
    requestJson<ProjectXMarketCandle[]>("/api/projectx/candles", {
      query: {
        contract_id: query.contractId,
        symbol: query.symbol,
        start: query.start,
        end: query.end,
        live: query.live ?? false,
        unit: query.unit ?? "minute",
        unit_number: query.unitNumber ?? 5,
        limit: query.limit ?? 500,
        include_partial_bar: query.includePartialBar ?? false,
        refresh: query.refresh ?? false,
        repair: query.repair ?? false,
      },
      signal,
    });

  const cacheKey = botCandleCacheKey(query);
  const canCache = !query.live && !query.includePartialBar && !query.refresh && !query.repair;
  const sharedRequest = canCache
    ? getTimedCachedRequest({
        cache: botCandlesCacheByQuery,
        inFlight: inFlightBotCandlesByQuery,
        cacheKey,
        ttlMs: BOT_CANDLE_CACHE_TTL_MS,
        load: () => request(),
      })
    : getSharedInFlightRequest(inFlightBotCandlesByQuery, cacheKey, () => request());
  return withConsumerAbort(sharedRequest, options.signal);
}

function buildBotWarmupCandleQuery(bot: BotConfig, timeframe: BotWarmupTimeframe, now = new Date()): CandleQuery {
  const unitNumber = Math.max(1, Math.trunc(timeframe.unitNumber));
  const timeframeSeconds = BOT_TIMEFRAME_SECONDS[timeframe.unit] * unitNumber;
  const lookbackBars = Math.max(1, Math.trunc(bot.lookback_bars));
  const limit = Math.min(BOT_CHART_MAX_BARS, Math.max(BOT_CHART_MIN_BARS, lookbackBars * 4));
  const end = Number.isFinite(now.getTime()) ? now : new Date();
  const start = new Date(end.getTime() - timeframeSeconds * limit * BOT_CHART_LOOKBACK_MULTIPLIER * 1_000);
  return {
    contractId: bot.contract_id,
    symbol: bot.symbol ?? undefined,
    start: start.toISOString(),
    end: end.toISOString(),
    live: false,
    unit: timeframe.unit,
    unitNumber,
    limit,
    includePartialBar: false,
    refresh: false,
  };
}

async function runSelectedBotWarmup(): Promise<void> {
  const configs = await listBotConfigs();
  if (configs.items.length === 0) {
    return;
  }
  const selectedId = readSelectedBotConfigId();
  const selectedBot = configs.items.find((config) => config.id === selectedId) ?? configs.items[0];
  const [configuredTimeframe, ...requiredTimeframes] = getBotWarmupTimeframes(selectedBot);
  if (!configuredTimeframe) {
    return;
  }

  await requestBotCandles(buildBotWarmupCandleQuery(selectedBot, configuredTimeframe)).catch(() => undefined);
  await Promise.allSettled(
    requiredTimeframes.map((timeframe) => requestBotCandles(buildBotWarmupCandleQuery(selectedBot, timeframe))),
  );
}

export function warmSelectedBot(): Promise<void> {
  if (selectedBotWarmupRequest) {
    return selectedBotWarmupRequest;
  }
  const request = runSelectedBotWarmup();
  selectedBotWarmupRequest = request;
  const clearWarmup = () => {
    if (selectedBotWarmupRequest === request) {
      selectedBotWarmupRequest = null;
    }
  };
  void request.then(clearWarmup, clearWarmup);
  return request;
}

interface MarketPriceStreamQuery {
  contractId: string;
  symbol?: string;
  throttleMs?: number;
}

interface MarketPriceStreamCallbacks {
  onPrice: (price: ProjectXMarketPrice) => void;
  onError?: (error: unknown) => void;
}

interface BotStartOptions {
  dryRun?: boolean;
  confirmLiveOrderRouting?: boolean;
  continuous?: boolean;
  pollIntervalSeconds?: number;
  stopAtSessionEnd?: boolean;
}

function botStartPayload(options: BotStartOptions = {}) {
  return {
    dry_run: options.dryRun,
    confirm_live_order_routing: options.confirmLiveOrderRouting ?? false,
    continuous: options.continuous,
    poll_interval_seconds: options.pollIntervalSeconds,
    stop_at_session_end: options.stopAtSessionEnd,
  };
}

export function streamProjectXMarketPrice(query: MarketPriceStreamQuery, callbacks: MarketPriceStreamCallbacks): () => void {
  const controller = new AbortController();
  let closed = false;

  void runProjectXMarketPriceStream(query, callbacks, controller.signal).catch((error) => {
    if (!closed && !isAbortError(error)) {
      callbacks.onError?.(error);
    }
  });

  return () => {
    closed = true;
    controller.abort();
  };
}

async function runProjectXMarketPriceStream(
  query: MarketPriceStreamQuery,
  callbacks: MarketPriceStreamCallbacks,
  signal: AbortSignal,
): Promise<void> {
  const accessToken = await getAccessToken();
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(
    buildUrl("/api/projectx/market-price/stream", {
      contract_id: query.contractId,
      symbol: query.symbol,
      throttle_ms: query.throttleMs ?? 250,
    }),
    {
      headers: Object.keys(headers).length === 0 ? undefined : headers,
      signal,
      cache: "no-store",
    },
  );

  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(detail || `Market price stream failed (${response.status} ${response.statusText})`, response.status, detail, detail);
  }
  if (!response.body) {
    throw new Error("Market price stream response did not include a body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const price = parseMarketPriceSseFrame(frame);
      if (price) {
        callbacks.onPrice(price);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function parseMarketPriceSseFrame(frame: string): ProjectXMarketPrice | null {
  let eventType = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }

    const separatorIndex = line.indexOf(":");
    const field = separatorIndex >= 0 ? line.slice(0, separatorIndex) : line;
    let value = separatorIndex >= 0 ? line.slice(separatorIndex + 1) : "";
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      eventType = value;
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  if (eventType !== "price" || dataLines.length === 0) {
    return null;
  }

  try {
    const parsed = JSON.parse(dataLines.join("\n"));
    return isProjectXMarketPrice(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function isProjectXMarketPrice(value: unknown): value is ProjectXMarketPrice {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<ProjectXMarketPrice>;
  return (
    typeof candidate.contract_id === "string" &&
    (candidate.symbol === null || typeof candidate.symbol === "string") &&
    typeof candidate.price === "number" &&
    Number.isFinite(candidate.price) &&
    typeof candidate.timestamp === "string"
  );
}

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}

async function runBacktestRequest(botConfigId: number, payload: BotBacktestInput): Promise<BotBacktestResult> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), BACKTEST_REQUEST_TIMEOUT_MS);
  try {
    return await requestJson<BotBacktestResult>(`/api/bots/${botConfigId}/backtest`, {
      method: "POST",
      body: payload,
      signal: controller.signal,
    });
  } catch (error) {
    if (isAbortError(error)) {
      throw new Error("Backtest timed out after 5 minutes. Try again after the server finishes caching candles, or narrow the date range.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

export const botsApi = {
  searchContracts: (query: ContractSearchQuery) =>
    requestJson<ProjectXContract[]>("/api/projectx/contracts/search", {
      query: {
        search_text: query.searchText,
        live: query.live ?? false,
      },
    }),
  getCandles: requestBotCandles,
  listConfigs: listBotConfigs,
  createConfig: (payload: BotConfigInput) =>
    requestJson<BotConfig>("/api/bots", {
      method: "POST",
      body: payload,
    }).then((config) => {
      invalidateBotConfigCaches();
      return config;
    }),
  updateConfig: (botConfigId: number, payload: BotConfigUpdateInput) =>
    requestJson<BotConfig>(`/api/bots/${botConfigId}`, {
      method: "PATCH",
      body: payload,
    }).then((config) => {
      invalidateBotConfigCaches();
      invalidateBotActivityCaches(botConfigId);
      return config;
    }),
  deleteConfig: (botConfigId: number) =>
    requestJson<void>(`/api/bots/${botConfigId}`, {
      method: "DELETE",
    }).then((result) => {
      invalidateBotConfigCaches();
      invalidateBotActivityCaches(botConfigId);
      if (readSelectedBotConfigId() === botConfigId) {
        rememberSelectedBotConfigId(null);
      }
      return result;
    }),
  start: (botConfigId: number, options: BotStartOptions = {}) =>
    requestJson<BotEvaluation>(`/api/bots/${botConfigId}/start`, {
      method: "POST",
      body: botStartPayload(options),
    }).then((evaluation) => {
      invalidateBotActivityCaches(botConfigId);
      return evaluation;
    }),
  evaluate: (botConfigId: number, options: BotStartOptions = { dryRun: true }) =>
    requestJson<BotEvaluation>(`/api/bots/${botConfigId}/evaluate`, {
      method: "POST",
      body: botStartPayload(options),
    }).then((evaluation) => {
      invalidateBotActivityCaches(botConfigId);
      return evaluation;
    }),
  runBacktest: runBacktestRequest,
  stop: (botConfigId: number) =>
    requestJson<BotEvaluation["run"]>(`/api/bots/${botConfigId}/stop`, {
      method: "POST",
    }).then((run) => {
      invalidateBotActivityCaches(botConfigId);
      return run;
    }),
  getActivity: getBotActivity,
  warmSelected: warmSelectedBot,
  evaluateTradePlan: (payload: TradePlanEvaluationInput) =>
    requestJson<TradeEvaluationResult>("/api/trade-plan/evaluate", {
      method: "POST",
      body: payload,
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
