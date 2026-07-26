// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompactMetricCard, InfoPopover } from "./CompactDashboardPrimitives";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Compact dashboard primitives", () => {
  it("portals and collision-shifts an info tooltip inside a 320px viewport", () => {
    vi.spyOn(window, "innerWidth", "get").mockReturnValue(320);
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(200);
    render(
      <div style={{ overflow: "hidden" }}>
        <InfoPopover triggerLabel="Net P&L" label="Net result after fees." align="end" />
      </div>,
    );
    const trigger = screen.getByRole("button", { name: "Net P&L information" });
    vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
      x: 300,
      y: 170,
      top: 170,
      right: 316,
      bottom: 190,
      left: 300,
      width: 16,
      height: 20,
      toJSON: () => ({}),
    });

    fireEvent.click(trigger);
    const tooltip = screen.getByRole("tooltip");
    const left = Number.parseFloat(tooltip.style.left);
    const width = Number.parseFloat(tooltip.style.width);
    expect(tooltip.parentElement).toBe(document.body);
    expect(left).toBeGreaterThanOrEqual(8);
    expect(left + width).toBeLessThanOrEqual(312);
    expect(Number.parseFloat(tooltip.style.top)).toBeLessThan(170);
    expect(trigger.getAttribute("aria-describedby")).toBe(tooltip.id);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("labels metric regions and never exposes stale values while loading", () => {
    const { rerender } = render(
      <CompactMetricCard
        label="Net P&L"
        info="Net result."
        value="+$900"
        kind="net"
        loading
        error={null}
      />,
    );
    expect(screen.getByRole("region", { name: "Net P&L" })).toBeTruthy();
    expect(screen.queryByText("+$900")).toBeNull();
    expect(screen.getByText("Loading Net P&L")).toBeTruthy();

    rerender(
      <CompactMetricCard
        label="Net P&L"
        info="Net result."
        value="+$900"
        kind="net"
        loading={false}
        error={null}
      />,
    );
    expect(screen.getByText("+$900")).toBeTruthy();
  });

  it("lets semantic gain and loss tones replace the neutral metric color", () => {
    render(
      <CompactMetricCard
        label="Net P&L"
        info="Net result."
        value="+$900"
        valueClassName="text-app-positive-text"
        kind="net"
        loading={false}
        error={null}
      />,
    );

    const value = screen.getByText("+$900");
    expect(value.className).toContain("text-app-positive-text");
    expect(value.className).not.toContain("text-app-text");
    const region = screen.getByRole("region", { name: "Net P&L" });
    const glyph = Array.from(region.querySelectorAll('span[aria-hidden="true"]'))
      .find((element) => element.className.includes("h-10"));
    expect(glyph?.parentElement?.className).toContain("max-[479px]:hidden");
  });
});
