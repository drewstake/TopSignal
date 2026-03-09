import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  computeCombineSpendSnapshotFromLedger,
  createEmptyCombineSpendLedger,
  decrementStandardActivationCount,
  evolveCombineSpendLedger,
  getCombinePlanSizeFromAccountName,
  incrementStandardActivationCount,
  markEvaluationExpensesSynced,
  readCombineSpendSnapshot,
  STANDARD_ACTIVATION_FEE_CENTS,
  syncCombineSpendTracker,
} from "./combineTracker";

describe("getCombinePlanSizeFromAccountName", () => {
  it("maps account name prefixes to combine plan sizes", () => {
    expect(getCombinePlanSizeFromAccountName("50KTC-12345")).toBe("50k");
    expect(getCombinePlanSizeFromAccountName("100KTC-12345")).toBe("100k");
    expect(getCombinePlanSizeFromAccountName("150KTC-12345")).toBe("150k");
  });

  it("normalizes whitespace and letter casing", () => {
    expect(getCombinePlanSizeFromAccountName("  50ktc-main ")).toBe("50k");
  });

  it("ignores non-combine account names", () => {
    expect(getCombinePlanSizeFromAccountName("PA-100KTC")).toBeNull();
    expect(getCombinePlanSizeFromAccountName("Account 7001")).toBeNull();
  });
});

describe("evolveCombineSpendLedger", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-01T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("tracks active and locked-out combines, and keeps historical purchases after transitions", () => {
    const start = createEmptyCombineSpendLedger();
    const first = evolveCombineSpendLedger(start, [
      { id: 1, name: "50KTC-1", status: "ACTIVE" },
      { id: 2, name: "100KTC-2", status: "ACTIVE" },
      { id: 3, name: "150KTC-3", status: "MISSING" },
      { id: 4, name: "PA-1", status: "ACTIVE" },
    ]);

    expect(first.baselineCaptured).toBe(true);
    expect(first.knownCombineAccountIds).toEqual({
      "1": true,
      "2": true,
    });
    expect(first.purchasesByAccountId).toEqual({
      "1": { planSize: "50k", purchasedOn: "2026-03-01" },
      "2": { planSize: "100k", purchasedOn: "2026-03-01" },
    });

    const second = evolveCombineSpendLedger(first, [
      { id: 1, name: "50KTC-1", status: "LOCKED_OUT" },
      { id: 2, name: "XFA-2", status: "ACTIVE" },
      { id: 5, name: "150KTC-5", status: "ACTIVE" },
    ]);

    expect(second.knownCombineAccountIds).toEqual({
      "1": true,
      "2": true,
      "5": true,
    });
    expect(second.purchasesByAccountId).toEqual({
      "1": { planSize: "50k", purchasedOn: "2026-03-01" },
      "2": { planSize: "100k", purchasedOn: "2026-03-01" },
      "5": { planSize: "150k", purchasedOn: "2026-03-01" },
    });
  });

  it("adds locked-out combines that appear after baseline", () => {
    const start = createEmptyCombineSpendLedger();
    const baseline = evolveCombineSpendLedger(start, []);
    const next = evolveCombineSpendLedger(baseline, [{ id: 9, name: "150KTC-9", status: "LOCKED_OUT" }]);

    expect(next.purchasesByAccountId).toEqual({
      "9": { planSize: "150k", purchasedOn: "2026-03-01" },
    });
  });
});

describe("computeCombineSpendSnapshotFromLedger", () => {
  it("totals base combine spend plus standard activations", () => {
    const snapshot = computeCombineSpendSnapshotFromLedger({
      startedOn: "2026-03-01",
      startedAt: "2026-03-01T12:00:00.000Z",
      purchasesByAccountId: {
        "1": { planSize: "50k", purchasedOn: "2026-03-01" },
        "2": { planSize: "50k", purchasedOn: "2026-03-01" },
        "3": { planSize: "100k", purchasedOn: "2026-03-01" },
      },
      knownCombineAccountIds: {
        "1": true,
        "2": true,
        "3": true,
      },
      baselineCaptured: true,
      standardActivationCount: 2,
      syncedEvaluationExpenseAccountIds: {},
    });

    expect(snapshot.countsByPlan).toEqual({
      "50k": 2,
      "100k": 1,
      "150k": 0,
    });
    expect(snapshot.totalTrackedCombines).toBe(3);
    expect(snapshot.baseCombineCostCents).toBe(2 * 11_500 + 16_800);
    expect(snapshot.standardActivationCostCents).toBe(2 * STANDARD_ACTIVATION_FEE_CENTS);
    expect(snapshot.totalCostCents).toBe(snapshot.baseCombineCostCents + snapshot.standardActivationCostCents);
  });
});

