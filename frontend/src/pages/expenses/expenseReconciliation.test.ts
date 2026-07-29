import { describe, expect, it, vi } from "vitest";

import type {
  CombineSpendSnapshot,
  CombineTrackerSyncResult,
  UnsyncedEvaluationExpensePurchase,
} from "../../lib/combineTracker";
import type { ExpenseCreateInput, ExpenseRecord } from "../../lib/types";
import {
  assertFreshProviderDataForExpenseReconciliation,
  buildCombineExpenseReconciliationPlan,
  collectActiveCombineAccounts,
  decideExpenseReconciliation,
  ExpenseReconciliationSafetyError,
  reconcileCombineExpenses,
  type CombineExpenseReconciliationDependencies,
  type ExpenseReconciliationAccount,
} from "./expenseReconciliation";

function snapshot(totalTrackedCombines = 0): CombineSpendSnapshot {
  return {
    startedOn: "2026-07-29",
    startedAt: "2026-07-29T12:00:00.000Z",
    countsByPlan: { "50k": totalTrackedCombines, "100k": 0, "150k": 0 },
    totalTrackedCombines,
    baseCombineCostCents: totalTrackedCombines * 4_900,
    standardActivationCount: 0,
    standardActivationCostCents: 0,
    totalCostCents: totalTrackedCombines * 4_900,
  };
}

function account(overrides: Partial<ExpenseReconciliationAccount> = {}): ExpenseReconciliationAccount {
  return {
    id: 101,
    name: "50KTC-101",
    provider_name: "50KTC-101",
    status: "ACTIVE",
    account_state: "ACTIVE",
    trade_data_source: "projectx",
    provider_data_stale: false,
    provider_sync_status: "provider_fresh",
    provider_sync_error_code: null,
    provider_sync_error_message: null,
    provider_last_successful_refresh_at: "2026-07-29T12:00:00.000Z",
    ...overrides,
  };
}

function expense(overrides: Partial<ExpenseRecord> = {}): ExpenseRecord {
  return {
    id: 1,
    account_id: 101,
    provider: "topstep",
    expense_date: "2026-07-29",
    amount_cents: 4_900,
    amount: 49,
    currency: "USD",
    category: "evaluation_fee",
    account_type: "standard",
    plan_size: "50k",
    description: null,
    tags: [],
    created_at: "2026-07-29T12:00:00.000Z",
    updated_at: "2026-07-29T12:00:00.000Z",
    ...overrides,
  };
}

function purchase(
  accountId: number,
  planSize: "50k" | "100k" | "150k",
  amountCents: number,
): UnsyncedEvaluationExpensePurchase {
  return {
    accountId,
    planSize,
    purchasedOn: "2026-07-29",
    amountCents,
  };
}

describe("decideExpenseReconciliation", () => {
  it("requires an explicit confirmation", () => {
    expect(decideExpenseReconciliation(false, false)).toEqual({ allowed: false, reason: "cancelled" });
    expect(decideExpenseReconciliation(false, true)).toEqual({ allowed: true });
  });

  it("never permits reconciliation in demo mode", () => {
    expect(decideExpenseReconciliation(true, true)).toEqual({ allowed: false, reason: "demo_mode" });
  });
});

