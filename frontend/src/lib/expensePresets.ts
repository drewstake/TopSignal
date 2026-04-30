import type { ExpenseAccountType, ExpenseCategory, ExpensePlanSize } from "./types";

export type ExpenseStage = Extract<ExpenseCategory, "evaluation_fee" | "activation_fee">;
export type ExpenseAccountPresetType = ExpenseAccountType | "no_activation_dll";

interface PlanPreset {
  evaluation_fee: number;
  activation_fee?: number;
}

const NO_ACTIVATION_DLL_PRESETS: Record<ExpensePlanSize, PlanPreset> = {
  "50k": { evaluation_fee: 8_500 },
  "100k": { evaluation_fee: 12_900 },
  "150k": { evaluation_fee: 19_900 },
};

const NO_ACTIVATION_PRESETS: Record<ExpensePlanSize, PlanPreset> = {
  "50k": { evaluation_fee: 9_500 },
  "100k": { evaluation_fee: 14_900 },
  "150k": { evaluation_fee: 22_900 },
};

const STANDARD_PRESETS: Record<ExpensePlanSize, PlanPreset> = {
  "50k": { evaluation_fee: 4_900, activation_fee: 14_900 },
  "100k": { evaluation_fee: 9_900, activation_fee: 14_900 },
  "150k": { evaluation_fee: 14_900, activation_fee: 14_900 },
};

export const EXPENSE_ACCOUNT_TYPES: ExpenseAccountPresetType[] = [
  "no_activation_dll",
  "no_activation",
  "standard",
  "practice",
];
export const EXPENSE_PLAN_SIZES: ExpensePlanSize[] = ["50k", "100k", "150k"];

export function getExpenseAccountTypeLabel(accountType: ExpenseAccountPresetType): string {
  if (accountType === "no_activation_dll") {
    return "No Activation + DLL";
  }
  if (accountType === "no_activation") {
    return "No Activation";
  }
  if (accountType === "standard") {
    return "Standard";
  }
  return "Practice";
}

export function getExpensePresetAmountCents(
  accountType: ExpenseAccountPresetType,
  planSize: ExpensePlanSize,
  stage: ExpenseStage,
): number | null {
  if (accountType === "practice") {
    return null;
  }

  if (planSize === "150k" && accountType !== "no_activation_dll" && accountType !== "no_activation" && accountType !== "standard") {
    return null;
  }

  if (accountType === "no_activation_dll") {
    if (stage !== "evaluation_fee") {
      return null;
    }
    return NO_ACTIVATION_DLL_PRESETS[planSize].evaluation_fee;
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
