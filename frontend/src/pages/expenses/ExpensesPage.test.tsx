import { describe, expect, it } from "vitest";

import { buildAnniversaryYearRangeOptions } from "./expenseNetRanges";

describe("buildAnniversaryYearRangeOptions", () => {
  it("builds started anniversary years and caps the current year at today", () => {
    const ranges = buildAnniversaryYearRangeOptions("2025-03-31", new Date(2026, 5, 25));

    expect(ranges).toEqual([
      {
        key: "anniversary_year_1",
        label: "Year 1",
        dateRange: {
          startDate: "2025-03-31",
          endDate: "2026-03-30",
        },
      },
      {
        key: "anniversary_year_2",
        label: "Year 2",
        dateRange: {
          startDate: "2026-03-31",
          endDate: "2026-06-25",
        },
      },
    ]);
  });

  it("does not include future anniversary years before they start", () => {
    const ranges = buildAnniversaryYearRangeOptions("2025-03-31", new Date(2026, 2, 30));

    expect(ranges).toHaveLength(1);
    expect(ranges[0]?.label).toBe("Year 1");
  });
});
