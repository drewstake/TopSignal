// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BacktestTradeAnalysis } from "./BacktestTradeAnalysis";
import { backtestResultFixture } from "./backtestTestFixtures";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const trades = Array.from({ length: 205 }, (_, index) => ({
  ...backtestResultFixture.trades[0], id: index + 1,
  side: index % 2 ? "short" as const : "long" as const,
  net_pnl: index % 2 ? -52.4 : 97.6,
  gross_pnl: index % 2 ? -50 : 100,
  exit_reason: index % 2 ? "stop_loss" : "take_profit",
}));
const result = { ...backtestResultFixture, trades };

describe("BacktestTradeAnalysis", () => {
  it("shows counts and winner/loser timing, then filters details by direction", () => {
    render(<BacktestTradeAnalysis result={result} />);
    const directions = screen.getByRole("table", { name: "Direction performance" });
    expect(within(directions).getByText("205")).toBeTruthy();
    expect(within(directions).getByText("103")).toBeTruthy();
    expect(within(directions).getByText("102")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Analysis direction"), { target: { value: "short" } });
    const timing = screen.getByRole("table", { name: "Holding time by outcome" });
    const winners = within(timing).getByRole("row", { name: /Winners/ });
    expect(within(winners).getByText("0")).toBeTruthy();
    expect(within(winners).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(timing).getByRole("row", { name: /Losers/ }).textContent).toContain("25m");
    expect(screen.getByText("Short trades · 102 trades in each export")).toBeTruthy();
  });

  it("changes the entry grouping without changing the ledger sample", () => {
    render(<BacktestTradeAnalysis result={result} />);
    fireEvent.click(screen.getByRole("button", { name: "Weekday" }));
    expect(within(screen.getByRole("table", { name: "Entry time performance" })).getByText("Friday")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Year" }));
    expect(within(screen.getByRole("table", { name: "Entry time performance" })).getByText("2026")).toBeTruthy();
    expect(screen.getByText("1–100 of 205 trades · page 1 of 3")).toBeTruthy();
  });

  it("paginates, resets on outcome changes, and sorts the actual trade rows", () => {
    render(<BacktestTradeAnalysis result={result} />);
    const ledger = screen.getByRole("table", { name: "Detailed trade ledger" });
    expect(within(ledger).getAllByRole("row")).toHaveLength(101);
    fireEvent.click(screen.getByRole("button", { name: "Next trades" }));
    expect(screen.getByText("101–200 of 205 trades · page 2 of 3")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Ledger outcome"), { target: { value: "loser" } });
    expect(screen.getByText("1–100 of 102 trades · page 1 of 2")).toBeTruthy();
    expect(within(ledger).queryAllByText("winner")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Ledger outcome"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Ledger sort"), { target: { value: "loss" } });
    expect(within(ledger).getAllByRole("row")[1].textContent).toContain("loser");
  });

  it("exports the whole direction selection, with metadata and no page truncation", async () => {
    const blobs: Blob[] = [];
    const createObjectURL = vi.fn((blob: Blob) => { blobs.push(blob); return "blob:test"; });
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<BacktestTradeAnalysis result={result} />);
    fireEvent.click(screen.getByRole("button", { name: "Export trades CSV" }));
    const read = (blob: Blob) => new Promise<string>((resolve) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob);
    });
    const csv = await read(blobs[0]);
    expect(csv.split("\r\n")).toHaveLength(206);
    fireEvent.change(screen.getByLabelText("Analysis direction"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Export analysis JSON" }));
    const report = JSON.parse(await read(blobs[1]));
    expect(report.scope).toMatchObject({ direction: "short", sample: "full_replay", timezone: "America/New_York" });
    expect(report.analysis.overall.count).toBe(102);
    expect(report.input_fingerprint).toBe(result.input_fingerprint);
    expect(report.config_snapshot).toEqual(result.config_snapshot);
  });

  it("handles a replay with no trades without inventing holding times", () => {
    render(<BacktestTradeAnalysis result={{ ...result, trades: [] }} />);
    expect(screen.getByRole("button", { name: "Export trades CSV" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("No closed trades match this selection.")).toBeTruthy();
    expect(screen.getByText("0 of 0 trades · page 1 of 1")).toBeTruthy();
  });
});
