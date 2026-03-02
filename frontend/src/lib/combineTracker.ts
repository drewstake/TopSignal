import type { ExpensePlanSize } from "./types";

type CombinePlanSize = Extract<ExpensePlanSize, "50k" | "100k" | "150k">;

const COMBINE_PREFIX_BY_PLAN: Record<CombinePlanSize, string> = {
  "50k": "50KTC",
  "100k": "100KTC",
  "150k": "150KTC",
};

const COMBINE_SPEND_TRACKER_STORAGE_KEY = "topsignal.combineSpendTracker.v1";
const COMBINE_SPEND_TRACKER_START_DATE = "2026-03-01";
export const STANDARD_ACTIVATION_FEE_CENTS = 15_000;

export const COMBINE_PRICE_CENTS_BY_PLAN: Record<CombinePlanSize, number> = {
  "50k": 11_500,
  "100k": 16_800,
  "150k": 22_100,
};

export interface CombineSpendLedger {
  startedOn: string;
  purchasesByAccountId: Record<string, CombinePurchaseEntry>;
  standardActivationCount: number;
  syncedEvaluationExpenseAccountIds: Record<string, true>;
}

export interface CombineSpendSnapshot {
  startedOn: string;
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
  status: string;
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
    startedOn: COMBINE_SPEND_TRACKER_START_DATE,
    purchasesByAccountId: {},
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

export function evolveCombineSpendLedger(
  ledger: CombineSpendLedger,
  accounts: CombineTrackerAccount[],
): CombineSpendLedger {
  const nextLedger: CombineSpendLedger = {
    startedOn: ledger.startedOn,
    purchasesByAccountId: { ...ledger.purchasesByAccountId },
    standardActivationCount: ledger.standardActivationCount,
    syncedEvaluationExpenseAccountIds: { ...ledger.syncedEvaluationExpenseAccountIds },
  };

  for (const account of accounts) {
    if (account.status.trim().toUpperCase() !== "ACTIVE") {
      continue;
    }
    const planSize = getCombinePlanSizeFromAccountName(account.name);
    if (planSize === null) {
      continue;
    }
    const accountId = String(account.id);
    if (nextLedger.purchasesByAccountId[accountId] !== undefined) {
      continue;
    }
    nextLedger.purchasesByAccountId[accountId] = {
      planSize,
      purchasedOn: getTodayLocalIsoDate(),
    };
  }

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
    countsByPlan,
    totalTrackedCombines: countsByPlan["50k"] + countsByPlan["100k"] + countsByPlan["150k"],
    baseCombineCostCents,
    standardActivationCount: ledger.standardActivationCount,
    standardActivationCostCents,
    totalCostCents: baseCombineCostCents + standardActivationCostCents,
  };
}

function normalizeCombineSpendLedger(raw: unknown): CombineSpendLedger {
  if (!raw || typeof raw !== "object") {
    return createEmptyCombineSpendLedger();
  }

  const candidate = raw as Record<string, unknown>;
  const startedOn =
    typeof candidate.startedOn === "string" && candidate.startedOn.trim().length > 0
      ? candidate.startedOn
      : COMBINE_SPEND_TRACKER_START_DATE;

  const purchasesByAccountIdRaw =
    candidate.purchasesByAccountId && typeof candidate.purchasesByAccountId === "object"
      ? (candidate.purchasesByAccountId as Record<string, unknown>)
      : {};
  const purchasesByAccountId: Record<string, CombinePurchaseEntry> = {};
  for (const [accountId, rawPurchase] of Object.entries(purchasesByAccountIdRaw)) {
    if (rawPurchase === "50k" || rawPurchase === "100k" || rawPurchase === "150k") {
      purchasesByAccountId[accountId] = {
        planSize: rawPurchase,
        purchasedOn: startedOn,
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
      purchasedOn:
        typeof purchase.purchasedOn === "string" && purchase.purchasedOn.trim().length > 0
          ? purchase.purchasedOn
          : startedOn,
    };
  }

  const standardActivationCountRaw =
    typeof candidate.standardActivationCount === "number" ? Math.floor(candidate.standardActivationCount) : 0;
  const standardActivationCount = Number.isFinite(standardActivationCountRaw)
    ? Math.max(0, standardActivationCountRaw)
    : 0;

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
    purchasesByAccountId,
    standardActivationCount,
    syncedEvaluationExpenseAccountIds,
  };
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
): UnsyncedEvaluationExpensePurchase[] {
  const unsynced: UnsyncedEvaluationExpensePurchase[] = [];

  for (const [accountId, purchase] of Object.entries(ledger.purchasesByAccountId)) {
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
    unsyncedEvaluationPurchases: listUnsyncedEvaluationExpensePurchases(next),
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
