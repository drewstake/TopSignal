import { describe, expect, it } from "vitest";

import { getAppliedCustomDateRange, resolveCustomDateRangeDraft } from "./customDateRange";

describe("getAppliedCustomDateRange", () => {
  it("applies a valid range regardless of which endpoint changed last", () => {
    expect(getAppliedCustomDateRange("2026-06-01", "2026-06-30")).toEqual({
      startDate: "2026-06-01",
      endDate: "2026-06-30",
    });
    expect(getAppliedCustomDateRange("2026-06-10", "2026-06-30")).toEqual({
      startDate: "2026-06-10",
      endDate: "2026-06-30",
    });
  });

  it("does not apply incomplete or reversed ranges", () => {
    expect(getAppliedCustomDateRange("", "2026-06-30")).toBeNull();
    expect(getAppliedCustomDateRange("2026-07-01", "2026-06-30")).toBeNull();
  });
});

describe("resolveCustomDateRangeDraft", () => {
  it("clears an applied custom range and returns to All when an endpoint is cleared", () => {
    expect(resolveCustomDateRangeDraft("", "2026-06-30", true)).toEqual({
      appliedRange: null,
      nextMode: "ALL",
    });
  });

  it("suspends an applied custom range when the draft becomes reversed", () => {
    expect(resolveCustomDateRangeDraft("2026-07-01", "2026-06-30", true)).toEqual({
      appliedRange: null,
      nextMode: "ALL",
    });
  });

  it("leaves a selected preset alone while a new custom draft is incomplete", () => {
    expect(resolveCustomDateRangeDraft("2026-06-01", "", false)).toEqual({
      appliedRange: null,
      nextMode: "UNCHANGED",
    });
  });
});
