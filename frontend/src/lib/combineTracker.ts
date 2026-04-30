import type { ExpenseAccountType, ExpenseCategory, ExpensePlanSize } from "./types";

type CombinePlanSize = Extract<ExpensePlanSize, "50k" | "100k" | "150k">;

const COMBINE_PREFIX_BY_PLAN: Record<CombinePlanSize, string> = {
  "50k": "50KTC",
  "100k": "100KTC",
  "150k": "150KTC",
};

const COMBINE_SPEND_TRACKER_STORAGE_KEY = "topsignal.combineSpendTracker.v3";
const LEGACY_COMBINE_SPEND_TRACKER_START_DATE = "2026-03-01";
export const STANDARD_ACTIVATION_FEE_CENTS = 14_900;

export const COMBINE_PRICE_CENTS_BY_PLAN: Record<CombinePlanSize, number> = {
  "50k": 4_900,
  "100k": 9_900,
  "150k": 14_900,
};

export const NO_ACTIVATION_COMBINE_PRICE_CENTS_BY_PLAN: Record<CombinePlanSize, number> = {
  "50k": 9_500,
  "100k": 14_900,
  "150k": 22_900,
};

export const DLL_COMBINE_PRICE_CENTS_BY_PLAN: Record<CombinePlanSize, number> = {
  "50k": 8_500,
  "100k": 12_900,
  "150k": 19_900,
};

const LEGACY_COMBINE_PRICE_CENTS_BY_PLAN: Record<CombinePlanSize, number> = {
  "50k": 11_500,
  "100k": 16_800,
  "150k": 22_100,
};

type CombinePurchaseSource = "account" | "expense" | "account_and_expense";
export interface CombineSpendLedger {
  startedOn: string;
  startedAt: string;
  purchasesByAccountId: Record<string, CombinePurchaseEntry>;
  knownCombineAccountIds: Record<string, true>;
  baselineCaptured: boolean;
  standardActivationCount: number;
  loggedStandardActivationCount: number;
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
  amountCents?: number;
  source?: CombinePurchaseSource;
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
  provider_name?: string;
  status?: string;
  account_state?: string;
}

export interface CombineTrackerExpense {
  id: number;
  account_id: number | null;
  expense_date: string;
  amount_cents: number;
  category: ExpenseCategory;
  account_type?: ExpenseAccountType | null;
  plan_size?: ExpensePlanSize | null;
  tags: string[];
  created_at?: string;
}

const EXPENSE_DERIVED_PURCHASE_KEY_PREFIX = "expense:";

function normalizeCombineTrackerAccountState(account: CombineTrackerAccount): string {
  const raw = account.status ?? account.account_state ?? "";
  return raw.trim().toUpperCase();
}

function isTrackableCombineTrackerAccount(account: CombineTrackerAccount): boolean {
  const state = normalizeCombineTrackerAccountState(account);
  return state === "ACTIVE" || state === "LOCKED_OUT";
}

