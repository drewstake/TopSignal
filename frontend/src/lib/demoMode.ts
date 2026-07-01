import { useEffect, useState } from "react";

export const DEMO_MODE_CHANGED_EVENT = "topsignal:demo-mode-changed";

const DEMO_MODE_STORAGE_KEY = "topsignal.demoMode";
const DEMO_USER_EMAIL = "demo@topsignal.local";
const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);
const FALSE_VALUES = new Set(["0", "false", "no", "off"]);

type JsonObject = Record<string, unknown>;

interface DemoModeChangeDetail {
  enabled: boolean;
}

function parseBooleanLike(value: unknown): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value !== "string") {
    return null;
  }
  const normalized = value.trim().toLowerCase();
  if (TRUE_VALUES.has(normalized)) {
    return true;
  }
  if (FALSE_VALUES.has(normalized)) {
    return false;
  }
  return null;
}

function getEnvDefault() {
  return parseBooleanLike(import.meta.env.VITE_DEMO_MODE) ?? false;
}

function getStoredPreference(): boolean | null {
  try {
    if (typeof localStorage === "undefined") {
      return null;
    }
    return parseBooleanLike(localStorage.getItem(DEMO_MODE_STORAGE_KEY));
  } catch {
    return null;
  }
}

export function isDemoModeEnabled(): boolean {
  return getStoredPreference() ?? getEnvDefault();
}

function emitDemoModeChanged(enabled: boolean) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }

  if (typeof CustomEvent === "function") {
    window.dispatchEvent(
      new CustomEvent<DemoModeChangeDetail>(DEMO_MODE_CHANGED_EVENT, {
        detail: { enabled },
      }),
    );
    return;
  }

  if (typeof Event === "function") {
    window.dispatchEvent(new Event(DEMO_MODE_CHANGED_EVENT));
  }
}

export function setDemoModeEnabled(enabled: boolean) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(DEMO_MODE_STORAGE_KEY, enabled ? "true" : "false");
    }
  } catch {
    // The app can still update in-memory state even when browser storage is unavailable.
  }
  emitDemoModeChanged(enabled);
}

export function useDemoMode() {
  const [enabled, setEnabled] = useState(() => isDemoModeEnabled());

  useEffect(() => {
    function handleDemoModeChanged(event: Event) {
      const nextEnabled = (event as CustomEvent<DemoModeChangeDetail>).detail?.enabled;
      setEnabled(typeof nextEnabled === "boolean" ? nextEnabled : isDemoModeEnabled());
    }

    if (typeof window === "undefined") {
      return undefined;
    }

    window.addEventListener(DEMO_MODE_CHANGED_EVENT, handleDemoModeChanged);
    return () => {
      window.removeEventListener(DEMO_MODE_CHANGED_EVENT, handleDemoModeChanged);
    };
  }, []);

  return {
    enabled,
    setEnabled: setDemoModeEnabled,
  };
}

function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function demoOrdinal(seed: string | number | null | undefined) {
  const hash = stableHash(String(seed ?? "demo"));
  return (hash % 90) + 10;
}

export function getDemoAccountName(account: { id: number; name: string }) {
  if (!isDemoModeEnabled()) {
    return account.name;
  }
  return `Demo Account ${demoOrdinal(account.id)}`;
}

export function getDemoAccountId(accountId: string | number | null | undefined) {
  if (!isDemoModeEnabled()) {
    return accountId === null || accountId === undefined ? "" : String(accountId);
  }
  return `ACCT-${demoOrdinal(accountId)}${stableHash(String(accountId ?? "demo")) % 100}`;
}

export function getDemoAccountLabel(account: { id: number; name: string }) {
  return `${getDemoAccountName(account)} (${getDemoAccountId(account.id)})`;
}

export function getDemoUserEmail(email: string | null | undefined) {
  if (!isDemoModeEnabled()) {
    return email ?? "Signed in";
  }
  return DEMO_USER_EMAIL;
}

export function getDemoTradeId(value: string | number | null | undefined) {
  if (!isDemoModeEnabled()) {
    return value === null || value === undefined ? "" : String(value);
  }
  return `TRD-${(stableHash(String(value ?? "trade")) % 900000) + 100000}`;
}

export function formatDemoCurrency(value: number, formatter: (nextValue: number) => string) {
  if (!isDemoModeEnabled()) {
    return formatter(value);
  }
  return "$--";
}

export function formatDemoPnl(value: number, formatter: (nextValue: number) => string) {
  if (!isDemoModeEnabled()) {
    return formatter(value);
  }
  if (value > 0) {
    return "+$--";
  }
  if (value < 0) {
    return "-$--";
  }
  return "$--";
}

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeKey(key: string) {
  return key.replace(/[A-Z]/g, (match) => `_${match.toLowerCase()}`).toLowerCase();
}