describe("provider freshness safety gate", () => {
  it("accepts only explicitly provider-fresh, non-stale combine accounts", () => {
    expect(() => assertFreshProviderDataForExpenseReconciliation([
      account(),
      account({
        id: 102,
        name: "XFA-102",
        provider_name: "XFA-102",
        provider_sync_status: "cache_fresh",
      }),
    ])).not.toThrow();

    expect(() =>
      assertFreshProviderDataForExpenseReconciliation([
        account({ provider_sync_status: "cache_fresh" }),
      ]),
    ).toThrow(/live ProjectX refresh was not confirmed/i);

    expect(() =>
      assertFreshProviderDataForExpenseReconciliation([
        account({ provider_data_stale: true, provider_sync_status: "cache_stale" }),
      ]),
    ).toThrow(/account data is stale/i);
  });

  it("surfaces cached fallback and credential failures even when zero combines are returned", () => {
    expect(() =>
      assertFreshProviderDataForExpenseReconciliation([
        account({
          name: "XFA-101",
          provider_name: "XFA-101",
          provider_sync_status: "cache_fresh",
        }),
      ]),
    ).toThrow(/live ProjectX refresh was not confirmed/i);

    expect(() =>
      assertFreshProviderDataForExpenseReconciliation([
        account({
          name: "XFA-101",
          provider_name: "XFA-101",
          provider_sync_status: "cached_fallback",
          provider_sync_error_code: "projectx_cached_fallback_used",
        }),
      ]),
    ).toThrow(/cached account data/i);

    expect(() =>
      assertFreshProviderDataForExpenseReconciliation([
        account({
          name: "XFA-101",
          provider_name: "XFA-101",
          provider_sync_status: "cached_fallback",
          provider_sync_error_code: "projectx_credentials_not_configured",
        }),
      ]),
    ).toThrow(/signed-in user/i);
  });

  it("does not apply ProjectX freshness rules to CSV-import accounts", () => {
    expect(() =>
      assertFreshProviderDataForExpenseReconciliation([
        account({
          trade_data_source: "csv_import",
          provider_data_stale: true,
          provider_sync_status: "not_applicable",
          provider_sync_error_code: "projectx_network_error",
        }),
      ]),
    ).not.toThrow();
    expect(collectActiveCombineAccounts([
      account({ trade_data_source: "csv_import" }),
    ])).toEqual([]);
  });

  it("runs no reads beyond the forced account refresh and no mutations when provider data is stale", async () => {
    const loadExpenses = vi.fn(async () => [] as ExpenseRecord[]);
    const loadSuppressedAccountIds = vi.fn(async () => [] as number[]);
    const createExpense = vi.fn<CombineExpenseReconciliationDependencies["createExpense"]>();
    const deleteExpense = vi.fn<CombineExpenseReconciliationDependencies["deleteExpense"]>();
    const syncTrackerFromExpenses = vi.fn(() => snapshot());
    const syncTrackerFromAccounts = vi.fn((): CombineTrackerSyncResult => ({
      snapshot: snapshot(),
      unsyncedEvaluationPurchases: [],
    }));
    const markEvaluationExpensesSynced = vi.fn(() => snapshot());
    const suppressEvaluationExpenseSync = vi.fn(() => snapshot());

    await expect(reconcileCombineExpenses({
      loadAccounts: async () => [account({ provider_data_stale: true, provider_sync_status: "cache_stale" })],
      loadExpenses,
      loadSuppressedAccountIds,
      createExpense,
      deleteExpense,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses,
      syncTrackerFromAccounts,
      markEvaluationExpensesSynced,
      suppressEvaluationExpenseSync,
    })).rejects.toBeInstanceOf(ExpenseReconciliationSafetyError);

    expect(loadExpenses).not.toHaveBeenCalled();
    expect(loadSuppressedAccountIds).not.toHaveBeenCalled();
    expect(syncTrackerFromExpenses).not.toHaveBeenCalled();
    expect(syncTrackerFromAccounts).not.toHaveBeenCalled();
    expect(markEvaluationExpensesSynced).not.toHaveBeenCalled();
    expect(suppressEvaluationExpenseSync).not.toHaveBeenCalled();
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
  });

  it("runs no expense or ledger mutations when a provider failure returns cached fallback rows", async () => {
    const loadExpenses = vi.fn(async () => [] as ExpenseRecord[]);
    const loadSuppressedAccountIds = vi.fn(async () => [] as number[]);
    const createExpense = vi.fn(async () => expense());
    const deleteExpense = vi.fn(async () => undefined);
    const syncTrackerFromExpenses = vi.fn(() => snapshot());
    const syncTrackerFromAccounts = vi.fn((): CombineTrackerSyncResult => ({
      snapshot: snapshot(),
      unsyncedEvaluationPurchases: [],
    }));

    await expect(reconcileCombineExpenses({
      loadAccounts: async () => [account({
        provider_sync_status: "cached_fallback",
        provider_sync_error_code: "projectx_network_error",
        provider_data_stale: false,
      })],
      loadExpenses,
      loadSuppressedAccountIds,
      createExpense,
      deleteExpense,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses,
      syncTrackerFromAccounts,
      markEvaluationExpensesSynced: () => snapshot(),
      suppressEvaluationExpenseSync: () => snapshot(),
    })).rejects.toThrow(/Cached account data cannot be used/i);

    expect(loadExpenses).not.toHaveBeenCalled();
    expect(loadSuppressedAccountIds).not.toHaveBeenCalled();
    expect(syncTrackerFromExpenses).not.toHaveBeenCalled();
    expect(syncTrackerFromAccounts).not.toHaveBeenCalled();
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
  });
});

