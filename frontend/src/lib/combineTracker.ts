import type { ExpensePlanSize } from "./types";

type CombinePlanSize = Extract<ExpensePlanSize, "50k" | "100k" | "150k">;

const COMBINE_PREFIX_BY_PLAN: Record<CombinePlanSize, string> = {
  "50k": "50KTC",
  "100k": "100KTC",
  "150k": "150KTC",
};

const COMBINE_SPEND_TRACKER_STORAGE_KEY = "topsignal.combineSpendTracker.v3";
const LEGACY_COMBINE_SPEND_TRACKER_START_DATE = "2026-03-01";
export const STANDARD_ACTIVATION_FEE_CENTS = 15_000;

export const COMBINE_PRICE_CENTS_BY_PLAN: Record<CombinePlanSize, number> = {
  "50k": 11_500,
  "100k": 16_800,
  "150k": 22_100,
};

export interface CombineSpendLedger {
  startedOn: string;
  startedAt: string;
  purchasesByAccountId: Record<string, CombinePurchaseEntry>;
  knownCombineAccountIds: Record<string, true>;
  baselineCaptured: boolean;
  standardActivationCount: number;
  syncedEvaluationExpenseAccountIds: Record<string, true>;
}

export interface CombineSpendSnapshot {
  startedOn: string;
  startedAt: string;
  countsByPlan: Record<CombinePlanSize, number>;
  totalTrackedCombines: number;
  baseCombineCostCents: number;
  standardActivationCount: number;
  standardActivationCostCents: number;
  totalCostCents: number;
}

export interface CombinePurchaseEntry {
  planSize: CombinePlanSize;
  purchasedOn: string;
}

export interface UnsyncedEvaluationExpensePurchase {
  accountId: number;
  planSize: CombinePlanSize;
  purchasedOn: string;
  amountCents: number;
}

export interface CombineTrackerSyncResult {
  snapshot: CombineSpendSnapshot;
  unsyncedEvaluationPurchases: UnsyncedEvaluationExpensePurchase[];
}

export interface CombineTrackerAccount {
  id: number;
  name: string;
  status?: string;
  account_state?: string;
}

function normalizeCombineTrackerAccountState(account: CombineTrackerAccount): string {
  const raw = account.status ?? account.account_state ?? "";
  return raw.trim().toUpperCase();
}

function isTrackableCombineTrackerAccount(account: CombineTrackerAccount): boolean {
  const state = normalizeCombineTrackerAccountState(account);
  return state === "ACTIVE" || state === "LOCKED_OUT";
}

export function getCombinePlanSizeFromAccountName(name: string): CombinePlanSize | null {
  const normalized = name.trim().toUpperCase();
  if (normalized.startsWith(COMBINE_PREFIX_BY_PLAN["50k"])) {
    return "50k";
  }
  if (normalized.startsWith(COMBINE_PREFIX_BY_PLAN["100k"])) {
    return "100k";
  }
  if (normalized.startsWith(COMBINE_PREFIX_BY_PLAN["150k"])) {
    return "150k";
  }
  return null;
}

export function createEmptyCombineSpendLedger(): CombineSpendLedger {
  return {
    startedOn: getTodayLocalIsoDate(),
    startedAt: getCurrentIsoTimestamp(),
    purchasesByAccountId: {},
    knownCombineAccountIds: {},
    baselineCaptured: false,
    standardActivationCount: 0,
    syncedEvaluationExpenseAccountIds: {},
  };
}

