import { describe, expect, it, vi } from "vitest";

import { computeCopyTradeWhenEnabled } from "./copyTradePerformance";

describe("copy-trade performance gate", () => {
  it("returns the stable disabled value without running the computation", () => {
    const disabledValue: string[] = [];
    const compute = vi.fn(() => ["expensive result"]);

    expect(computeCopyTradeWhenEnabled(false, disabledValue, compute)).toBe(disabledValue);
    expect(compute).not.toHaveBeenCalled();
  });

  it("runs the computation after Copy Trade Mode is enabled", () => {
    const compute = vi.fn(() => ["enabled result"]);

    expect(computeCopyTradeWhenEnabled(true, [], compute)).toEqual(["enabled result"]);
    expect(compute).toHaveBeenCalledTimes(1);
  });
});
