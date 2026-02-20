import type {
  BehaviorMetrics,
  DayPnlPoint,
  HourPnlPoint,
  StreakMetrics,
  SummaryMetrics,
  SymbolPnlPoint,
  TradeRecord,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

type QueryValue = string | number | boolean | null | undefined;

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

async function requestJson<T>(path: string, query?: Record<string, QueryValue>): Promise<T> {
  const response = await fetch(buildUrl(path, query));
  if (!response.ok) {
    throw new Error(`Request failed (${response.status} ${response.statusText})`);
  }
  return (await response.json()) as T;
}

export const metricsApi = {
  getSummary: (accountId?: number) =>
    requestJson<SummaryMetrics>("/metrics/summary", { account_id: accountId }),
  getPnlByHour: (accountId?: number) =>
    requestJson<HourPnlPoint[]>("/metrics/pnl-by-hour", { account_id: accountId }),
  getPnlByDay: (accountId?: number) =>
    requestJson<DayPnlPoint[]>("/metrics/pnl-by-day", { account_id: accountId }),
  getPnlBySymbol: (accountId?: number) =>
    requestJson<SymbolPnlPoint[]>("/metrics/pnl-by-symbol", { account_id: accountId }),
  getStreaks: (accountId?: number) =>
    requestJson<StreakMetrics>("/metrics/streaks", { account_id: accountId }),
  getBehavior: (accountId?: number) =>
    requestJson<BehaviorMetrics>("/metrics/behavior", { account_id: accountId }),
  getTrades: (limit = 100, accountId?: number) =>
    requestJson<TradeRecord[]>("/trades", { limit, account_id: accountId }),
};
