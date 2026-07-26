// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CopyFullStatsButton, type CopyFullStatsMetrics } from "./CopyFullStatsButton";

afterEach(cleanup);

describe("CopyFullStatsButton deferred coaching", () => {
  it("does not inspect coaching metrics while the summary dialog is closed", () => {
    const guardedMetrics = new Proxy({} as CopyFullStatsMetrics, {
      get() {
        throw new Error("closed summary accessed metrics");
      },
    });

    render(<CopyFullStatsButton metrics={guardedMetrics} rangeLabel="All time" />);

    expect(screen.getByRole("button", { name: "Summary" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy Full Stats" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Trading Summary" })).toBeNull();
  });
});
