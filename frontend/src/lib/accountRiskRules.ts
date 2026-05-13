import {
  getCombinePlanSizeFromAccountName,
  readTrackedCombinePlanSizeForAccountId,
  type CombinePlanSize,
} from "./combineTracker";

export interface AccountRiskRule {
  provider: "Topstep";
  planSize: CombinePlanSize;
  nominalBuyingPower: number;
  profitTarget: number;
  maxLossLimit: number;
}

const TOPSTEP_COMBINE_RISK_RULES: Record<AccountRiskRule["planSize"], AccountRiskRule> = {
  "50k": {
    provider: "Topstep",
    planSize: "50k",
    nominalBuyingPower: 50_000,
    profitTarget: 3_000,
    maxLossLimit: 2_000,
  },
  "100k": {
    provider: "Topstep",
    planSize: "100k",
    nominalBuyingPower: 100_000,
    profitTarget: 6_000,
    maxLossLimit: 3_000,
  },
  "150k": {
    provider: "Topstep",
    planSize: "150k",
    nominalBuyingPower: 150_000,
    profitTarget: 9_000,
    maxLossLimit: 4_500,
  },
};

export function getAccountRiskRuleFromName(name: string): AccountRiskRule | null {
  const planSize = getCombinePlanSizeFromAccountName(name);
  return planSize === null ? null : TOPSTEP_COMBINE_RISK_RULES[planSize];
}

export function getAccountRiskRuleForAccount(account: {
  id: number;
  name: string;
  provider_name?: string | null;
}): AccountRiskRule | null {
  const providerBackedName = account.provider_name || account.name;
  const planSize = getCombinePlanSizeFromAccountName(providerBackedName) ?? readTrackedCombinePlanSizeForAccountId(account.id);
  return planSize === null ? null : TOPSTEP_COMBINE_RISK_RULES[planSize];
}
