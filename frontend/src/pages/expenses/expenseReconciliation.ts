import {
  getCombinePlanSizeFromAccountName,
  getCombinePriceCentsFromAccountName,
  isDailyLossLimitCombineAccountName,
  isTrackedCombinePurchaseExpense,
  type CombineSpendSnapshot,
  type CombineTrackerSyncResult,
  type UnsyncedEvaluationExpensePurchase,
} from "../../lib/combineTracker";
import type {
  AccountProviderSyncStatus,
  AccountTradeDataSource,
  ExpenseCreateInput,
  ExpenseRecord,
  ExpensePlanSize,
} from "../../lib/types";

export type ExpenseReconciliationDecision =
  | { allowed: true }
  | { allowed: false; reason: "demo_mode" | "cancelled" };

export function decideExpenseReconciliation(
  demoModeEnabled: boolean,
  confirmed: boolean,
): ExpenseReconciliationDecision {
  if (demoModeEnabled) {
    return { allowed: false, reason: "demo_mode" };
  }
  if (!confirmed) {
    return { allowed: false, reason: "cancelled" };
  }
  return { allowed: true };
}

/**
 * A deliberately structural subset of AccountInfo. Provider health fields are
 * optional here so the safety gate can reject an older backend response with
 * an actionable message instead of letting a rolling frontend deploy crash.
 */
export interface ExpenseReconciliationAccount {
  id: number;
  name: string;
  provider_name?: string;
  status?: string;
  account_state?: string;
  trade_data_source?: AccountTradeDataSource;
  provider_data_stale?: boolean;
  provider_sync_status?: AccountProviderSyncStatus;
  provider_sync_error_code?: string | null;
  provider_sync_error_message?: string | null;
  provider_last_successful_refresh_at?: string | null;
}

export interface ActiveCombineAccount {
  accountId: number;
  planSize: Extract<ExpensePlanSize, "50k" | "100k" | "150k">;
  amountCents: number;
  isDailyLossLimit: boolean;
}

export interface PlannedCombineExpenseCreation {
  accountId: number;
  input: ExpenseCreateInput;
}

export interface CombineExpenseReconciliationPlan {
  expenseIdsToDelete: number[];
  expensesToCreate: PlannedCombineExpenseCreation[];
}

export interface CombineExpenseReconciliationDependencies {
  loadAccounts: () => Promise<ExpenseReconciliationAccount[]>;
  loadExpenses: () => Promise<ExpenseRecord[]>;
  loadSuppressedAccountIds: () => Promise<number[]>;
  createExpense: (input: ExpenseCreateInput) => Promise<ExpenseRecord>;
  deleteExpense: (expenseId: number) => Promise<void>;
  isDuplicateCreateError: (error: unknown) => boolean;
  syncTrackerFromExpenses: (expenses: ExpenseRecord[]) => CombineSpendSnapshot;
  syncTrackerFromAccounts: (accounts: ExpenseReconciliationAccount[]) => CombineTrackerSyncResult;
  markEvaluationExpensesSynced: (accountIds: number[]) => CombineSpendSnapshot;
  suppressEvaluationExpenseSync: (accountIds: number[]) => CombineSpendSnapshot;
}

export interface CombineExpenseReconciliationResult {
  snapshot: CombineSpendSnapshot;
  eligibleCombineCount: number;
  createdCount: number;
  deletedCount: number;
  duplicateCreateCount: number;
  failedCreateCount: number;
  failedDeleteCount: number;
  didMutateExpenses: boolean;
}

export class ExpenseReconciliationSafetyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ExpenseReconciliationSafetyError";
  }
}

function normalizeAccountState(account: ExpenseReconciliationAccount): string {
  return (account.account_state ?? account.status ?? "").trim().toUpperCase();
}

function isTrackableAccountState(account: ExpenseReconciliationAccount): boolean {
  const state = normalizeAccountState(account);
  return state === "ACTIVE" || state === "LOCKED_OUT";
}

