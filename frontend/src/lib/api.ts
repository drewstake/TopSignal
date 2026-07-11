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
  BotBacktestProgress,
  BotBacktestResult,
  BotConfig,
  BotConfigInput,
  BotConfigListResponse,
  BotConfigUpdateInput,
  BotEvaluation,
  BotTimeframeUnit,
  ProjectXContract,
  ProjectXMarketCandle,
  ProjectXMarketDepthSnapshot,
  ProjectXMarketDepthState,
  ProjectXMarketDepthUpdate,
  ProjectXMarketPrice,
  TradeEvaluationResult,
  TradePlanEvaluationInput,
} from "./types";
import { dispatchAccountDisplayNameUpdated } from "./accountSelection";
import { isDemoModeEnabled, sanitizeDemoApiResponse } from "./demoMode";
import { ENABLE_PERF_LOGS, logPerfInfo } from "./perf";
import { getAccessToken } from "./supabase";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const ACCOUNTS_CACHE_TTL_MS = 10 * 60_000;
const ACCOUNT_READ_CACHE_TTL_MS = 10 * 60_000;
const BACKTEST_REQUEST_TIMEOUT_MS = 15 * 60_000;

type QueryValue = string | number | boolean | null | undefined;

interface RequestJsonOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
  /** Reuse the token captured while selecting a user-scoped cache lane. */
  accessTokenOverride?: string | null;
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

interface UserScopedTimedCachedRequestOptions<T> extends Omit<TimedCachedRequestOptions<T>, "cacheKey" | "load"> {
  cacheKey: string;
  load: (accessToken: string | null) => Promise<T>;
}

interface RequestAuthContext {
  accessToken: string | null;
  cacheScope: string;
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
  const clearIfCurrent = () => {
    if (inFlight.get(cacheKey) === request) {
      inFlight.delete(cacheKey);
    }
  };
  // Avoid a detached `finally()` promise: if `request` rejects, that child
  // promise would otherwise surface an unhandled rejection even when the
  // caller handles the original request.
  void request.then(clearIfCurrent, clearIfCurrent);

  return request;
}

async function getUserScopedTimedCachedRequest<T>(options: UserScopedTimedCachedRequestOptions<T>): Promise<T> {
  const auth = await getRequestAuthContext();
  return getTimedCachedRequest({
    ...options,
    cacheKey: `${options.cacheKey}|scope:${auth.cacheScope}`,
    load: () => options.load(auth.accessToken),
  });
}

/**
 * Stable, non-secret namespace for browser caches. Supabase access tokens are
 * JWTs, so issuer + subject remain stable across token refreshes without ever
 * storing the credential itself. Opaque-token fallback is intentionally
 * session-scoped rather than exposing or persisting the token.
 */
export async function getAuthenticatedCacheScope(): Promise<string> {
  return (await getRequestAuthContext()).cacheScope;
}

async function getRequestAuthContext(): Promise<RequestAuthContext> {
  if (isDemoModeEnabled()) {
    return { accessToken: null, cacheScope: "demo" };
  }

  const accessToken = await getAccessToken();
  if (!accessToken) {
    return { accessToken: null, cacheScope: "anonymous" };
  }

  return {
    accessToken,
    cacheScope: getJwtUserScope(accessToken) ?? await getOpaqueTokenScope(accessToken),
  };
}