function getProviderBackedAccountName(account: Pick<CombineTrackerAccount, "name" | "provider_name">): string {
  return account.provider_name ?? account.name;
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

export function isDailyLossLimitCombineAccountName(name: string): boolean {
  const normalized = name.trim().toUpperCase();
  return /(?:^|[-_\s])DLL(?:$|[-_\s])/.test(normalized) || normalized.includes("DAILY LOSS LIMIT");
}

export function getCombinePriceCentsFromAccountName(name: string): number | null {
  const planSize = getCombinePlanSizeFromAccountName(name);
  if (planSize === null) {
    return null;
  }
  return isDailyLossLimitCombineAccountName(name)
    ? DLL_COMBINE_PRICE_CENTS_BY_PLAN[planSize]
    : COMBINE_PRICE_CENTS_BY_PLAN[planSize];
}

export function createEmptyCombineSpendLedger(): CombineSpendLedger {
  return {
    startedOn: getTodayLocalIsoDate(),
    startedAt: getCurrentIsoTimestamp(),
    purchasesByAccountId: {},
    knownCombineAccountIds: {},
    baselineCaptured: false,
    standardActivationCount: 0,
    loggedStandardActivationCount: 0,
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
    loggedStandardActivationCount: ledger.loggedStandardActivationCount,
    syncedEvaluationExpenseAccountIds: { ...ledger.syncedEvaluationExpenseAccountIds },
  };

  for (const account of accounts) {
    // Keep temporarily locked combines tracked as still-active purchases.
    if (!isTrackableCombineTrackerAccount(account)) {
      continue;
    }
    const providerBackedName = getProviderBackedAccountName(account);
    const planSize = getCombinePlanSizeFromAccountName(providerBackedName);
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
        amountCents: getCombinePriceCentsFromAccountName(providerBackedName) ?? COMBINE_PRICE_CENTS_BY_PLAN[planSize],
        source: "account",
      };
      continue;
    }

    nextLedger.purchasesByAccountId[accountId] = {
      ...existingPurchase,
      planSize,
      amountCents: getPurchaseAmountCents(existingPurchase),
      source: addAccountSource(existingPurchase.source),
    };
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

  const baseCombineCostCents = Object.values(ledger.purchasesByAccountId).reduce(
    (total, purchase) => total + getPurchaseAmountCents(purchase),
    0,
  );
  const standardActivationCount = ledger.standardActivationCount + ledger.loggedStandardActivationCount;
  const standardActivationCostCents = standardActivationCount * STANDARD_ACTIVATION_FEE_CENTS;

  return {
    startedOn: ledger.startedOn,
    startedAt: ledger.startedAt,
    countsByPlan,
    totalTrackedCombines: countsByPlan["50k"] + countsByPlan["100k"] + countsByPlan["150k"],
    baseCombineCostCents,
    standardActivationCount,
    standardActivationCostCents,
    totalCostCents: baseCombineCostCents + standardActivationCostCents,
  };
}

function normalizeCombinePurchaseSource(value: unknown): CombinePurchaseSource {
  if (value === "account" || value === "expense" || value === "account_and_expense") {
    return value;
  }
  return "account";
}

function removeExpenseSource(source: CombinePurchaseSource): CombinePurchaseSource | null {
  if (source === "expense") {
    return null;
  }
  if (source === "account_and_expense") {
    return "account";
  }
  return "account";
}

function addExpenseSource(source: CombinePurchaseSource | undefined): CombinePurchaseSource {
  if (source === undefined) {
    return "expense";
  }
  const normalized = normalizeCombinePurchaseSource(source);
  if (normalized === "account") {
    return "account_and_expense";
  }
  return normalized;
}

function addAccountSource(source: CombinePurchaseSource | undefined): CombinePurchaseSource {
  if (source === undefined) {
    return "account";
  }
  const normalized = normalizeCombinePurchaseSource(source);
  if (normalized === "expense") {
    return "account_and_expense";
  }
  return normalized;
}

function getExpenseDerivedPurchaseKey(expenseId: number): string {
  return `${EXPENSE_DERIVED_PURCHASE_KEY_PREFIX}${expenseId}`;
}

function isExpenseDerivedPurchaseKey(value: string): boolean {
  return value.startsWith(EXPENSE_DERIVED_PURCHASE_KEY_PREFIX);
}

function compareIsoDate(left: string, right: string): number {
  return left.localeCompare(right);
}

function getEarlierIsoDate(left: string, right: string): string {
  return compareIsoDate(left, right) <= 0 ? left : right;
}

function getEarlierIsoDateTime(left: string, right: string): string {
  return Date.parse(left) <= Date.parse(right) ? left : right;
}

function getExpenseCreatedAtOrFallback(expense: CombineTrackerExpense): string {
  if (isIsoDateTime(expense.created_at)) {
    return expense.created_at;
  }
  return `${expense.expense_date}T00:00:00.000Z`;
}

function isCombinePlanSize(value: ExpensePlanSize | null | undefined): value is CombinePlanSize {
  return value === "50k" || value === "100k" || value === "150k";
}