function isProjectXAccount(account: ExpenseReconciliationAccount): boolean {
  // The actual AccountInfo contract always includes trade_data_source. Treat a
  // missing value as ProjectX so old responses fail closed at the health gate.
  return account.trade_data_source !== "csv_import";
}

function getProviderBackedAccountName(account: ExpenseReconciliationAccount): string {
  const providerName = account.provider_name?.trim();
  return providerName ? providerName : account.name;
}

function withCachedFallbackContext(account: ExpenseReconciliationAccount, message: string): string {
  return account.provider_sync_status === "cached_fallback"
    ? `${message} Cached account data cannot be used for combine expense reconciliation.`
    : message;
}

export function collectActiveCombineAccounts(
  accounts: ExpenseReconciliationAccount[],
): ActiveCombineAccount[] {
  const activeCombines: ActiveCombineAccount[] = [];

  for (const account of accounts) {
    if (!isProjectXAccount(account) || !isTrackableAccountState(account)) {
      continue;
    }

    const providerBackedName = getProviderBackedAccountName(account);
    const planSize = getCombinePlanSizeFromAccountName(providerBackedName);
    const amountCents = getCombinePriceCentsFromAccountName(providerBackedName);
    if (planSize === null || amountCents === null) {
      continue;
    }

    activeCombines.push({
      accountId: account.id,
      planSize,
      amountCents,
      isDailyLossLimit: isDailyLossLimitCombineAccountName(providerBackedName),
    });
  }

  return activeCombines;
}

function providerFailureMessage(account: ExpenseReconciliationAccount): string | null {
  const code = account.provider_sync_error_code?.trim();
  if (code === "projectx_credentials_not_configured") {
    return withCachedFallbackContext(
      account,
      "ProjectX credentials are not configured for this signed-in user. Configure them, then retry.",
    );
  }
  if (code === "projectx_credentials_unavailable") {
    return withCachedFallbackContext(
      account,
      "The signed-in user's stored ProjectX credentials could not be used. Reconnect them, then retry.",
    );
  }
  if (code === "projectx_auth_failed") {
    return withCachedFallbackContext(
      account,
      "ProjectX rejected the signed-in user's stored credentials. Reconnect them, then retry.",
    );
  }
  if (code === "projectx_network_error") {
    return withCachedFallbackContext(
      account,
      "ProjectX could not be reached. Check the connection and retry after a successful account refresh.",
    );
  }

  const safeProviderMessage = account.provider_sync_error_message?.trim();
  if (safeProviderMessage) {
    return withCachedFallbackContext(account, safeProviderMessage);
  }
  if (code === "projectx_cached_fallback_used" || account.provider_sync_status === "cached_fallback") {
    return "The ProjectX refresh failed and cached account data was returned. Retry after a successful live refresh.";
  }
  return null;
}

function withMutationFreeSuffix(message: string): string {
  return `${message} No expenses or local combine-tracker data were changed.`;
}

/**
 * Reconciliation is intentionally stricter than ordinary account rendering:
 * a forced request must prove that eligible provider combines came from
 * ProjectX, not merely from a recent or aged cache. When no combine is
 * recognized, every returned ProjectX row must be fresh before the UI can call
 * that an authoritative zero-combine result.
 */
export function assertFreshProviderDataForExpenseReconciliation(
  accounts: ExpenseReconciliationAccount[],
): void {
  const providerAccounts = accounts.filter(isProjectXAccount);
  const explicitFailure = providerAccounts.find((account) => providerFailureMessage(account) !== null);
  if (explicitFailure) {
    throw new ExpenseReconciliationSafetyError(
      withMutationFreeSuffix(providerFailureMessage(explicitFailure) as string),
    );
  }

  const activeCombineIds = new Set(collectActiveCombineAccounts(accounts).map((account) => account.accountId));
  const accountsRequiringFreshness = activeCombineIds.size > 0
    ? providerAccounts.filter((account) => activeCombineIds.has(account.id))
    : providerAccounts;
  for (const account of accountsRequiringFreshness) {
    if (account.provider_data_stale === true || account.provider_sync_status === "cache_stale") {
      throw new ExpenseReconciliationSafetyError(
        withMutationFreeSuffix(
          "ProjectX account data is stale. Refresh ProjectX successfully, then retry reconciliation.",
        ),
      );
    }
    if (account.provider_sync_status !== "provider_fresh" || account.provider_data_stale !== false) {
      throw new ExpenseReconciliationSafetyError(
        withMutationFreeSuffix(
          "A live ProjectX refresh was not confirmed for the provider-backed account list. Refresh ProjectX successfully, then retry reconciliation.",
        ),
      );
    }
  }
}