describe("combine recognition and generated metadata", () => {
  it("recognizes standard and DLL variants in ACTIVE and LOCKED_OUT states", () => {
    expect(collectActiveCombineAccounts([
      account({ id: 1, provider_name: "50KTC-1", account_state: "ACTIVE" }),
      account({ id: 2, provider_name: "100KTC-V2-DLL-2", account_state: "LOCKED_OUT" }),
      account({ id: 3, provider_name: "150KTC (Daily Loss Limit)-3", account_state: "ACTIVE" }),
      account({ id: 4, provider_name: "50KTC-4", account_state: "MISSING", status: "MISSING" }),
    ])).toEqual([
      { accountId: 1, planSize: "50k", amountCents: 4_900, isDailyLossLimit: false },
      { accountId: 2, planSize: "100k", amountCents: 12_900, isDailyLossLimit: true },
      { accountId: 3, planSize: "150k", amountCents: 19_900, isDailyLossLimit: true },
    ]);
  });

  it("creates all standard and DLL plan sizes with exact prices, metadata, and purchase dates", () => {
    const accounts = [
      account({ id: 1, provider_name: "50KTC-1" }),
      account({ id: 2, provider_name: "100KTC-2" }),
      account({ id: 3, provider_name: "150KTC-3" }),
      account({ id: 4, provider_name: "50KTC-V2-DLL-4" }),
      account({ id: 5, provider_name: "100KTC_V2_DLL_5" }),
      account({ id: 6, provider_name: "150KTC (DLL)-6", account_state: "LOCKED_OUT" }),
    ];
    const plan = buildCombineExpenseReconciliationPlan(accounts, [], [
      purchase(1, "50k", 4_900),
      purchase(2, "100k", 9_900),
      purchase(3, "150k", 14_900),
      purchase(4, "50k", 8_500),
      purchase(5, "100k", 12_900),
      purchase(6, "150k", 19_900),
    ]);

    expect(plan.expensesToCreate.map((creation) => creation.input)).toEqual([
      {
        expense_date: "2026-07-29",
        amount_cents: 4_900,
        category: "evaluation_fee",
        provider: "topstep",
        plan_size: "50k",
        account_id: 1,
        account_type: "standard",
        description: "Auto tracked combine purchase (50K)",
        tags: ["combine_tracker", "auto"],
        currency: "USD",
      },
      {
        expense_date: "2026-07-29",
        amount_cents: 9_900,
        category: "evaluation_fee",
        provider: "topstep",
        plan_size: "100k",
        account_id: 2,
        account_type: "standard",
        description: "Auto tracked combine purchase (100K)",
        tags: ["combine_tracker", "auto"],
        currency: "USD",
      },
      {
        expense_date: "2026-07-29",
        amount_cents: 14_900,
        category: "evaluation_fee",
        provider: "topstep",
        plan_size: "150k",
        account_id: 3,
        account_type: "standard",
        description: "Auto tracked combine purchase (150K)",
        tags: ["combine_tracker", "auto"],
        currency: "USD",
      },
      {
        expense_date: "2026-07-29",
        amount_cents: 8_500,
        category: "evaluation_fee",
        provider: "topstep",
        plan_size: "50k",
        account_id: 4,
        account_type: "no_activation",
        description: "Auto tracked combine purchase (50K DLL)",
        tags: ["combine_tracker", "auto", "dll"],
        currency: "USD",
      },
      {
        expense_date: "2026-07-29",
        amount_cents: 12_900,
        category: "evaluation_fee",
        provider: "topstep",
        plan_size: "100k",
        account_id: 5,
        account_type: "no_activation",
        description: "Auto tracked combine purchase (100K DLL)",
        tags: ["combine_tracker", "auto", "dll"],
        currency: "USD",
      },
      {
        expense_date: "2026-07-29",
        amount_cents: 19_900,
        category: "evaluation_fee",
        provider: "topstep",
        plan_size: "150k",
        account_id: 6,
        account_type: "no_activation",
        description: "Auto tracked combine purchase (150K DLL)",
        tags: ["combine_tracker", "auto", "dll"],
        currency: "USD",
      },
    ] satisfies ExpenseCreateInput[]);
  });
});

