import { describe, expect, it } from "vitest";

import { decideExpenseReconciliation } from "./expenseReconciliation";

describe("decideExpenseReconciliation", () => {
  it("requires an explicit confirmation", () => {
    expect(decideExpenseReconciliation(false, false)).toEqual({ allowed: false, reason: "cancelled" });
    expect(decideExpenseReconciliation(false, true)).toEqual({ allowed: true });
  });

  it("never permits reconciliation in demo mode", () => {
    expect(decideExpenseReconciliation(true, true)).toEqual({ allowed: false, reason: "demo_mode" });
  });
});