function getTodayLocalIsoDate(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function getCurrentIsoTimestamp(): string {
  return new Date().toISOString();
}

export function evolveCombineSpendLedger(
  ledger: CombineSpendLedger,
  accounts: CombineTrackerAccount[],
): CombineSpendLedger {
  const nextLedger: CombineSpendLedger = {
    startedOn: ledger.startedOn,
    startedAt: ledger.startedAt,
    purchasesByAccountId: { ...ledger.purchasesByAccountId },
    knownCombineAccountIds: { ...ledger.knownCombineAccountIds },
    baselineCaptured: ledger.baselineCaptured,
    standardActivationCount: ledger.standardActivationCount,
    syncedEvaluationExpenseAccountIds: { ...ledger.syncedEvaluationExpenseAccountIds },
  };

  for (const account of accounts) {
    // Keep temporarily locked combines tracked as still-active purchases.
    if (!isTrackableCombineTrackerAccount(account)) {
      continue;
    }
    const planSize = getCombinePlanSizeFromAccountName(account.name);
    if (planSize === null) {
      continue;
    }
    const accountId = String(account.id);
    nextLedger.knownCombineAccountIds[accountId] = true;

    const existingPurchase = nextLedger.purchasesByAccountId[accountId];
    if (existingPurchase === undefined) {
      nextLedger.purchasesByAccountId[accountId] = {
        planSize,
        purchasedOn: getTodayLocalIsoDate(),
      };
      continue;
    }

    if (existingPurchase.planSize !== planSize) {
      nextLedger.purchasesByAccountId[accountId] = {
        ...existingPurchase,
        planSize,
      };
    }
  }

  nextLedger.baselineCaptured = true;

  return nextLedger;
}

export function computeCombineSpendSnapshotFromLedger(ledger: CombineSpendLedger): CombineSpendSnapshot {
  const countsByPlan: Record<CombinePlanSize, number> = {
    "50k": 0,
    "100k": 0,
    "150k": 0,
  };

  for (const purchase of Object.values(ledger.purchasesByAccountId)) {
    countsByPlan[purchase.planSize] += 1;
  }

  const baseCombineCostCents =
    countsByPlan["50k"] * COMBINE_PRICE_CENTS_BY_PLAN["50k"] +
    countsByPlan["100k"] * COMBINE_PRICE_CENTS_BY_PLAN["100k"] +
    countsByPlan["150k"] * COMBINE_PRICE_CENTS_BY_PLAN["150k"];
  const standardActivationCostCents = ledger.standardActivationCount * STANDARD_ACTIVATION_FEE_CENTS;

  return {
    startedOn: ledger.startedOn,
    startedAt: ledger.startedAt,
    countsByPlan,
    totalTrackedCombines: countsByPlan["50k"] + countsByPlan["100k"] + countsByPlan["150k"],
    baseCombineCostCents,
    standardActivationCount: ledger.standardActivationCount,
    standardActivationCostCents,
    totalCostCents: baseCombineCostCents + standardActivationCostCents,
  };
}

function normalizeCombineSpendLedger(raw: unknown): CombineSpendLedger {
  const todayLocalIsoDate = getTodayLocalIsoDate();
  const nowIso = getCurrentIsoTimestamp();
  if (!raw || typeof raw !== "object") {
    return createEmptyCombineSpendLedger();
  }

  const candidate = raw as Record<string, unknown>;
  const rawStartedOn =
    typeof candidate.startedOn === "string" && candidate.startedOn.trim().length > 0
      ? candidate.startedOn
      : null;
  const normalizedStartedOn = isIsoDate(rawStartedOn) ? rawStartedOn : null;
  const rawStartedAt =
    typeof candidate.startedAt === "string" && candidate.startedAt.trim().length > 0 ? candidate.startedAt : null;
  const normalizedStartedAt = isIsoDateTime(rawStartedAt) ? rawStartedAt : null;

  const purchasesByAccountIdRaw =
    candidate.purchasesByAccountId && typeof candidate.purchasesByAccountId === "object"
      ? (candidate.purchasesByAccountId as Record<string, unknown>)
      : {};
  const purchasesByAccountId: Record<string, CombinePurchaseEntry> = {};
  for (const [accountId, rawPurchase] of Object.entries(purchasesByAccountIdRaw)) {
    if (rawPurchase === "50k" || rawPurchase === "100k" || rawPurchase === "150k") {
      purchasesByAccountId[accountId] = {
        planSize: rawPurchase,
        purchasedOn: normalizedStartedOn ?? todayLocalIsoDate,
      };
      continue;
    }

    if (!rawPurchase || typeof rawPurchase !== "object") {
      continue;
    }

    const purchase = rawPurchase as Partial<CombinePurchaseEntry>;
    if (purchase.planSize !== "50k" && purchase.planSize !== "100k" && purchase.planSize !== "150k") {
      continue;
    }
    purchasesByAccountId[accountId] = {
      planSize: purchase.planSize,
      purchasedOn: isIsoDate(purchase.purchasedOn) ? purchase.purchasedOn : normalizedStartedOn ?? todayLocalIsoDate,
    };
  }

  let earliestPurchaseDate: string | null = null;
  for (const purchase of Object.values(purchasesByAccountId)) {
    if (!isIsoDate(purchase.purchasedOn)) {
      continue;
    }
    if (earliestPurchaseDate === null || purchase.purchasedOn < earliestPurchaseDate) {
      earliestPurchaseDate = purchase.purchasedOn;
    }
  }

  let startedOn = earliestPurchaseDate ?? normalizedStartedOn ?? todayLocalIsoDate;
  let startedAt = normalizedStartedAt ?? (isIsoDate(startedOn) ? `${startedOn}T00:00:00.000Z` : nowIso);
  if (
    startedOn === LEGACY_COMBINE_SPEND_TRACKER_START_DATE &&
    Object.keys(purchasesByAccountId).length === 0
  ) {
    startedOn = todayLocalIsoDate;
    startedAt = nowIso;
  }

  const standardActivationCountRaw =
    typeof candidate.standardActivationCount === "number" ? Math.floor(candidate.standardActivationCount) : 0;
  const standardActivationCount = Number.isFinite(standardActivationCountRaw)
    ? Math.max(0, standardActivationCountRaw)
    : 0;

  const knownCombineAccountIdsRaw =
    candidate.knownCombineAccountIds && typeof candidate.knownCombineAccountIds === "object"
      ? (candidate.knownCombineAccountIds as Record<string, unknown>)
      : {};
  const knownCombineAccountIds: Record<string, true> = {};
  for (const [accountId, value] of Object.entries(knownCombineAccountIdsRaw)) {
    if (value === true) {
      knownCombineAccountIds[accountId] = true;
    }
  }
  for (const accountId of Object.keys(purchasesByAccountId)) {
    knownCombineAccountIds[accountId] = true;
  }

  const rawBaselineCaptured = candidate.baselineCaptured;
  const baselineCaptured =
    typeof rawBaselineCaptured === "boolean"
      ? rawBaselineCaptured
      : Object.keys(knownCombineAccountIds).length > 0 || Object.keys(purchasesByAccountId).length > 0;

  const syncedEvaluationExpenseAccountIdsRaw =
    candidate.syncedEvaluationExpenseAccountIds && typeof candidate.syncedEvaluationExpenseAccountIds === "object"
      ? (candidate.syncedEvaluationExpenseAccountIds as Record<string, unknown>)
      : {};
  const syncedEvaluationExpenseAccountIds: Record<string, true> = {};
  for (const [accountId, value] of Object.entries(syncedEvaluationExpenseAccountIdsRaw)) {
    if (value === true) {
      syncedEvaluationExpenseAccountIds[accountId] = true;
    }
  }

  return {
    startedOn,
    startedAt,
    purchasesByAccountId,
    knownCombineAccountIds,
    baselineCaptured,
    standardActivationCount,
    syncedEvaluationExpenseAccountIds,
  };
}

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function isIsoDateTime(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  return Number.isFinite(Date.parse(value));
}

function readCombineSpendLedger(): CombineSpendLedger {
  if (typeof window === "undefined") {
    return createEmptyCombineSpendLedger();
  }

  const rawValue = window.localStorage.getItem(COMBINE_SPEND_TRACKER_STORAGE_KEY);
  if (!rawValue) {
    return createEmptyCombineSpendLedger();
  }

  try {
    return normalizeCombineSpendLedger(JSON.parse(rawValue));
  } catch {
    return createEmptyCombineSpendLedger();
  }
}

function writeCombineSpendLedger(ledger: CombineSpendLedger): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(COMBINE_SPEND_TRACKER_STORAGE_KEY, JSON.stringify(ledger));
}