describe("combine expense mutation planning", () => {
  it("prefers a manual expense and safely removes all generated rows for that account", () => {
    const plan = buildCombineExpenseReconciliationPlan([account()], [
      expense({ id: 1, amount_cents: 3_750, amount: 37.5, tags: [], account_type: "standard" }),
      expense({ id: 2, tags: ["combine_tracker", "auto"], created_at: "2026-07-28T12:00:00.000Z" }),
      expense({ id: 3, tags: ["combine_tracker", "auto"], created_at: "2026-07-29T12:00:00.000Z" }),
    ], [purchase(101, "50k", 4_900)]);

    expect(plan.expenseIdsToDelete).toEqual([2, 3]);
    expect(plan.expensesToCreate).toEqual([]);
  });

  it("keeps one deterministic generated row and removes only older duplicates", () => {
    const plan = buildCombineExpenseReconciliationPlan([account()], [
      expense({ id: 10, tags: ["combine_tracker", "auto"], created_at: "2026-07-28T12:00:00.000Z" }),
      expense({ id: 11, tags: ["combine_tracker", "auto"], created_at: "2026-07-29T12:00:00.000Z" }),
    ], [purchase(101, "50k", 4_900)]);

    expect(plan.expenseIdsToDelete).toEqual([10]);
    expect(plan.expensesToCreate).toEqual([]);
  });

  it("does not let an unrelated same-day spreadsheet import suppress a genuinely new account", () => {
    const spreadsheetImport = expense({
      id: 20,
      account_id: null,
      expense_date: "2026-07-29",
      tags: ["topstep_import"],
    });
    const plan = buildCombineExpenseReconciliationPlan(
      [account()],
      [spreadsheetImport],
      [purchase(101, "50k", 4_900)],
    );

    expect(plan.expenseIdsToDelete).toEqual([]);
    expect(plan.expensesToCreate).toHaveLength(1);
    expect(plan.expensesToCreate[0]?.accountId).toBe(101);
  });

  it("honors a suppressed/deleted account by requiring an explicitly unsynced ledger purchase", () => {
    const plan = buildCombineExpenseReconciliationPlan([account()], [], []);

    expect(plan.expenseIdsToDelete).toEqual([]);
    expect(plan.expensesToCreate).toEqual([]);
  });
});

