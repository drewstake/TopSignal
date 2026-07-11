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