export function readCombineSpendSnapshot(): CombineSpendSnapshot {
  return computeCombineSpendSnapshotFromLedger(readCombineSpendLedger());
}

function listUnsyncedEvaluationExpensePurchases(
  ledger: CombineSpendLedger,
  accounts: CombineTrackerAccount[],
): UnsyncedEvaluationExpensePurchase[] {
  const unsynced: UnsyncedEvaluationExpensePurchase[] = [];
  const activeCombineAccountIds = new Set<string>();

  for (const account of accounts) {
    if (!isTrackableCombineTrackerAccount(account)) {
      continue;
    }
    if (getCombinePlanSizeFromAccountName(account.name) === null) {
      continue;
    }
    activeCombineAccountIds.add(String(account.id));
  }

  for (const [accountId, purchase] of Object.entries(ledger.purchasesByAccountId)) {
    if (!activeCombineAccountIds.has(accountId)) {
      continue;
    }
    if (ledger.syncedEvaluationExpenseAccountIds[accountId] === true) {
      continue;
    }
    unsynced.push({
      accountId: Number(accountId),
      planSize: purchase.planSize,
      purchasedOn: purchase.purchasedOn,
      amountCents: COMBINE_PRICE_CENTS_BY_PLAN[purchase.planSize],
    });
  }

  unsynced.sort((a, b) => a.accountId - b.accountId);
  return unsynced;
}