describe("storage-backed helpers", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => (values.has(key) ? values.get(key)! : null),
        setItem: (key: string, value: string) => {
          values.set(key, value);
        },
        removeItem: (key: string) => {
          values.delete(key);
        },
        clear: () => {
          values.clear();
        },
      },
    });
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-01T12:00:00.000Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("decrement helper never drops below zero", () => {
    const before = readCombineSpendSnapshot();

    incrementStandardActivationCount(1);
    const afterIncrement = readCombineSpendSnapshot();
    expect(afterIncrement.standardActivationCount).toBe(before.standardActivationCount + 1);

    decrementStandardActivationCount(1);
    const afterDecrement = readCombineSpendSnapshot();
    expect(afterDecrement.standardActivationCount).toBe(before.standardActivationCount);

    decrementStandardActivationCount(10_000);
    const afterFloor = readCombineSpendSnapshot();
    expect(afterFloor.standardActivationCount).toBe(0);
  });

  it("returns unsynced combine purchases and marks them synced", () => {
    const firstSync = syncCombineSpendTracker([
      { id: 7001, name: "50KTC-7001", status: "ACTIVE" },
      { id: 7002, name: "PA-7002", status: "ACTIVE" },
    ]);

    expect(firstSync.snapshot.totalTrackedCombines).toBe(1);
    expect(firstSync.unsyncedEvaluationPurchases).toEqual([
      {
        accountId: 7001,
        planSize: "50k",
        purchasedOn: "2026-03-01",
        amountCents: 11_500,
      },
    ]);

    const afterFirstMark = markEvaluationExpensesSynced([7001]);
    expect(afterFirstMark.totalTrackedCombines).toBe(1);

    const secondSync = syncCombineSpendTracker([
      { id: 7001, name: "50KTC-7001", status: "ACTIVE" },
      { id: 7003, name: "50KTC-7003", status: "ACTIVE" },
    ]);
    expect(secondSync.snapshot.totalTrackedCombines).toBe(2);
    expect(secondSync.unsyncedEvaluationPurchases).toEqual([
      {
        accountId: 7003,
        planSize: "50k",
        purchasedOn: "2026-03-01",
        amountCents: 11_500,
      },
    ]);

    const afterSecondMark = markEvaluationExpensesSynced([7003]);
    expect(afterSecondMark.totalTrackedCombines).toBe(2);

    const thirdSync = syncCombineSpendTracker([
      { id: 7001, name: "50KTC-7001", status: "ACTIVE" },
      { id: 7003, name: "50KTC-7003", status: "ACTIVE" },
    ]);
    expect(thirdSync.unsyncedEvaluationPurchases).toEqual([]);
  });

  it("keeps locked-out combines tracked and synced", () => {
    const first = syncCombineSpendTracker([{ id: 8101, name: "50KTC-8101", status: "ACTIVE" }]);
    expect(first.unsyncedEvaluationPurchases).toEqual([
      {
        accountId: 8101,
        planSize: "50k",
        purchasedOn: "2026-03-01",
        amountCents: 11_500,
      },
    ]);
    markEvaluationExpensesSynced([8101]);

    const secondSync = syncCombineSpendTracker([
      { id: 8101, name: "50KTC-8101", status: "ACTIVE" },
      { id: 8102, name: "100KTC-8102", status: "ACTIVE" },
    ]);

    expect(secondSync.unsyncedEvaluationPurchases).toEqual([
      {
        accountId: 8102,
        planSize: "100k",
        purchasedOn: "2026-03-01",
        amountCents: 16_800,
      },
    ]);
    markEvaluationExpensesSynced([8102]);

    const lockedSync = syncCombineSpendTracker([
      { id: 8101, name: "50KTC-8101", status: "ACTIVE" },
      { id: 8102, name: "100KTC-8102", status: "LOCKED_OUT" },
    ]);

    expect(lockedSync.snapshot.totalTrackedCombines).toBe(2);
    expect(lockedSync.unsyncedEvaluationPurchases).toEqual([]);

    const xfaSync = syncCombineSpendTracker([
      { id: 8101, name: "50KTC-8101", status: "ACTIVE" },
      { id: 8102, name: "XFA-8102", status: "ACTIVE" },
    ]);
    expect(xfaSync.snapshot.totalTrackedCombines).toBe(2);
    expect(xfaSync.unsyncedEvaluationPurchases).toEqual([]);
  });

  it("keeps using provider names when local display names are customized", () => {
    const sync = syncCombineSpendTracker([
      { id: 8201, name: "Main Desk", provider_name: "50KTC-8201", status: "ACTIVE" },
    ]);

    expect(sync.unsyncedEvaluationPurchases).toEqual([
      {
        accountId: 8201,
        planSize: "50k",
        purchasedOn: "2026-03-01",
        amountCents: 11_500,
      },
    ]);
  });
});
