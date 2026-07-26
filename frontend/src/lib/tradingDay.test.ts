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

  it("rolls 6:28 PM ET during daylight saving time to the next session", () => {
    expect(tradingDayKey(new Date("2026-05-28T22:28:00.000Z"))).toBe("2026-05-29");
  });
});

describe("getTradingDayRange", () => {
  it("returns UTC boundaries for a trading day key", () => {
    expect(getTradingDayRange("2026-03-03")).toEqual({
      start: "2026-03-02T23:00:00.000Z",
      end: "2026-03-03T21:59:59.999999Z",
    });
  });

  it("returns daylight-saving UTC boundaries for a trading day key", () => {
    expect(getTradingDayRange("2026-05-29")).toEqual({
      start: "2026-05-28T22:00:00.000Z",
      end: "2026-05-29T20:59:59.999999Z",
    });
  });

  it("uses the final inclusive database microsecond before the 5 PM ET close", () => {
    const range = getTradingDayRange("2026-01-13");

    expect(range?.end).toBe("2026-01-13T21:59:59.999999Z");
    expect(new Date(range!.end).getTime() + 1).toBe(new Date("2026-01-13T22:00:00.000Z").getTime());
  });

  it("keeps an overnight session on one trading-day key", () => {
    expect(getTradingDayRange("2026-07-14")).toEqual({
      start: "2026-07-13T22:00:00.000Z",
      end: "2026-07-14T20:59:59.999999Z",
    });
  });

  it("accounts for the spring-forward transition within a boundary range", () => {
    const range = getTradingDayRange("2026-03-08");

    expect(range).toEqual({
      start: "2026-03-07T23:00:00.000Z",
      end: "2026-03-08T20:59:59.999999Z",
    });
    expect(new Date(range!.end).getTime() - new Date(range!.start).getTime() + 1).toBe(22 * 60 * 60 * 1000);
  });

  it("accounts for the fall-back transition within a boundary range", () => {
    const range = getTradingDayRange("2026-11-01");

    expect(range).toEqual({
      start: "2026-10-31T22:00:00.000Z",
      end: "2026-11-01T21:59:59.999999Z",
    });
    expect(new Date(range!.end).getTime() - new Date(range!.start).getTime() + 1).toBe(24 * 60 * 60 * 1000);
  });
});

describe("getCalendarDayRange", () => {
  it("returns midnight ET boundaries for a calendar day", () => {
    expect(getCalendarDayRange("2026-03-03")).toEqual({
      start: "2026-03-03T05:00:00.000Z",
      end: "2026-03-04T04:59:59.999999Z",
    });
  });
});
