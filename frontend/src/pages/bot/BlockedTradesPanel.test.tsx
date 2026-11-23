// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { requestJson } from "../../lib/api";
import { BlockedTradesPanel, type BlockedReport } from "./BlockedTradesPanel";

vi.mock("../../lib/api", () => ({ requestJson: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("shows the reason, executable prices, outcome and resolved denominator", async () => {
  const report: BlockedReport = { account_id: 7, config_id: 40, reader_at: new Date().toISOString(), next_before: null,
    counts: { TP: 1, SL: 1, other: 0, open: 2, unresolved: 3 }, wins: 1, denominator: 2,
    trades: [{ id: 1, at: "2026-11-23T14:00:00Z", side: "BUY", quantity: 2, reason: "daily_loss_limit_reached",
      entry: 20000.25, tp: 20025.25, sl: 19975.25, status: "TP", exit_at: "2026-11-23T14:05:00Z",
      exit_price: 20025.25, detail: "Target", net: 97.56 }] };
  vi.mocked(requestJson).mockResolvedValue(report);
  render(<BlockedTradesPanel account={7} config={40} />);
  expect(await screen.findByText("Daily loss stop")).toBeTruthy();
  expect(screen.getByText("$97.56")).toBeTruthy();
  expect(screen.getByText("1 profitable / 2 resolved simulated trades after fees.")).toBeTruthy();
  expect(screen.getByText("50.0%")).toBeTruthy();
  expect(requestJson).toHaveBeenCalledWith("/api/accounts/7/topbot/40/blocked-trades", expect.anything());
});
