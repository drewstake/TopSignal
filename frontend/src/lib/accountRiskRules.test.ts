import { describe, expect, it } from "vitest";

import { getAccountRiskRuleForAccount, getAccountRiskRuleFromName } from "./accountRiskRules";

describe("getAccountRiskRuleFromName", () => {
  it("maps Topstep combine account names to max loss limits", () => {
    expect(getAccountRiskRuleFromName("50KTC-12345")?.maxLossLimit).toBe(2_000);
    expect(getAccountRiskRuleFromName("100KTC-12345")?.maxLossLimit).toBe(3_000);
    expect(getAccountRiskRuleFromName("150KTC-12345")?.maxLossLimit).toBe(4_500);
  });

  it("includes the matching profit targets", () => {
    expect(getAccountRiskRuleFromName("50KTC-12345")?.profitTarget).toBe(3_000);
    expect(getAccountRiskRuleFromName("100KTC-12345")?.profitTarget).toBe(6_000);
    expect(getAccountRiskRuleFromName("150KTC-12345")?.profitTarget).toBe(9_000);
  });

  it("ignores accounts without a known combine prefix", () => {
    expect(getAccountRiskRuleFromName("XFA-12345")).toBeNull();
    expect(getAccountRiskRuleFromName("Account 7001")).toBeNull();
  });

  it("uses provider names before custom display names", () => {
    expect(
      getAccountRiskRuleForAccount({
        id: 101,
        name: "Main scalp account",
        provider_name: "50KTC-101",
      })?.maxLossLimit,
    ).toBe(2_000);
  });
});
