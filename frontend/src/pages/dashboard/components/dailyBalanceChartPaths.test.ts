import { describe, expect, it } from "vitest";

import { buildInterpolatedAreaPath, buildInterpolatedLinePath } from "./dailyBalanceChartPaths";

describe("buildInterpolatedLinePath", () => {
  it("uses cubic segments that terminate on each input point", () => {
    const points = [
      { x: 0, y: 24 },
      { x: 40, y: 8 },
      { x: 80, y: 32 },
      { x: 120, y: 16 },
    ];

    const path = buildInterpolatedLinePath(points);

    expect(path.startsWith("M 0 24 C ")).toBe(true);
    expect(path).toContain(" 40 8 C ");
    expect(path).toContain(" 80 32 C ");
    expect(path.endsWith("120 16")).toBe(true);
  });

  it("keeps the one-point and two-point fallbacks intact", () => {
    expect(buildInterpolatedLinePath([{ x: 10, y: 20 }])).toBe("M 10 20");
    expect(
      buildInterpolatedLinePath([
        { x: 10, y: 20 },
        { x: 30, y: 40 },
      ]),
    ).toBe("M 10 20 L 30 40");
  });
});

describe("buildInterpolatedAreaPath", () => {
  it("closes the filled area against the baseline", () => {
    const points = [
      { x: 10, y: 40 },
      { x: 30, y: 20 },
      { x: 50, y: 60 },
    ];

    expect(buildInterpolatedAreaPath(points, 80)).toBe(
      "M 10 40 C 13.333333333333334 36.666666666666664 23.333333333333332 16.666666666666668 30 20 C 36.666666666666664 23.333333333333332 46.666666666666664 53.333333333333336 50 60 L 50 80 L 10 80 Z",
    );
  });
});
