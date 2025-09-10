// src/services/lockout.js
import { getToken } from "../lib/storage";

const DEV_BASE = "/ts-userapi";
const PROD_BASE = import.meta.env.VITE_USER_API_BASE || "https://userapi.topstepx.com";
const USER_API_BASE = import.meta.env.DEV ? DEV_BASE : PROD_BASE;

export const LockoutType = {
  Unknown: 0,
  Personal: 1,
  WeeklyTradeLimit: 2,
  DailyTradeLimit: 3,
  TradeClock: 4,
};

function parseHHMM(v) {
  if (!v) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(v).trim());
  if (!m) return null;
  const hour = Number(m[1]), minute = Number(m[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return { hour, minute };
}

export function resolveUserId() {
  const envId = import.meta.env.VITE_TS_USER_ID;
  if (envId && /^\d+$/.test(envId)) return Number(envId);

  const cached = localStorage.getItem("tsx:userId");
  if (cached && /^\d+$/.test(cached)) return Number(cached);

  // Optional: last-ditch try from JWT
  try {
    const token = getToken?.();
    if (token && token.split(".").length === 3) {
      const payload = JSON.parse(atob(token.split(".")[1]));
      const candidates = [payload.userId, payload.uid, payload.nameid, payload.sub];
      const found = candidates.find((v) => v && /^\d+$/.test(String(v)));
      if (found) return Number(found);
    }
  } catch { /* ignore */ }
  return null;
}

/** Build payloads that match PersonalLockoutModel exactly */
export function buildTodayLockouts(
  userId,
  accountIds,
  opts = { hour: 23, minute: 59, reason: "TopSignal daily lockout", shouldLiquidate: false }
) {
  const { hour = 23, minute = 59, reason = "TopSignal daily lockout", shouldLiquidate = false } = opts;

  const now = new Date();
  const target = new Date(now);
  target.setHours(hour, minute, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 1);

  // Examples show arbitrary ISO times; we'll align tradeDay to start time day (UTC ISO)
  const createdAt = now.toISOString();
  const startsAt = createdAt;
  const expiresAt = target.toISOString();
  const tradeDay = startsAt;

  return accountIds.map((accountId) => ({
    tradingAccountId: Number(accountId),
    userId: Number(userId),
    reason,
    shouldLiquidate: Boolean(shouldLiquidate),
    createdAt,
    tradeDay,
    startsAt,
    expiresAt,
    type: LockoutType.Personal,
  }));
}

export function buildLockoutsForToday(accountIds, opts) {
  const userId = resolveUserId();
  if (!userId) {
    throw new Error("Missing userId. Set VITE_TS_USER_ID=12345 in .env.local or store 'tsx:userId' once.");
  }

  let finalOpts = { ...opts };
  if (finalOpts.hour == null && finalOpts.minute == null) {
    const envTime = parseHHMM(import.meta.env.VITE_LOCKOUT_UNTIL_HHMM);
    if (envTime) finalOpts = { ...finalOpts, ...envTime };
  }
  return buildTodayLockouts(userId, accountIds, finalOpts);
}

/** POST /PersonalLockout/add -> { id, message, success } */
export async function addPersonalLockouts(entries, { signal } = {}) {
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new Error("No lockout entries to submit.");
  }

  const token = getToken?.(); // optional
  const res = await fetch(`${USER_API_BASE}/PersonalLockout/add`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(entries),
    credentials: "include",
    mode: "cors",
    signal,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Lockout request failed (HTTP ${res.status}) ${text}`);
  }

  return res.json().catch(() => ({ success: true }));
}

/** GET /PersonalLockout/active/{tradingAccountId} */
export async function getActiveLockouts(accountId, { signal } = {}) {
  const res = await fetch(`${USER_API_BASE}/PersonalLockout/active/${Number(accountId)}`, {
    method: "GET",
    credentials: "include",
    mode: "cors",
    signal,
  });
  if (!res.ok) throw new Error(`Active lockouts fetch failed (HTTP ${res.status})`);
  return res.json();
}

/** GET /PersonalLockout/all?tradingAccountId= */
export async function getAllLockouts(accountId, { signal } = {}) {
  const url = new URL(`${USER_API_BASE}/PersonalLockout/all`, window.location.origin);
  if (accountId != null) url.searchParams.set("tradingAccountId", String(accountId));
  const full = `${url.pathname}${url.search}`; // keeps proxy base
  const res = await fetch(full, {
    method: "GET",
    credentials: "include",
    mode: "cors",
    signal,
  });
  if (!res.ok) throw new Error(`All lockouts fetch failed (HTTP ${res.status})`);
  return res.json();
}