describe("reconcileCombineExpenses", () => {
  it("does not mutate ledger or expenses when persisted suppression state cannot be read", async () => {
    const syncTrackerFromExpenses = vi.fn(() => snapshot());
    const suppressEvaluationExpenseSync = vi.fn(() => snapshot());
    const syncTrackerFromAccounts = vi.fn((): CombineTrackerSyncResult => ({
      snapshot: snapshot(),
      unsyncedEvaluationPurchases: [purchase(101, "50k", 4_900)],
    }));
    const createExpense = vi.fn(async () => expense());
    const deleteExpense = vi.fn(async () => undefined);

    await expect(reconcileCombineExpenses({
      loadAccounts: async () => [account()],
      loadExpenses: async () => [],
      loadSuppressedAccountIds: async () => {
        throw new Error("suppression read unavailable");
      },
      createExpense,
      deleteExpense,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses,
      syncTrackerFromAccounts,
      markEvaluationExpensesSynced: () => snapshot(),
      suppressEvaluationExpenseSync,
    })).rejects.toThrow("suppression read unavailable");

    expect(syncTrackerFromExpenses).not.toHaveBeenCalled();
    expect(suppressEvaluationExpenseSync).not.toHaveBeenCalled();
    expect(syncTrackerFromAccounts).not.toHaveBeenCalled();
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
  });

  it("never feeds CSV-import accounts into the browser combine ledger", async () => {
    const syncTrackerFromAccounts = vi.fn((): CombineTrackerSyncResult => ({
      snapshot: snapshot(),
      unsyncedEvaluationPurchases: [],
    }));
    await reconcileCombineExpenses({
      loadAccounts: async () => [account({
        trade_data_source: "csv_import",
        provider_sync_status: "not_applicable",
        provider_data_stale: false,
      })],
      loadExpenses: async () => [],
      loadSuppressedAccountIds: async () => [],
      createExpense: async () => expense(),
      deleteExpense: async () => undefined,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses: () => snapshot(),
      syncTrackerFromAccounts,
      markEvaluationExpensesSynced: () => snapshot(),
      suppressEvaluationExpenseSync: () => snapshot(),
    });

    expect(syncTrackerFromAccounts).toHaveBeenCalledWith([]);
  });

  it("reports an explicit successful-refresh zero-combine result without mutating expenses", async () => {
    const createExpense = vi.fn(async () => expense());
    const deleteExpense = vi.fn(async () => undefined);
    const result = await reconcileCombineExpenses({
      loadAccounts: async () => [account({ name: "XFA-101", provider_name: "XFA-101" })],
      loadExpenses: async () => [],
      loadSuppressedAccountIds: async () => [],
      createExpense,
      deleteExpense,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses: () => snapshot(),
      syncTrackerFromAccounts: () => ({ snapshot: snapshot(), unsyncedEvaluationPurchases: [] }),
      markEvaluationExpensesSynced: () => snapshot(),
      suppressEvaluationExpenseSync: () => snapshot(),
    });

    expect(result.eligibleCombineCount).toBe(0);
    expect(result.didMutateExpenses).toBe(false);
    expect(createExpense).not.toHaveBeenCalled();
    expect(deleteExpense).not.toHaveBeenCalled();
  });

  it("reapplies persisted server suppression before account sync on a cleared browser ledger", async () => {
    const createExpense = vi.fn(async () => expense());
    const callOrder: string[] = [];
    let suppressionApplied = false;
    const result = await reconcileCombineExpenses({
      loadAccounts: async () => [account()],
      loadExpenses: async () => {
        callOrder.push("expenses-read");
        return [];
      },
      loadSuppressedAccountIds: async () => {
        callOrder.push("suppressions-read");
        return [101];
      },
      createExpense,
      deleteExpense: async () => undefined,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses: () => {
        callOrder.push("expense-ledger-sync");
        return snapshot();
      },
      suppressEvaluationExpenseSync: (accountIds) => {
        expect(accountIds).toEqual([101]);
        suppressionApplied = true;
        callOrder.push("suppression-applied");
        return snapshot();
      },
      syncTrackerFromAccounts: () => {
        callOrder.push("account-ledger-sync");
        return {
          snapshot: snapshot(1),
          unsyncedEvaluationPurchases: suppressionApplied
            ? []
            : [purchase(101, "50k", 4_900)],
        };
      },
      markEvaluationExpensesSynced: () => snapshot(),
    });

    expect(callOrder.indexOf("expenses-read")).toBeLessThan(callOrder.indexOf("expense-ledger-sync"));
    expect(callOrder.indexOf("suppressions-read")).toBeLessThan(callOrder.indexOf("suppression-applied"));
    expect(callOrder.indexOf("suppression-applied")).toBeLessThan(callOrder.indexOf("account-ledger-sync"));
    expect(result.createdCount).toBe(0);
    expect(createExpense).not.toHaveBeenCalled();
  });

  it("creates one expense for a new combine and remains idempotent on repeated runs", async () => {
    const accounts = [account()];
    const expenses: ExpenseRecord[] = [];
    let nextId = 1;
    const createExpense = vi.fn(async (input: ExpenseCreateInput) => {
      const created = expense({
        ...input,
        id: nextId,
        amount: (input.amount_cents ?? 0) / 100,
        account_id: input.account_id ?? null,
        account_type: input.account_type ?? null,
        plan_size: input.plan_size ?? null,
        description: input.description ?? null,
        tags: input.tags ?? [],
      });
      nextId += 1;
      expenses.push(created);
      return created;
    });
    const deleteExpense = vi.fn(async (expenseId: number) => {
      const index = expenses.findIndex((row) => row.id === expenseId);
      if (index >= 0) {
        expenses.splice(index, 1);
      }
    });
    const trackerResult = (): CombineTrackerSyncResult => ({
      snapshot: snapshot(1),
      unsyncedEvaluationPurchases: [purchase(101, "50k", 4_900)],
    });
    const dependencies: CombineExpenseReconciliationDependencies = {
      loadAccounts: async () => accounts,
      loadExpenses: async () => [...expenses],
      loadSuppressedAccountIds: async () => [],
      createExpense,
      deleteExpense,
      isDuplicateCreateError: () => false,
      syncTrackerFromExpenses: () => snapshot(1),
      syncTrackerFromAccounts: trackerResult,
      markEvaluationExpensesSynced: () => snapshot(1),
      suppressEvaluationExpenseSync: () => snapshot(1),
    };

    const first = await reconcileCombineExpenses(dependencies);
    const second = await reconcileCombineExpenses(dependencies);

    expect(first.createdCount).toBe(1);
    expect(first.deletedCount).toBe(0);
    expect(second.createdCount).toBe(0);
    expect(second.deletedCount).toBe(0);
    expect(expenses).toHaveLength(1);
    expect(createExpense).toHaveBeenCalledOnce();
    expect(deleteExpense).not.toHaveBeenCalled();
  });
});
