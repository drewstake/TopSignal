// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const auth = vi.hoisted(() => ({ token: null as string | null, read: vi.fn<() => Promise<string | null>>() }));
vi.mock("../../lib/supabase", () => ({ getAccessToken: auth.read, getAccessTokenSync: () => auth.token }));
import { observeAuthIdentity } from "../../lib/authIdentity";
import { createAccountOperation } from "../../lib/accountOperation";
import { createExpense } from "../../lib/api";
import { setDemoModeEnabled, DEMO_MODE_STORAGE_KEY } from "../../lib/demoMode";
import { getScopedStorageKey } from "../../lib/storageScope";
import { syncCombineSpendTracker, syncCombineSpendTrackerFromExpenses, markEvaluationExpensesSynced, suppressEvaluationExpenseSync } from "../../lib/combineTracker";
import { reconcileCombineExpenses, type CombineExpenseReconciliationDependencies } from "./expenseReconciliation";
import type { ExpenseRecord } from "../../lib/types";

function token(sub: string, refresh = 1) { return `fixture.${btoa(JSON.stringify({ iss: "fixture", sub, refresh }))}.fake`; }
function signIn(sub: string | null, refresh = 1) {
  auth.token = sub ? token(sub, refresh) : null;
  observeAuthIdentity(auth.token);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function dependencies(overrides: Partial<CombineExpenseReconciliationDependencies> = {}): CombineExpenseReconciliationDependencies {
  return {
    loadAccounts: async () => [91001, 91002].map(id => ({ id, name: `50KTC-${id}`, status: "ACTIVE", account_state: "ACTIVE",
      trade_data_source: "projectx", provider_data_stale: false, provider_sync_status: "provider_fresh",
      provider_last_successful_refresh_at: new Date().toISOString() })),
    loadExpenses: async () => [], loadSuppressedAccountIds: async () => [], createExpense,
    deleteExpense: async () => { throw new Error("Unexpected deletion"); }, isDuplicateCreateError: () => false,
    syncTrackerFromExpenses: syncCombineSpendTrackerFromExpenses, syncTrackerFromAccounts: syncCombineSpendTracker,
    markEvaluationExpensesSynced, suppressEvaluationExpenseSync, ...overrides,
  };
}
beforeEach(() => {
  localStorage.clear();
  setDemoModeEnabled(false);
  signIn("a");
  auth.read.mockImplementation(async () => auth.token);
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    const body = JSON.parse(options.body);
    return new Response(JSON.stringify({ ...body, id: 1, amount: body.amount_cents / 100 }), { status: 201 });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); signIn(null); });

it.each(["replacement", "sign-out", "identity-round-trip", "demo-round-trip", "cross-tab-demo"])("cancels delayed reads on %s before storage or expense writes", async change => {
  const held = deferred<ExpenseRecord[]>();
  const started = deferred<void>();
  const running = reconcileCombineExpenses(dependencies({ loadExpenses: () => { started.resolve(); return held.promise; } }));
  await started.promise;
  if (change === "replacement") signIn("b");
  if (change === "sign-out") signIn(null);
  if (change === "identity-round-trip") { signIn("b"); signIn("a"); }
  if (change === "demo-round-trip") { setDemoModeEnabled(true); setDemoModeEnabled(false); }
  if (change === "cross-tab-demo") {
    localStorage.setItem(DEMO_MODE_STORAGE_KEY, "true");
    window.dispatchEvent(new StorageEvent("storage", { key: DEMO_MODE_STORAGE_KEY, newValue: "true" }));
    localStorage.setItem(DEMO_MODE_STORAGE_KEY, "false");
  }
  held.resolve([]);
  await expect(running).rejects.toThrow(/Reconciliation cancelled/);
  expect(fetch).not.toHaveBeenCalled();
  expect([...Array(localStorage.length)].map((_, i) => localStorage.key(i)).filter(k => k?.includes("combineSpend"))).toEqual([]);
});

it("stops the mutation queue and does not write the replacement ledger after a late successful create", async () => {
  const held = deferred<Response>();
  vi.mocked(fetch).mockImplementationOnce(() => held.promise);
  const running = reconcileCombineExpenses(dependencies());
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const request = vi.mocked(fetch).mock.calls[0][1]!;
  expect(new Headers(request.headers).get("Authorization")).toBe(`Bearer ${token("a")}`);
  signIn("b");
  expect(request.signal?.aborted).toBe(true);
  held.resolve(new Response(JSON.stringify({ id: 1, account_id: 91001 }), { status: 201 }));
  await expect(running).rejects.toThrow(/Reconciliation cancelled/);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem(getScopedStorageKey("topsignal.combineSpendTracker.v3"))).toBeNull();
});

it("rechecks identity after asynchronous token acquisition before dispatch", async () => {
  const held = deferred<string | null>();
  auth.read.mockReturnValueOnce(held.promise);
  const operation = createAccountOperation();
  const running = createExpense({ category: "other", amount: 1, expense_date: "2026-10-26" }, operation);
  signIn("b"); held.resolve(auth.token);
  await expect(running).rejects.toThrow(/Reconciliation cancelled/);
  expect(fetch).not.toHaveBeenCalled();
  operation.dispose();
});

it.each(["resolve", "reject"])("stops duplicate deletions after identity changes even when the first delete %ss", async outcome => {
  const held = deferred<void>();
  const remove = vi.fn(() => outcome === "resolve" ? held.promise : held.promise.then(() => { throw new Error("late failure"); }));
  const rows = [1, 2, 3].map(id => ({ id, account_id: 91001, provider: "topstep", category: "evaluation_fee",
    amount_cents: 4900, amount: 49, currency: "USD", plan_size: "50k", account_type: "standard",
    expense_date: "2026-10-26", created_at: `2026-10-26T12:00:0${id}Z`, tags: id === 1 ? [] : ["combine_tracker", "auto"] }) as ExpenseRecord);
  const running = reconcileCombineExpenses(dependencies({ loadExpenses: async () => rows, deleteExpense: remove }));
  await vi.waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
  signIn("b"); held.resolve();
  await expect(running).rejects.toThrow(/Reconciliation cancelled/);
  expect(remove).toHaveBeenCalledTimes(1);
  expect(fetch).not.toHaveBeenCalled();
  expect(localStorage.getItem(getScopedStorageKey("topsignal.combineSpendTracker.v3"))).toBeNull();
});

it("permits same-user token refresh during reconciliation", async () => {
  const result = await reconcileCombineExpenses(dependencies({ loadExpenses: async () => { signIn("a", 2); return []; } }));
  expect(result.createdCount).toBe(2);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("honors unmount cancellation even if a dependency ignores abort", async () => {
  const held = deferred<ExpenseRecord[]>();
  const controller = new AbortController();
  const running = reconcileCombineExpenses(dependencies({ loadExpenses: () => held.promise }), controller.signal);
  await Promise.resolve(); controller.abort(); held.resolve([]);
  await expect(running).rejects.toThrow(/Reconciliation cancelled/);
  expect(fetch).not.toHaveBeenCalled();
});