function getJwtUserScope(accessToken: string): string | null {
  const payload = accessToken.split(".")[1];
  if (!payload) {
    return null;
  }

  try {
    const normalized = payload.replaceAll("-", "+").replaceAll("_", "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const decoded = JSON.parse(globalThis.atob(padded)) as { iss?: unknown; sub?: unknown };
    if (typeof decoded.sub !== "string" || decoded.sub.trim() === "") {
      return null;
    }
    const issuer = typeof decoded.iss === "string" ? decoded.iss : "supabase";
    return `user:${encodeURIComponent(issuer)}:${encodeURIComponent(decoded.sub)}`;
  } catch {
    return null;
  }
}

async function getOpaqueTokenScope(accessToken: string): Promise<string> {
  if (globalThis.crypto?.subtle) {
    const bytes = new TextEncoder().encode(accessToken);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    const fingerprint = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
    return `opaque:${fingerprint}`;
  }

  // Legacy-browser fallback. This never persists the credential itself; two
  // independent 32-bit accumulators make accidental cross-user collisions
  // vanishingly unlikely until Web Crypto is available.
  let left = 0x811c9dc5;
  let right = 0x9e3779b9;
  for (let index = 0; index < accessToken.length; index += 1) {
    const value = accessToken.charCodeAt(index);
    left = Math.imul(left ^ value, 0x01000193) >>> 0;
    right = Math.imul(right ^ value, 0x85ebca6b) >>> 0;
  }
  return `opaque:${left.toString(16).padStart(8, "0")}${right.toString(16).padStart(8, "0")}`;
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
  const { method = "GET", query, body, signal, accessTokenOverride } = options;
  if (method !== "GET" && isDemoModeEnabled()) {
    throw new ApiError("Demo mode is read-only. Turn it off to sync or save changes.", 409, null, null);
  }
  if (method === "GET" && isDemoModeEnabled()) {
    const { getDemoApiResponse } = await import("./demoData");
    const demoResponse = getDemoApiResponse<T>(path, query);
    if (demoResponse) {
      return demoResponse.data;
    }
  }
  const accessToken = accessTokenOverride === undefined ? await getAccessToken() : accessTokenOverride;
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

interface BacktestRequestOptions extends RequestSignalOptions {
  onProgress?: (progress: BotBacktestProgress) => void;
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
  return getUserScopedTimedCachedRequest({
    cache: accountsCacheByQuery,
    inFlight: inFlightAccountsByQuery,
    cacheKey,
    ttlMs: ACCOUNTS_CACHE_TTL_MS,
    bypassCache: options.bypassCache,
    load: (accessToken) =>
      requestJson<AccountInfo[]>("/api/accounts", {
        query: {
          show_inactive: options.showInactive,
          show_missing: options.showMissing,
        },
        accessTokenOverride: accessToken,
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
    return getUserScopedTimedCachedRequest({
      cache: accountTradesCacheByQuery,
      inFlight: inFlightAccountTradesByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: (accessToken) =>
        requestJson<AccountTrade[]>(`/api/accounts/${accountId}/trades`, {
          query: requestQuery,
          accessTokenOverride: accessToken,
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
    return getUserScopedTimedCachedRequest({
      cache: accountSummaryCacheByQuery,
      inFlight: inFlightAccountSummaryByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: (accessToken) =>
        requestJson<AccountSummary>(`/api/accounts/${accountId}/summary`, {
          query: requestQuery,
          accessTokenOverride: accessToken,
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
    return getUserScopedTimedCachedRequest({
      cache: accountSummaryWithPointBasesCacheByQuery,
      inFlight: inFlightAccountSummaryWithPointBasesByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: (accessToken) =>
        requestJson<AccountSummaryWithPointBases>(`/api/accounts/${accountId}/summary-with-point-bases`, {
          query: requestQuery,
          accessTokenOverride: accessToken,
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
    return getUserScopedTimedCachedRequest({
      cache: accountPnlCalendarCacheByQuery,
      inFlight: inFlightAccountPnlCalendarByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      bypassCache: Boolean(query.refresh),
      load: (accessToken) =>
        requestJson<AccountPnlCalendarDay[]>(`/api/accounts/${accountId}/pnl-calendar`, {
          query: requestQuery,
          accessTokenOverride: accessToken,
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
    return getUserScopedTimedCachedRequest({
      cache: accountJournalDaysCacheByQuery,
      inFlight: inFlightAccountJournalDaysByQuery,
      cacheKey,
      ttlMs: ACCOUNT_READ_CACHE_TTL_MS,
      load: (accessToken) =>
        requestJson<JournalDaysResponse>(`/api/accounts/${accountId}/journal/days`, {
          query: requestQuery,
          accessTokenOverride: accessToken,
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

export interface CandleQuery {
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

const CANDLE_UNIT_MS: Record<BotTimeframeUnit, number> = {
  second: 1_000,
  minute: 60_000,
  hour: 60 * 60_000,
  day: 24 * 60 * 60_000,
  week: 7 * 24 * 60 * 60_000,
  month: 31 * 24 * 60 * 60_000,
};

function projectXCandleQueryParams(query: CandleQuery): Record<string, QueryValue> {
  return {
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
  };
}

/**
 * Stable identity for deduplicating equivalent candle reads. Closed-candle
 * ranges are bucketed to their chart interval: two callers within the same
 * active bar need the same closed history even if their `Date` values differ by
 * a few milliseconds. Partial/live reads retain exact timestamps.
 */
export function buildProjectXCandleRequestKey(query: CandleQuery): string {
  const params = projectXCandleQueryParams(query);
  if (!(query.includePartialBar ?? false)) {
    const unit = query.unit ?? "minute";
    const intervalMs = CANDLE_UNIT_MS[unit] * Math.max(1, Math.trunc(query.unitNumber ?? 5));
    params.start = normalizeCandleRequestTimestamp(query.start, intervalMs);
    params.end = normalizeCandleRequestTimestamp(query.end, intervalMs);
  }
  params.contract_id = query.contractId.trim().toUpperCase();
  params.symbol = query.symbol?.trim().toUpperCase();
  return `projectx-candles:${toSortedQueryCacheKey(params)}`;
}

export function buildUserScopedProjectXCandleRequestKey(cacheScope: string, query: CandleQuery): string {
  return `scope:${encodeURIComponent(cacheScope)}|${buildProjectXCandleRequestKey(query)}`;
}

function normalizeCandleRequestTimestamp(value: string | undefined, intervalMs: number): string | undefined {
  if (!value) {
    return value;
  }
  const timestampMs = Date.parse(value);
  if (!Number.isFinite(timestampMs) || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return value;
  }
  return new Date(Math.floor(timestampMs / intervalMs) * intervalMs).toISOString();
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
  const guardedCallbacks: MarketPriceStreamCallbacks = {
    ...callbacks,
    onPrice: (price) => {
      if (!closed) {
        callbacks.onPrice(price);
      }
    },
  };

  void runProjectXMarketPriceStream(query, guardedCallbacks, controller.signal).catch((error) => {
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
      if (price && marketPriceMatchesStreamQuery(price, query)) {
        callbacks.onPrice(price);
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function marketPriceMatchesStreamQuery(price: ProjectXMarketPrice, query: MarketPriceStreamQuery): boolean {
  if (query.symbol && price.symbol) {
    return price.symbol.trim().toUpperCase() === query.symbol.trim().toUpperCase();
  }
  return price.contract_id.trim().toUpperCase() === query.contractId.trim().toUpperCase();
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

export interface MarketDepthStreamQuery {
  contractId: string;
}

export interface MarketDepthStreamCallbacks {
  onState: (state: ProjectXMarketDepthState) => void;
  onSnapshot: (snapshot: ProjectXMarketDepthSnapshot) => void;
  onUpdate: (update: ProjectXMarketDepthUpdate) => void;
}

export type ProjectXMarketDepthSseEvent =
  | { event: "state"; data: ProjectXMarketDepthState }
  | { event: "snapshot"; data: ProjectXMarketDepthSnapshot }
  | { event: "update"; data: ProjectXMarketDepthUpdate };

const MARKET_DEPTH_RECONNECT_MIN_MS = 500;
const MARKET_DEPTH_RECONNECT_MAX_MS = 10_000;

class MarketDepthSequenceGapError extends Error {
  constructor(previousSequence: number, nextSequence: number) {
    super(`Market depth sequence gap (${previousSequence} to ${nextSequence}).`);
    this.name = "MarketDepthSequenceGapError";
  }
}

/**
 * Opens one authenticated stream to TopSignal. Topstep credentials remain on
 * the server; closing this handle aborts the HTTP request so the backend can
 * release its reference-counted contract subscription.
 */
export function streamProjectXMarketDepth(
  query: MarketDepthStreamQuery,
  callbacks: MarketDepthStreamCallbacks,
): () => void {
  const controller = new AbortController();
  const expectedContractId = normalizeContractId(query.contractId);
  let closed = false;

  const guardedCallbacks: MarketDepthStreamCallbacks = {
    onState: (state) => {
      if (!closed && contractIdsMatch(state.contract_id, expectedContractId)) {
        callbacks.onState(state);
      }
    },
    onSnapshot: (snapshot) => {
      if (!closed && contractIdsMatch(snapshot.contract_id, expectedContractId)) {
        callbacks.onSnapshot(snapshot);
      }
    },
    onUpdate: (update) => {
      if (!closed && contractIdsMatch(update.contract_id, expectedContractId)) {
        callbacks.onUpdate(update);
      }
    },
  };

  if (!expectedContractId) {
    queueMicrotask(() => {
      guardedCallbacks.onState({ contract_id: "", state: "unavailable", message: "No contract selected." });
    });
  } else {
    void runProjectXMarketDepthStream(expectedContractId, guardedCallbacks, controller.signal);
  }

  return () => {
    closed = true;
    controller.abort();
  };
}

async function runProjectXMarketDepthStream(
  contractId: string,
  callbacks: MarketDepthStreamCallbacks,
  signal: AbortSignal,
): Promise<void> {
  let reconnectDelayMs = MARKET_DEPTH_RECONNECT_MIN_MS;

  while (!signal.aborted) {
    try {
      await readProjectXMarketDepthConnection(contractId, callbacks, signal, () => {
        reconnectDelayMs = MARKET_DEPTH_RECONNECT_MIN_MS;
      });
      if (signal.aborted) {
        return;
      }
      callbacks.onState({
        contract_id: contractId,
        state: "disconnected",
        message: "Market depth stream closed.",
      });
    } catch (error) {
      if (signal.aborted || isAbortError(error)) {
        return;
      }
      if (isTerminalMarketDepthStreamError(error)) {
        callbacks.onState({
          contract_id: contractId,
          state: "unavailable",
          message: marketDepthErrorMessage(error),
        });
        return;
      }
      callbacks.onState({
        contract_id: contractId,
        state: "disconnected",
        message: marketDepthErrorMessage(error),
      });
    }

    if (!(await waitForAbortableDelay(250, signal))) {
      return;
    }
    callbacks.onState({ contract_id: contractId, state: "reconnecting", message: "Reconnecting market depth…" });
    if (!(await waitForAbortableDelay(reconnectDelayMs, signal))) {
      return;
    }
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, MARKET_DEPTH_RECONNECT_MAX_MS);
  }
}

async function readProjectXMarketDepthConnection(
  contractId: string,
  callbacks: MarketDepthStreamCallbacks,
  signal: AbortSignal,
  onHealthy: () => void,
): Promise<void> {
  const accessToken = await getAccessToken();
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(buildUrl("/api/projectx/market-depth/stream", { contract_id: contractId }), {
    headers: Object.keys(headers).length === 0 ? undefined : headers,
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(
      detail || `Market depth stream failed (${response.status} ${response.statusText})`,
      response.status,
      detail,
      detail,
    );
  }
  if (!response.body) {
    throw new Error("Market depth stream response did not include a body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedEvent = false;
  let lastSequence: number | null = null;

  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) {
        return;
      }
      buffer += decoder.decode(value, { stream: true });
      // Normalizing after each append also handles CR/LF split across chunks.
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const parsed = parseProjectXMarketDepthSseFrame(frame);
        if (parsed && !receivedEvent) {
          receivedEvent = true;
          // Reset transport backoff only after the server produced a valid event;
          // merely opening HTTP does not prove the upstream depth path is healthy.
          onHealthy();
        }
        if (parsed?.event === "state") {
          callbacks.onState(parsed.data);
        } else if (parsed?.event === "snapshot") {
          // Every snapshot is authoritative, including reset/reconnect snapshots
          // whose server-side sequence epoch may be lower than the prior one.
          lastSequence = parsed.data.sequence;
          callbacks.onSnapshot(parsed.data);
        } else if (parsed?.event === "update") {
          const nextSequence = parsed.data.sequence;
          if (nextSequence !== null && lastSequence !== null) {
            if (nextSequence > lastSequence + 1) {
              throw new MarketDepthSequenceGapError(lastSequence, nextSequence);
            }
            if (nextSequence <= lastSequence) {
              boundary = buffer.indexOf("\n\n");
              continue;
            }
          }
          // Once an unsequenced delta appears, continuity cannot be inferred
          // again until a sequenced update or authoritative snapshot arrives.
          lastSequence = nextSequence;
          callbacks.onUpdate(parsed.data);
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } catch (error) {
    // A gap makes the remaining response unsafe. Canceling releases the old
    // body immediately before the outer loop establishes a snapshot-first stream.
    await reader.cancel(error instanceof Error ? error.message : undefined).catch(() => undefined);
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export function parseProjectXMarketDepthSseFrame(frame: string): ProjectXMarketDepthSseEvent | null {
  let eventType = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) {
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

  if (dataLines.length === 0 || !["state", "snapshot", "update"].includes(eventType)) {
    return null;
  }
  try {
    const value: unknown = JSON.parse(dataLines.join("\n"));
    if (eventType === "state") {
      const state = parseMarketDepthState(value);
      return state ? { event: "state", data: state } : null;
    }
    if (eventType === "snapshot") {
      const snapshot = parseMarketDepthSnapshot(value);
      return snapshot ? { event: "snapshot", data: snapshot } : null;
    }
    const update = parseMarketDepthUpdate(value);
    return update ? { event: "update", data: update } : null;
  } catch {
    return null;
  }
}

function parseMarketDepthState(value: unknown): ProjectXMarketDepthState | null {
  const candidate = asUnknownRecord(value);
  if (!candidate || typeof candidate.contract_id !== "string") {
    return null;
  }
  const state = candidate.state;
  if (!isMarketDepthConnectionState(state)) {
    return null;
  }
  const message = candidate.message;
  if (message !== undefined && message !== null && typeof message !== "string") {
    return null;
  }
  return { contract_id: candidate.contract_id, state, message: message ?? null };
}

function parseMarketDepthSnapshot(value: unknown): ProjectXMarketDepthSnapshot | null {
  const candidate = asUnknownRecord(value);
  if (!candidate || typeof candidate.contract_id !== "string" || !Array.isArray(candidate.bids) || !Array.isArray(candidate.asks)) {
    return null;
  }
  const sequence = parseOptionalSequence(candidate.sequence);
  const timestamp = parseOptionalTimestamp(candidate.timestamp);
  if (sequence === undefined || timestamp === undefined) {
    return null;
  }
  return {
    contract_id: candidate.contract_id,
    sequence,
    timestamp,
    bids: candidate.bids.flatMap((level) => {
      const parsed = parseMarketDepthLevel(level);
      return parsed ? [parsed] : [];
    }),
    asks: candidate.asks.flatMap((level) => {
      const parsed = parseMarketDepthLevel(level);
      return parsed ? [parsed] : [];
    }),
    reset: candidate.reset === true || undefined,
  };
}

function parseMarketDepthUpdate(value: unknown): ProjectXMarketDepthUpdate | null {
  const candidate = asUnknownRecord(value);
  if (!candidate || typeof candidate.contract_id !== "string" || (candidate.side !== "bid" && candidate.side !== "ask")) {
    return null;
  }
  const price = finiteNumber(candidate.price);
  const size = finiteNumber(candidate.size ?? candidate.volume);
  const sequence = parseOptionalSequence(candidate.sequence);
  const timestamp = parseOptionalTimestamp(candidate.timestamp);
  if (price === null || size === null || size < 0 || sequence === undefined || timestamp === undefined) {
    return null;
  }
  return { contract_id: candidate.contract_id, sequence, timestamp, side: candidate.side, price, size };
}

function parseMarketDepthLevel(value: unknown): ProjectXMarketDepthSnapshot["bids"][number] | null {
  const candidate = asUnknownRecord(value);
  if (!candidate) {
    return null;
  }
  const price = finiteNumber(candidate.price);
  const size = finiteNumber(candidate.size ?? candidate.volume);
  return price === null || size === null || size < 0 ? null : { price, size };
}

function parseOptionalSequence(value: unknown): number | null | undefined {
  if (value === null || value === undefined) {
    return null;
  }
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function parseOptionalTimestamp(value: unknown): string | null | undefined {
  if (value === null || value === undefined) {
    return null;
  }
  return typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : undefined;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asUnknownRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function isMarketDepthConnectionState(value: unknown): value is ProjectXMarketDepthState["state"] {
  return (
    value === "connected" ||
    value === "disconnected" ||
    value === "reconnecting" ||
    value === "unavailable"
  );
}

function contractIdsMatch(candidate: string, expected: string): boolean {
  return normalizeContractId(candidate) === expected;
}

function normalizeContractId(contractId: string): string {
  return contractId.trim().toUpperCase();
}

function isTerminalMarketDepthStreamError(error: unknown): boolean {
  return error instanceof ApiError && (
    (error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) ||
    error.status === 500 ||
    error.status === 503
  );
}

function marketDepthErrorMessage(error: unknown): string {
  if (error instanceof ApiError && typeof error.body === "string") {
    try {
      const parsed: unknown = JSON.parse(error.body);
      const record = asUnknownRecord(parsed);
      if (record && typeof record.detail === "string" && record.detail.trim()) {
        return record.detail;
      }
    } catch {
      // Plain-text API errors already have a suitable Error.message below.
    }
  }
  return error instanceof Error && error.message.trim() ? error.message : "Market depth connection failed.";
}

function waitForAbortableDelay(delayMs: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const timeoutId = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve(true);
    }, delayMs);
    const handleAbort = () => {
      globalThis.clearTimeout(timeoutId);
      resolve(false);
    };
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}

interface ParsedBacktestStreamEvent {
  event: string;
  data: unknown;
}

export function parseBacktestSseFrame(frame: string): ParsedBacktestStreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line === "" || line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) as unknown };
  } catch {
    return null;
  }
}

function parseBacktestProgress(value: unknown): BotBacktestProgress | null {
  const record = asUnknownRecord(value);
  if (!record) {
    return null;
  }
  const phase = record.phase;
  if (
    phase !== "preparing"
    && phase !== "loading"
    && phase !== "replaying"
    && phase !== "finalizing"
    && phase !== "complete"
  ) {
    return null;
  }
  const optionalNumber = (candidate: unknown): number | null => (
    typeof candidate === "number" && Number.isFinite(candidate) ? candidate : null
  );
  const percent = optionalNumber(record.percent);
  const remainingPercent = optionalNumber(record.remaining_percent);
  return {
    phase,
    completed: optionalNumber(record.completed),
    total: optionalNumber(record.total),
    percent: percent === null ? null : Math.max(0, Math.min(100, Math.round(percent))),
    remaining_percent: remainingPercent === null
      ? null
      : Math.max(0, Math.min(100, Math.round(remainingPercent))),
  };
}

async function runBacktestStream(
  botConfigId: number,
  payload: BotBacktestInput,
  signal: AbortSignal,
  onProgress: (progress: BotBacktestProgress) => void,
): Promise<BotBacktestResult> {
  if (isDemoModeEnabled()) {
    throw new ApiError("Demo mode is read-only. Turn it off to sync or save changes.", 409, null, null);
  }
  const path = `/api/bots/${botConfigId}/backtests`;
  const url = buildUrl(path);
  const accessToken = await getAccessToken();
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Content-Type": "application/json",
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal,
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    let detail = body || `Backtest request failed (${response.status} ${response.statusText})`;
    try {
      const parsed = asUnknownRecord(JSON.parse(body) as unknown);
      if (typeof parsed?.detail === "string") {
        detail = parsed.detail;
      }
    } catch {
      // Keep the response text fallback.
    }
    throw new ApiError(detail, response.status, body, detail);
  }
  if (!response.body) {
    throw new Error("Backtest progress response did not include a body.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result: BotBacktestResult | null = null;

  const handleFrame = (frame: string) => {
    const parsed = parseBacktestSseFrame(frame);
    if (!parsed) {
      return;
    }
    if (parsed.event === "progress") {
      const progress = parseBacktestProgress(parsed.data);
      if (progress) {
        onProgress(progress);
      }
      return;
    }
    if (parsed.event === "result") {
      result = parsed.data as BotBacktestResult;
      return;
    }
    if (parsed.event === "error") {
      const error = asUnknownRecord(parsed.data);
      const status = typeof error?.status === "number" ? error.status : 500;
      const detail = typeof error?.detail === "string"
        ? error.detail
        : "Backtest failed.";
      throw new ApiError(detail, status, parsed.data, error?.detail ?? null);
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.search(/\r?\n\r?\n/);
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      const separator = /^\r\n\r\n/.test(buffer.slice(boundary)) ? 4 : 2;
      buffer = buffer.slice(boundary + separator);
      handleFrame(frame);
      boundary = buffer.search(/\r?\n\r?\n/);
    }
  }
  if (buffer.trim()) {
    handleFrame(buffer);
  }
  if (!result) {
    throw new Error("Backtest progress stream ended before returning a result.");
  }
  return result;
}

async function runBacktestRequest(
  botConfigId: number,
  payload: BotBacktestInput,
  options: BacktestRequestOptions = {},
): Promise<BotBacktestResult> {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort();
  if (options.signal?.aborted) {
    abortFromCaller();
  } else {
    options.signal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const timeoutId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, BACKTEST_REQUEST_TIMEOUT_MS);
  try {
    if (options.onProgress) {
      return await runBacktestStream(
        botConfigId,
        payload,
        controller.signal,
        options.onProgress,
      );
    }
    return await requestJson<BotBacktestResult>(`/api/bots/${botConfigId}/backtests`, {
      method: "POST",
      body: payload,
      signal: controller.signal,
    });
  } catch (error) {
    if (isAbortError(error) && timedOut) {
      throw new Error("Full-history backtest timed out after 15 minutes. Try again after the server finishes preparing candles.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
    options.signal?.removeEventListener("abort", abortFromCaller);
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
  getCandles: (query: CandleQuery, options: RequestSignalOptions = {}) =>
    requestJson<ProjectXMarketCandle[]>("/api/projectx/candles", {
      query: projectXCandleQueryParams(query),
      signal: options.signal,
    }),
  listConfigs: (accountId?: number) =>
    requestJson<BotConfigListResponse>("/api/bots", {
      query: {
        account_id: accountId,
      },
    }),
  listConfigsWithCacheScope: async (accountId?: number) => {
    const auth = await getRequestAuthContext();
    const configs = await requestJson<BotConfigListResponse>("/api/bots", {
      query: {
        account_id: accountId,
      },
      accessTokenOverride: auth.accessToken,
    });
    return { configs, cacheScope: auth.cacheScope };
  },
  createConfig: (payload: BotConfigInput) =>
    requestJson<BotConfig>("/api/bots", {
      method: "POST",
      body: payload,
    }),
  updateConfig: (botConfigId: number, payload: BotConfigUpdateInput) =>
    requestJson<BotConfig>(`/api/bots/${botConfigId}`, {
      method: "PATCH",
      body: payload,
    }),
  deleteConfig: (botConfigId: number) =>
    requestJson<void>(`/api/bots/${botConfigId}`, {
      method: "DELETE",
    }),
  start: (botConfigId: number, options: BotStartOptions = {}) =>
    requestJson<BotEvaluation>(`/api/bots/${botConfigId}/start`, {
      method: "POST",
      body: botStartPayload(options),
    }),
  evaluate: (botConfigId: number, options: BotStartOptions = { dryRun: true }) =>
    requestJson<BotEvaluation>(`/api/bots/${botConfigId}/evaluate`, {
      method: "POST",
      body: botStartPayload(options),
    }),
  runBacktest: runBacktestRequest,
  stop: (botConfigId: number) =>
    requestJson<BotEvaluation["run"]>(`/api/bots/${botConfigId}/stop`, {
      method: "POST",
    }),
  getActivity: (botConfigId: number, limit = 50, options: RequestSignalOptions = {}) =>
    requestJson<BotActivity>(`/api/bots/${botConfigId}/activity`, {
      query: {
        limit,
      },
      signal: options.signal,
    }),
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
