import { describe, expect, it } from "vitest";

import { buildPnlCalendarRefreshWindow } from "./pnlCalendarRefresh";

describe("PnL calendar month refresh", () => {
  it("uses Eastern futures-trading-day boundaries for a completed month", () => {
    expect(
      buildPnlCalendarRefreshWindow(
        { startDate: "2026-08-01", endDate: "2026-08-31" },
        new Date("2026-09-04T16:00:00.000Z"),
      ),
    ).toEqual({
      startDate: "2026-08-01",
      endDate: "2026-08-31",
      start: "2026-07-31T22:00:00.000Z",
      end: "2026-08-31T20:59:59.999999Z",
    });
  });

  it("caps an open month at the current instant and rejects a future month", () => {
    const now = new Date("2026-09-04T16:00:00.000Z");

    expect(buildPnlCalendarRefreshWindow({ startDate: "2026-09-01", endDate: "2026-09-30" }, now)?.end).toBe(
      now.toISOString(),
    );
    expect(buildPnlCalendarRefreshWindow({ startDate: "2026-10-01", endDate: "2026-10-31" }, now)).toBeNull();
  });

  it("uses timestamp ordering at a microsecond month boundary", () => {
    const now = new Date("2026-08-31T20:59:59.999Z");

    expect(buildPnlCalendarRefreshWindow({ startDate: "2026-08-01", endDate: "2026-08-31" }, now)?.end).toBe(
      now.toISOString(),
    );
  });
});