function isKnownCombinePriceCents(planSize: CombinePlanSize, amountCents: number): boolean {
  return (
    amountCents === COMBINE_PRICE_CENTS_BY_PLAN[planSize] ||
    amountCents === NO_ACTIVATION_COMBINE_PRICE_CENTS_BY_PLAN[planSize] ||
    amountCents === DLL_COMBINE_PRICE_CENTS_BY_PLAN[planSize] ||
    amountCents === LEGACY_COMBINE_PRICE_CENTS_BY_PLAN[planSize]
  );
}

function normalizeAmountCents(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  const amountCents = Math.round(value);
  return amountCents >= 0 ? amountCents : null;
}

function getPurchaseAmountCents(purchase: CombinePurchaseEntry): number {
  return normalizeAmountCents(purchase.amountCents) ?? COMBINE_PRICE_CENTS_BY_PLAN[purchase.planSize];
}

export function isTrackedCombinePurchaseExpense(expense: CombineTrackerExpense): boolean {
  if (expense.category !== "evaluation_fee") {
    return false;
  }
  if (!isCombinePlanSize(expense.plan_size)) {
    return false;
  }
  if (expense.tags.includes("combine_tracker")) {
    return true;
  }
  if (expense.account_type === "no_activation") {
    return true;
  }
  return isKnownCombinePriceCents(expense.plan_size, expense.amount_cents);
}

export function isTrackedStandardActivationExpense(expense: CombineTrackerExpense): boolean {
  if (expense.category !== "activation_fee") {
    return false;
  }
  if (!isCombinePlanSize(expense.plan_size)) {
    return false;
  }
  if (expense.account_type !== null && expense.account_type !== undefined && expense.account_type !== "standard") {
    return false;
  }
  return expense.amount_cents === STANDARD_ACTIVATION_FEE_CENTS;
}