function shouldSanitizeMoneyKey(key: string) {
  const normalized = normalizeKey(key);
  if (
    normalized.endsWith("_count") ||
    normalized.endsWith("_id") ||
    normalized.endsWith("_ratio") ||
    normalized.endsWith("_rate") ||
    normalized.endsWith("_percent") ||
    normalized.endsWith("_minutes") ||
    normalized.endsWith("_hours") ||
    normalized.endsWith("_days") ||
    normalized.endsWith("_score") ||
    normalized.includes("point")
  ) {
    return false;
  }

  return (
    normalized === "balance" ||
    normalized.endsWith("_balance") ||
    normalized === "amount" ||
    normalized.endsWith("_amount") ||
    normalized === "amount_cents" ||
    normalized.endsWith("_amount_cents") ||
    normalized === "fee" ||
    normalized === "fees" ||
    normalized.endsWith("_fee") ||
    normalized.endsWith("_fees") ||
    normalized === "pnl" ||
    normalized.endsWith("_pnl") ||
    normalized.includes("_pnl_") ||
    normalized === "gross" ||
    normalized === "net" ||
    normalized === "avg_win" ||
    normalized === "avg_loss" ||
    normalized === "average_win" ||
    normalized === "average_loss" ||
    normalized === "largest_win" ||
    normalized === "largest_loss" ||
    normalized === "expectancy" ||
    normalized === "expectancy_per_trade" ||
    normalized === "profit_per_day" ||
    normalized === "efficiency_per_hour" ||
    normalized === "tail_risk_5pct" ||
    normalized === "max_daily_loss" ||
    normalized === "trailing_drawdown" ||
    normalized === "max_drawdown" ||
    normalized === "average_drawdown" ||
    normalized === "benchmark_diff" ||
    normalized === "benchmark_gross_pnl" ||
    normalized === "benchmark_net_pnl"
  );
}

function demoMoneyValue(value: number, seed: string) {
  if (!Number.isFinite(value) || value === 0) {
    return value;
  }

  const hash = stableHash(seed);
  const sign = value < 0 ? -1 : 1;
  const absoluteValue = Math.abs(value);
  const scale = 0.48 + (hash % 64) / 100;
  const offset = absoluteValue >= 100 ? hash % 375 : (hash % 45) / 10;
  const transformed = absoluteValue * scale + offset;
  const rounding = absoluteValue >= 1000 ? 25 : absoluteValue >= 100 ? 5 : 0.25;
  return sign * Math.round(transformed / rounding) * rounding;
}

function sanitizeMoneyNumber(key: string, value: number, path: string) {
  if (!shouldSanitizeMoneyKey(key)) {
    return value;
  }
  if (normalizeKey(key).endsWith("_amount_cents")) {
    return Math.round(demoMoneyValue(value / 100, `${path}.${key}`) * 100);
  }
  return demoMoneyValue(value, `${path}.${key}`);
}

function looksLikeAccountInfo(value: JsonObject) {
  return (
    typeof value.id === "number" &&
    typeof value.name === "string" &&
    "balance" in value &&
    "account_state" in value &&
    "provider_name" in value
  );
}

function looksLikeJournalEntry(value: JsonObject) {
  return (
    typeof value.id === "number" &&
    typeof value.account_id === "number" &&
    typeof value.entry_date === "string" &&
    typeof value.title === "string" &&
    typeof value.body === "string"
  );
}

function looksLikeBotConfig(value: JsonObject) {
  return typeof value.id === "number" && typeof value.name === "string" && typeof value.strategy_type === "string";
}

function sanitizeKnownObject(value: JsonObject): JsonObject {
  if (looksLikeAccountInfo(value)) {
    const demoName = `Demo Account ${demoOrdinal(Number(value.id))}`;
    return {
      ...value,
      name: demoName,
      provider_name: demoName,
      custom_display_name: null,
    };
  }

  if (looksLikeJournalEntry(value)) {
    return {
      ...value,
      title: `Demo journal ${value.entry_date}`,
      tags: ["demo"],
      body: "Demo mode hides journal notes and recap text.",
    };
  }

  if (looksLikeBotConfig(value)) {
    return {
      ...value,
      name: `Demo Bot ${demoOrdinal(Number(value.id))}`,
    };
  }

  return value;
}

function sanitizeStringValue(key: string, value: string, path: string) {
  const normalized = normalizeKey(key);
  if (normalized === "email") {
    return DEMO_USER_EMAIL;
  }
  if (normalized === "username") {
    return "demo_user";
  }
  if (normalized === "source_trade_id" || normalized === "order_id" || normalized === "provider_order_id") {
    return getDemoTradeId(value);
  }
  if (normalized === "filename") {
    return `demo-file-${demoOrdinal(value)}.png`;
  }
  if (normalized === "description" && path.includes("expenses")) {
    return "Demo expense";
  }
  if (normalized === "notes" && path.includes("payout")) {
    return "Demo payout";
  }
  if (normalized === "recap_markdown") {
    return "Demo mode hides generated recap text.";
  }
  return value;
}

function sanitizeDemoValue(value: unknown, path: string, key = ""): unknown {
  if (Array.isArray(value)) {
    return value.map((item, index) => sanitizeDemoValue(item, `${path}[${index}]`));
  }

  if (!isObject(value)) {
    if (typeof value === "number") {
      return sanitizeMoneyNumber(key, value, path);
    }
    if (typeof value === "string") {
      return sanitizeStringValue(key, value, path);
    }
    return value;
  }

  const knownObject = sanitizeKnownObject(value);
  const output: JsonObject = {};
  for (const [childKey, childValue] of Object.entries(knownObject)) {
    output[childKey] = sanitizeDemoValue(childValue, `${path}.${childKey}`, childKey);
  }
  return output;
}

export function sanitizeDemoApiResponse<T>(path: string, data: T): T {
  if (!isDemoModeEnabled()) {
    return data;
  }
  return sanitizeDemoValue(data, path) as T;
}
