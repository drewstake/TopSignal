import { getAccessToken } from "./supabase";
import type { AccountInfo, AccountTradeRefreshResult } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

type QueryValue = string | number | boolean | null | undefined;

interface RequestJsonOptions {
  method?: "GET" | "POST";
  query?: Record<string, QueryValue>;
}

function buildUrl(path: string, query?: Record<string, QueryValue>) {
  const url = new URL(path, API_BASE_URL);
  if (!query) {
    return url.toString();
  }

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
  return url.toString();
}

async function requestJson<T>(path: string, options: RequestJsonOptions = {}): Promise<T> {
  const { method = "GET", query } = options;
  const accessToken = await getAccessToken();
  const headers: Record<string, string> = {};
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(buildUrl(path, query), {
    method,
    headers: Object.keys(headers).length > 0 ? headers : undefined,
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status} ${response.statusText})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (body.detail !== undefined) {
        detail = JSON.stringify(body.detail);
      }
    } catch {
      // Keep the default HTTP error text when the response body is unavailable.
    }
    throw new Error(detail);
  }

  return (await response.json()) as T;
}

function isSelectableAccount(account: Pick<AccountInfo, "account_state">) {
  return account.account_state === "ACTIVE" || account.account_state === "LOCKED_OUT";
}

export async function getSelectableAccounts(): Promise<AccountInfo[]> {
  const accounts = await requestJson<AccountInfo[]>("/api/accounts", {
    query: {
      show_inactive: true,
      show_missing: false,
    },
  });
  return accounts.filter((account) => isSelectableAccount(account));
}

export function refreshTrades(accountId: number, query: { start?: string; end?: string } = {}) {
  return requestJson<AccountTradeRefreshResult>(`/api/accounts/${accountId}/trades/refresh`, {
    method: "POST",
    query: {
      start: query.start,
      end: query.end,
    },
  });
}
