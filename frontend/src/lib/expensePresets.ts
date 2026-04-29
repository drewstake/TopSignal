import type { ExpenseAccountType, ExpenseCategory, ExpensePlanSize } from "./types";

export type ExpenseStage = Extract<ExpenseCategory, "evaluation_fee" | "activation_fee">;

interface PlanPreset {
  evaluation_fee: number;
  activation_fee?: number;
}

const NO_ACTIVATION_PRESETS: Record<ExpensePlanSize, PlanPreset> = {
  "50k": { evaluation_fee: 9_500 },
  "100k": { evaluation_fee: 14_900 },
  "150k": { evaluation_fee: 22_900 },
};

const STANDARD_PRESETS: Record<ExpensePlanSize, PlanPreset> = {
  "50k": { evaluation_fee: 5_100, activation_fee: 15_000 },
  "100k": { evaluation_fee: 10_500, activation_fee: 15_000 },
  "150k": { evaluation_fee: 15_900, activation_fee: 15_000 },
};

export const EXPENSE_ACCOUNT_TYPES: ExpenseAccountType[] = ["no_activation", "standard", "practice"];
export const EXPENSE_PLAN_SIZES: ExpensePlanSize[] = ["50k", "100k", "150k"];

export function getExpensePresetAmountCents(
  accountType: ExpenseAccountType,
  planSize: ExpensePlanSize,
  stage: ExpenseStage,
): number | null {
  if (accountType === "practice") {
    return null;
  }

  if (planSize === "150k" && accountType !== "no_activation" && accountType !== "standard") {
    return null;
  }

  if (accountType === "no_activation") {
    if (stage !== "evaluation_fee") {
      return null;
    }
    return NO_ACTIVATION_PRESETS[planSize].evaluation_fee;
  }

  const planPreset = STANDARD_PRESETS[planSize];
  if (stage === "activation_fee") {
    return planPreset.activation_fee ?? null;
  }
  return planPreset.evaluation_fee;
}
