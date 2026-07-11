import { describe, expect, it } from "vitest";

import { canApplyAccountScopedResult } from "./appShellRequests";

describe("canApplyAccountScopedResult", () => {
  it("rejects a completed sync after the active account changes", () => {
    expect(canApplyAccountScopedResult(101, 202, true)).toBe(false);
  });

  it("requires both the same account and the latest request generation", () => {
    expect(canApplyAccountScopedResult(101, 101, false)).toBe(false);
    expect(canApplyAccountScopedResult(101, 101, true)).toBe(true);
  });
});
