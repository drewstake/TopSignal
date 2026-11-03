// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { TopBotExecutionEvidence, TopBotRecordingStatus } from "./TopBotExecutionEvidence";

afterEach(cleanup);
it("keeps old or missing measurements unknown", () => {
  const view = render(<TopBotExecutionEvidence />);
  expect(screen.getByText("Not recorded")).toBeTruthy();
  view.rerender(<TopBotExecutionEvidence execution={{ entry_slippage_ticks: null }} />);
  expect(screen.getAllByText("Unknown")).toHaveLength(5);
});
it("shows measured zero, clock skew, and open gaps without claiming completeness", () => {
  render(<TopBotExecutionEvidence execution={{ submission: { request_duration_ms: 420 }, entry_slippage_ticks: 0, fill_to_observation_ms: -50 }}
    coverage={{ gap_count: 2, gap_seconds: 6, open_gap: { reason: "disconnected", start: "2026-11-03T12:00:00Z" } }} />);
  expect(screen.getByText("420 ms")).toBeTruthy();
  expect(screen.getByText("0.00 ticks")).toBeTruthy();
  expect(screen.getByText("-50 ms")).toBeTruthy();
  expect(screen.getByText(/Gap still open/)).toBeTruthy();
  expect(screen.getByText(/clock offset is unverified/)).toBeTruthy();
});
it("reports recording loss and storage stops", () => {
  const view = render(<TopBotRecordingStatus evidence={{ recording: { state: "recording", dropped_events: 4 } }} />);
  expect(screen.getByRole("status").textContent).toContain("4 capture events missing");
  view.rerender(<TopBotRecordingStatus evidence={{ recording: { state: "storage_blocked" } }} />);
  expect(screen.getByRole("status").textContent).toContain("Paused for storage review");
});