export function syncCombineSpendTracker(accounts: CombineTrackerAccount[]): CombineTrackerSyncResult {
  const current = readCombineSpendLedger();
  const next = evolveCombineSpendLedger(current, accounts);
  writeCombineSpendLedger(next);
  return {
    snapshot: computeCombineSpendSnapshotFromLedger(next),
    unsyncedEvaluationPurchases: listUnsyncedEvaluationExpensePurchases(next, accounts),
  };
}

export function markEvaluationExpensesSynced(accountIds: number[]): CombineSpendSnapshot {
  if (accountIds.length === 0) {
    return readCombineSpendSnapshot();
  }

  const current = readCombineSpendLedger();
  const next: CombineSpendLedger = {
    ...current,
    syncedEvaluationExpenseAccountIds: {
      ...current.syncedEvaluationExpenseAccountIds,
    },
  };

  for (const accountId of accountIds) {
    const accountIdKey = String(accountId);
    if (next.purchasesByAccountId[accountIdKey] === undefined) {
      continue;
    }
    next.syncedEvaluationExpenseAccountIds[accountIdKey] = true;
  }

  writeCombineSpendLedger(next);
  return computeCombineSpendSnapshotFromLedger(next);
}

export function incrementStandardActivationCount(increment = 1): CombineSpendSnapshot {
  if (!Number.isFinite(increment) || increment <= 0) {
    return readCombineSpendSnapshot();
  }

  const safeIncrement = Math.floor(increment);
  if (safeIncrement <= 0) {
    return readCombineSpendSnapshot();
  }

  const current = readCombineSpendLedger();
  const next: CombineSpendLedger = {
    ...current,
    standardActivationCount: current.standardActivationCount + safeIncrement,
  };
  writeCombineSpendLedger(next);
  return computeCombineSpendSnapshotFromLedger(next);
}

export function decrementStandardActivationCount(decrement = 1): CombineSpendSnapshot {
  if (!Number.isFinite(decrement) || decrement <= 0) {
    return readCombineSpendSnapshot();
  }

  const safeDecrement = Math.floor(decrement);
  if (safeDecrement <= 0) {
    return readCombineSpendSnapshot();
  }

  const current = readCombineSpendLedger();
  const next: CombineSpendLedger = {
    ...current,
    standardActivationCount: Math.max(0, current.standardActivationCount - safeDecrement),
  };
  writeCombineSpendLedger(next);
  return computeCombineSpendSnapshotFromLedger(next);
}