function isAutoTrackedCombineExpense(expense: ExpenseRecord): boolean {
  return expense.category === "evaluation_fee" && expense.tags.includes("combine_tracker");
}

function isManualCombineExpense(expense: ExpenseRecord): boolean {
  return !isAutoTrackedCombineExpense(expense) && isTrackedCombinePurchaseExpense(expense);
}

function sortAutoExpensesNewestFirst(left: ExpenseRecord, right: ExpenseRecord): number {
  const createdAtDiff = Date.parse(right.created_at) - Date.parse(left.created_at);
  if (Number.isFinite(createdAtDiff) && createdAtDiff !== 0) {
    return createdAtDiff;
  }
  return right.id - left.id;
}

function buildAutoTrackedExpenseInput(
  activeCombine: ActiveCombineAccount,
  purchase: UnsyncedEvaluationExpensePurchase,
): ExpenseCreateInput {
  const dllSuffix = activeCombine.isDailyLossLimit ? " DLL" : "";
  return {
    expense_date: purchase.purchasedOn,
    amount_cents: purchase.amountCents,
    category: "evaluation_fee",
    provider: "topstep",
    plan_size: activeCombine.planSize,
    account_id: activeCombine.accountId,
    account_type: activeCombine.isDailyLossLimit ? "no_activation" : "standard",
    description: `Auto tracked combine purchase (${activeCombine.planSize.toUpperCase()}${dllSuffix})`,
    tags: activeCombine.isDailyLossLimit ? ["combine_tracker", "auto", "dll"] : ["combine_tracker", "auto"],
    currency: "USD",
  };
}

/**
 * Builds a deterministic mutation plan. Spreadsheet import dates are not used
 * as a global cutoff: only an expense tied to the same account can take
 * precedence over that account's inferred purchase.
 */
export function buildCombineExpenseReconciliationPlan(
  accounts: ExpenseReconciliationAccount[],
  expenses: ExpenseRecord[],
  unsyncedPurchases: UnsyncedEvaluationExpensePurchase[],
): CombineExpenseReconciliationPlan {
  const activeCombines = collectActiveCombineAccounts(accounts);
  const activeCombineByAccountId = new Map(activeCombines.map((account) => [account.accountId, account]));
  const expensesByAccountId = new Map<number, ExpenseRecord[]>();
  const expenseIdsToDelete = new Set<number>();

  for (const expense of expenses) {
    const isAuto = isAutoTrackedCombineExpense(expense);
    const isManual = isManualCombineExpense(expense);
    if (!isAuto && !isManual) {
      continue;
    }
    if (expense.account_id === null) {
      // Only rows explicitly tagged as tracker-generated are safe to clean up
      // without an account association. Imported/manual history is retained.
      if (isAuto) {
        expenseIdsToDelete.add(expense.id);
      }
      continue;
    }
    const rows = expensesByAccountId.get(expense.account_id);
    if (rows) {
      rows.push(expense);
    } else {
      expensesByAccountId.set(expense.account_id, [expense]);
    }
  }

  const accountsWithRetainedExpense = new Set<number>();
  for (const [accountId, rows] of expensesByAccountId.entries()) {
    const manualRows = rows.filter(isManualCombineExpense);
    const autoRows = rows.filter(isAutoTrackedCombineExpense);
    if (manualRows.length > 0) {
      for (const autoRow of autoRows) {
        expenseIdsToDelete.add(autoRow.id);
      }
      accountsWithRetainedExpense.add(accountId);
      continue;
    }

    if (autoRows.length > 0) {
      const [retained, ...duplicates] = [...autoRows].sort(sortAutoExpensesNewestFirst);
      if (retained) {
        accountsWithRetainedExpense.add(accountId);
      }
      for (const duplicate of duplicates) {
        expenseIdsToDelete.add(duplicate.id);
      }
    }
  }

  const unsyncedByAccountId = new Map(unsyncedPurchases.map((purchase) => [purchase.accountId, purchase]));
  const expensesToCreate: PlannedCombineExpenseCreation[] = [];
  for (const [accountId, activeCombine] of activeCombineByAccountId.entries()) {
    if (accountsWithRetainedExpense.has(accountId)) {
      continue;
    }
    const purchase = unsyncedByAccountId.get(accountId);
    // Absence is meaningful: the user may have deleted and suppressed an
    // auto-tracked row. Never manufacture a fallback purchase here.
    if (!purchase) {
      continue;
    }
    expensesToCreate.push({
      accountId,
      input: buildAutoTrackedExpenseInput(activeCombine, purchase),
    });
  }

  return {
    expenseIdsToDelete: [...expenseIdsToDelete].sort((left, right) => left - right),
    expensesToCreate,
  };
}

