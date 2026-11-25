import {
  getCombinePlanSizeFromAccountName,
  readTrackedCombinePlanSizeForAccountId,
  type CombinePlanSize,
} from "./combineTracker";

export interface AccountRiskRule {
  provider: "Topstep";
  planSize: CombinePlanSize | "250k";
  dailyLossLimit?: number;
  consistencyTargetPercent?: number;
  nominalBuyingPower: number;
  profitTarget: number;
  maxLossLimit: number;
}

const TOPSTEP_COMBINE_RISK_RULES: Record<AccountRiskRule["planSize"], AccountRiskRule> = {
  "250k": {
    provider: "Topstep",
    planSize: "250k",
    nominalBuyingPower: 250_000,
    profitTarget: 15_000,
    maxLossLimit: 10_000,
    dailyLossLimit: 5_000,
    consistencyTargetPercent: 55,
  },
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
  const normalized = name.trim().toUpperCase().replaceAll("_", " ");
  if (/\b250K(?:TC)?\b/.test(normalized) && /\b(?:LABS?[ -]?#?002|FREEDOM)\b/.test(normalized)
    && !/\b(?:XFA|FUNDED|LIVE)\b/.test(normalized)) {
    return TOPSTEP_COMBINE_RISK_RULES["250k"];
  }
  const planSize = getCombinePlanSizeFromAccountName(name);
  return planSize === null ? null : TOPSTEP_COMBINE_RISK_RULES[planSize];
}

export function getAccountRiskRuleForAccount(account: {
  id: number;
  name: string;
  provider_name?: string | null;
}): AccountRiskRule | null {
  const providerBackedName = account.provider_name || account.name;
  const namedRule = getAccountRiskRuleFromName(providerBackedName);
  if (namedRule) return namedRule;
  const planSize = readTrackedCombinePlanSizeForAccountId(account.id);
  return planSize === null ? null : TOPSTEP_COMBINE_RISK_RULES[planSize];
}