function reconcileCombineSpendLedgerWithExpenses(
  ledger: CombineSpendLedger,
  expenses: CombineTrackerExpense[],
): CombineSpendLedger {
  const nextLedger: CombineSpendLedger = {
    startedOn: ledger.startedOn,
    startedAt: ledger.startedAt,
    purchasesByAccountId: {},
    knownCombineAccountIds: { ...ledger.knownCombineAccountIds },
    baselineCaptured: ledger.baselineCaptured,
    standardActivationCount: ledger.standardActivationCount,
    loggedStandardActivationCount: 0,
    syncedEvaluationExpenseAccountIds: {},
  };

  for (const [purchaseKey, purchase] of Object.entries(ledger.purchasesByAccountId)) {
    const nextSource = removeExpenseSource(normalizeCombinePurchaseSource(purchase.source));
    if (nextSource === null) {
      continue;
    }
    nextLedger.purchasesByAccountId[purchaseKey] = {
      ...purchase,
      amountCents: getPurchaseAmountCents(purchase),
      source: nextSource,
    };
  }

  const sortedExpenses = [...expenses].sort((left, right) => {
    const expenseDateDiff = compareIsoDate(left.expense_date, right.expense_date);
    if (expenseDateDiff !== 0) {
      return expenseDateDiff;
    }
    const createdAtDiff = Date.parse(getExpenseCreatedAtOrFallback(left)) - Date.parse(getExpenseCreatedAtOrFallback(right));
    if (createdAtDiff !== 0) {
      return createdAtDiff;
    }
    return left.id - right.id;
  });

  let earliestRelevantExpenseDate: string | null = null;
  let earliestRelevantExpenseCreatedAt: string | null = null;
  let loggedStandardActivationCount = 0;

  for (const expense of sortedExpenses) {
    if (!isTrackedCombinePurchaseExpense(expense) && !isTrackedStandardActivationExpense(expense)) {
      continue;
    }

    earliestRelevantExpenseDate =
      earliestRelevantExpenseDate === null
        ? expense.expense_date
        : getEarlierIsoDate(earliestRelevantExpenseDate, expense.expense_date);
    const createdAt = getExpenseCreatedAtOrFallback(expense);
    earliestRelevantExpenseCreatedAt =
      earliestRelevantExpenseCreatedAt === null
        ? createdAt
        : getEarlierIsoDateTime(earliestRelevantExpenseCreatedAt, createdAt);

    if (isTrackedStandardActivationExpense(expense)) {
      loggedStandardActivationCount += 1;
      continue;
    }

    if (!isTrackedCombinePurchaseExpense(expense)) {
      continue;
    }

    const planSize = expense.plan_size;
    if (!isCombinePlanSize(planSize)) {
      continue;
    }

    const purchaseKey =
      expense.account_id === null ? getExpenseDerivedPurchaseKey(expense.id) : String(expense.account_id);
    const existingPurchase = nextLedger.purchasesByAccountId[purchaseKey];
    const existingSource = existingPurchase ? normalizeCombinePurchaseSource(existingPurchase.source) : null;
    const existingHasExpenseSource = existingSource === "expense" || existingSource === "account_and_expense";

    nextLedger.purchasesByAccountId[purchaseKey] = {
      planSize,
      purchasedOn:
        existingPurchase === undefined
          ? expense.expense_date
          : getEarlierIsoDate(existingPurchase.purchasedOn, expense.expense_date),
      amountCents:
        existingPurchase !== undefined && existingHasExpenseSource
          ? getPurchaseAmountCents(existingPurchase)
          : expense.amount_cents,
      source: addExpenseSource(existingPurchase?.source),
    };

    if (expense.account_id !== null) {
      const accountId = String(expense.account_id);
      nextLedger.knownCombineAccountIds[accountId] = true;
      nextLedger.syncedEvaluationExpenseAccountIds[accountId] = true;
    }
  }

  nextLedger.loggedStandardActivationCount = loggedStandardActivationCount;

  if (earliestRelevantExpenseDate !== null) {
    nextLedger.startedOn = getEarlierIsoDate(nextLedger.startedOn, earliestRelevantExpenseDate);
  }
  if (earliestRelevantExpenseCreatedAt !== null) {
    nextLedger.startedAt = getEarlierIsoDateTime(nextLedger.startedAt, earliestRelevantExpenseCreatedAt);
  }

  if (
    Object.keys(nextLedger.purchasesByAccountId).length > 0 ||
    Object.keys(nextLedger.knownCombineAccountIds).length > 0 ||
    loggedStandardActivationCount > 0
  ) {
    nextLedger.baselineCaptured = true;
  }

  return nextLedger;
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
        amountCents: LEGACY_COMBINE_PRICE_CENTS_BY_PLAN[rawPurchase],
        source: "account",
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
      amountCents: normalizeAmountCents(purchase.amountCents) ?? LEGACY_COMBINE_PRICE_CENTS_BY_PLAN[purchase.planSize],
      source: normalizeCombinePurchaseSource(purchase.source),
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
  const loggedStandardActivationCountRaw =
    typeof candidate.loggedStandardActivationCount === "number" ? Math.floor(candidate.loggedStandardActivationCount) : 0;
  const loggedStandardActivationCount = Number.isFinite(loggedStandardActivationCountRaw)
    ? Math.max(0, loggedStandardActivationCountRaw)
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
    if (isExpenseDerivedPurchaseKey(accountId)) {
      continue;
    }
    knownCombineAccountIds[accountId] = true;
  }

  const rawBaselineCaptured = candidate.baselineCaptured;
  const baselineCaptured =
    typeof rawBaselineCaptured === "boolean"
      ? rawBaselineCaptured
      : Object.keys(knownCombineAccountIds).length > 0 ||
        Object.keys(purchasesByAccountId).length > 0 ||
        loggedStandardActivationCount > 0;

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
    loggedStandardActivationCount,
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

export function syncCombineSpendTrackerFromExpenses(expenses: CombineTrackerExpense[]): CombineSpendSnapshot {
  const current = readCombineSpendLedger();
  const next = reconcileCombineSpendLedgerWithExpenses(current, expenses);
  writeCombineSpendLedger(next);
  return computeCombineSpendSnapshotFromLedger(next);
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
    if (getCombinePlanSizeFromAccountName(getProviderBackedAccountName(account)) === null) {
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
      amountCents: getPurchaseAmountCents(purchase),
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