/**
 * Runs the full reconciliation with an explicit read/validate/mutate order.
 * A provider or expense-read failure happens before both browser-ledger and
 * expense API mutations.
 */
export async function reconcileCombineExpenses(
  dependencies: CombineExpenseReconciliationDependencies,
): Promise<CombineExpenseReconciliationResult> {
  const accounts = await dependencies.loadAccounts();
  assertFreshProviderDataForExpenseReconciliation(accounts);
  const [expenses, suppressedAccountIds] = await Promise.all([
    dependencies.loadExpenses(),
    dependencies.loadSuppressedAccountIds(),
  ]);

  dependencies.syncTrackerFromExpenses(expenses);
  dependencies.suppressEvaluationExpenseSync(suppressedAccountIds);
  const projectXAccounts = accounts.filter(isProjectXAccount);
  const trackerSync = dependencies.syncTrackerFromAccounts(projectXAccounts);
  const plan = buildCombineExpenseReconciliationPlan(
    accounts,
    expenses,
    trackerSync.unsyncedEvaluationPurchases,
  );

  const successfullyDeletedIds = new Set<number>();
  const createdExpenses: ExpenseRecord[] = [];
  const duplicateCreateAccountIds: number[] = [];
  let failedDeleteCount = 0;
  let failedCreateCount = 0;

  for (const expenseId of plan.expenseIdsToDelete) {
    try {
      await dependencies.deleteExpense(expenseId);
      successfullyDeletedIds.add(expenseId);
    } catch {
      failedDeleteCount += 1;
    }
  }

  for (const creation of plan.expensesToCreate) {
    try {
      createdExpenses.push(await dependencies.createExpense(creation.input));
    } catch (error) {
      if (dependencies.isDuplicateCreateError(error)) {
        duplicateCreateAccountIds.push(creation.accountId);
      } else {
        failedCreateCount += 1;
      }
    }
  }

  const didMutateExpenses = successfullyDeletedIds.size > 0 || createdExpenses.length > 0;
  let snapshot = trackerSync.snapshot;
  if (didMutateExpenses) {
    const finalExpenses = expenses
      .filter((expense) => !successfullyDeletedIds.has(expense.id))
      .concat(createdExpenses);
    dependencies.syncTrackerFromExpenses(finalExpenses);
    snapshot = dependencies.syncTrackerFromAccounts(projectXAccounts).snapshot;
  }
  if (duplicateCreateAccountIds.length > 0) {
    snapshot = dependencies.markEvaluationExpensesSynced(duplicateCreateAccountIds);
  }

  return {
    snapshot,
    eligibleCombineCount: collectActiveCombineAccounts(accounts).length,
    createdCount: createdExpenses.length,
    deletedCount: successfullyDeletedIds.size,
    duplicateCreateCount: duplicateCreateAccountIds.length,
    failedCreateCount,
    failedDeleteCount,
    didMutateExpenses,
  };
}
