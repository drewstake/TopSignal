// Investigation probes assert current defects, not desired behavior. No real network.
import { afterEach, expect, it, vi } from "vitest";
const identity = vi.hoisted(() => ({ token: "" }));
vi.mock("../../../frontend/src/lib/supabase", () => ({
  getAccessToken: async () => identity.token,
  getAccessTokenSync: () => identity.token,
}));
import { reconcileCombineExpenses } from "../../../frontend/src/pages/expenses/expenseReconciliation";
import {
  syncCombineSpendTracker, syncCombineSpendTrackerFromExpenses,
  markEvaluationExpensesSynced, suppressEvaluationExpenseSync,
} from "../../../frontend/src/lib/combineTracker";
import { getScopedStorageKey } from "../../../frontend/src/lib/storageScope";
import { createExpense } from "../../../frontend/src/lib/api";

function token(subject: string) {
  return `fixture.${btoa(JSON.stringify({ iss: "fixture", sub: subject }))}.fake`;
}
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

it("writes old-user combine data and submits a fee with the replacement user's bearer", async () => {
  localStorage.clear();
  identity.token = token("user-a");
  const keyA = getScopedStorageKey("topsignal.combineSpendTracker.v3");
  let release!: (value: never[]) => void;
  let readsStarted!: () => void;
  const reading = new Promise<void>(resolve => { readsStarted = resolve; });
  const heldExpenses = new Promise<never[]>(resolve => { release = resolve; });
  const requests: { auth: string | null; body: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => {
    const body = JSON.parse(options.body);
    requests.push({ auth: new Headers(options.headers).get("Authorization"), body });
    return new Response(JSON.stringify({ ...body, id: 1, amount: body.amount_cents / 100 }),
      { status: 201, headers: { "Content-Type": "application/json" } });
  }));
  const running = reconcileCombineExpenses({
    loadAccounts: async () => [{ id: 91001, name: "50KTC-private-a", status: "ACTIVE",
      account_state: "ACTIVE", trade_data_source: "projectx", provider_data_stale: false,
      provider_sync_status: "provider_fresh", provider_last_successful_refresh_at: new Date().toISOString() }],
    loadExpenses: () => { readsStarted(); return heldExpenses; },
    loadSuppressedAccountIds: async () => [],
    createExpense,
    deleteExpense: async () => { throw new Error("Unexpected fixture delete"); },
    isDuplicateCreateError: () => false,
    syncTrackerFromExpenses: syncCombineSpendTrackerFromExpenses,
    syncTrackerFromAccounts: syncCombineSpendTracker,
    markEvaluationExpensesSynced,
    suppressEvaluationExpenseSync,
  });
  await reading;
  identity.token = token("user-b"); // Auth event remounts UI, but the old async operation remains alive.
  const keyB = getScopedStorageKey("topsignal.combineSpendTracker.v3");
  release([]);
  await running;
  expect(keyA).not.toBe(keyB);
  expect(localStorage.getItem(keyA)).toBeNull();
  expect(JSON.parse(localStorage.getItem(keyB)!).purchasesByAccountId["91001"]).toBeTruthy();
  expect(requests).toHaveLength(1);
  expect(requests[0].auth).toBe(`Bearer ${token("user-b")}`);
  expect(requests[0].body.account_id).toBe(91001);
  console.log("CONFIRMED: A's pending reconciliation persisted account 91001 in B's browser scope and sent its fee using B's bearer");
});
