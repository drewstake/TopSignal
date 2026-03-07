import { describe, expect, it } from "vitest";

import { getCalendarDayRange, getTradingDayRange, tradingDayKey } from "./tradingDay";

describe("tradingDayKey", () => {
  it("keeps 5:59 PM ET on the same trading day", () => {
    expect(tradingDayKey(new Date("2026-03-02T22:59:00.000Z"))).toBe("2026-03-02");
  });

  it("rolls 6:00 PM ET to the next trading day", () => {
    expect(tradingDayKey(new Date("2026-03-02T23:00:00.000Z"))).toBe("2026-03-03");
  });

  it("rolls Monday 6:09 PM ET to Tuesday (reported case)", () => {
    expect(tradingDayKey(new Date("2026-03-02T23:09:00.000Z"))).toBe("2026-03-03");
  });
});

describe("getTradingDayRange", () => {
  it("returns UTC boundaries for a trading day key", () => {
    expect(getTradingDayRange("2026-03-03")).toEqual({
      start: "2026-03-02T23:00:00.000Z",
      end: "2026-03-03T22:59:59.999Z",
    });
  });
});

describe("getCalendarDayRange", () => {
  it("returns midnight ET boundaries for a calendar day", () => {
    expect(getCalendarDayRange("2026-03-03")).toEqual({
      start: "2026-03-03T05:00:00.000Z",
      end: "2026-03-04T04:59:59.999Z",
    });
  });
});
