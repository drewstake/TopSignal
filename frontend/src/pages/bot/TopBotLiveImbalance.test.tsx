// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { TopBotLiveImbalance, type TopBotDepthObservation } from "./TopBotLiveImbalance";
const visibility = vi.hoisted(() => ({ visible: true }));
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => visibility.visible }));
const contractId = "CON.F.US.MNQ.Z26";
const props = { contractId, threshold: .1, requiredSeconds: 30, enabled: true };
function reading(patch: Partial<TopBotDepthObservation["depth"]> = {}): TopBotDepthObservation {
  return { contract_id: contractId, as_of: new Date().toISOString(), threshold: .1,
    depth: { ready: true, reason: "", bid_total: 1200, ask_total: 1000, bid_levels: 100, ask_levels: 100,
      imbalance: 200/2200, persistence_seconds: 0, observed_at: new Date().toISOString(), ...patch } };
}
beforeEach(() => { vi.useFakeTimers(); visibility.visible = true; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
async function flush() { await act(async () => {}); }

it("shows normalized imbalance and the configured threshold, rather than percent difference", async () => {
  vi.spyOn(botsApi, "getTopBotDepth").mockResolvedValue(reading());
  render(<TopBotLiveImbalance {...props} />); await flush();
  expect(screen.getByText("+9.1%")).toBeTruthy();
  expect(screen.getByText("Entry threshold ±10%")).toBeTruthy();
  expect(screen.getByText("1,200")).toBeTruthy();
  expect(screen.getByText("1,000")).toBeTruthy();
  expect(screen.getByText("0.0 / 30s")).toBeTruthy();
});

it("shows the contrarian long side and only the worker-observed persistence", async () => {
  const call = vi.spyOn(botsApi, "getTopBotDepth").mockResolvedValue(reading({ imbalance: -.15, persistence_seconds: 12.4 }));
  render(<TopBotLiveImbalance {...props} />); await flush();
  expect(screen.getByText("Ask-heavy → long setup")).toBeTruthy();
  expect(screen.getByText("12.4 / 30s")).toBeTruthy();
  call.mockImplementation(() => new Promise(() => {}));
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  expect(screen.getByText("12.4 / 30s")).toBeTruthy(); // No optimistic wall-clock timer.
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText("Stale / unavailable")).toBeTruthy();
  expect(screen.queryByText("12.4 / 30s")).toBeNull();
  expect(screen.getByRole("meter").getAttribute("aria-valuenow")).toBeNull();
});

it("shows a qualified 99-level book without requiring 100 levels", async () => {
  vi.spyOn(botsApi, "getTopBotDepth").mockResolvedValue(reading({ ready: true, bid_levels: 99, imbalance: -.2, persistence_seconds: 31 }));
  render(<TopBotLiveImbalance {...props} />); await flush();
  expect(screen.getByText("-20.0%")).toBeTruthy();
  expect(screen.getByText("99 levels (up to 100)")).toBeTruthy();
  expect(screen.getByText("Live")).toBeTruthy();
  expect(screen.getByText("31.0 / 30s")).toBeTruthy();
  expect(screen.getByText(/Duration met/)).toBeTruthy();
});

it("polls once per second without overlapping or invoking evaluations", async () => {
  const call = vi.spyOn(botsApi, "getTopBotDepth").mockImplementation(async () => reading());
  render(<TopBotLiveImbalance {...props} />); await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(call).toHaveBeenCalledTimes(2);
  const pending = new Promise<TopBotDepthObservation>(() => {});
  call.mockReturnValue(pending);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
  expect(call).toHaveBeenCalledTimes(3);
  cleanup();
  expect(call.mock.calls[2][1]?.signal?.aborted).toBe(true);
});

it("does not fetch when disabled or hidden, and never reuses a different contract's totals", async () => {
  const call = vi.spyOn(botsApi, "getTopBotDepth").mockResolvedValue(reading());
  const view = render(<TopBotLiveImbalance {...props} enabled={false} />); await flush();
  expect(call).not.toHaveBeenCalled();
  visibility.visible = false;
  view.rerender(<TopBotLiveImbalance {...props} />); await flush();
  expect(call).not.toHaveBeenCalled();
  visibility.visible = true;
  view.rerender(<TopBotLiveImbalance {...props} />); await flush();
  expect(screen.getByText("+9.1%")).toBeTruthy();
  call.mockImplementation(() => new Promise(() => {}));
  view.rerender(<TopBotLiveImbalance {...props} contractId="different" />); await flush();
  expect(screen.queryByText("+9.1%")).toBeNull();
});

it("does not label a failed refresh live", async () => {
  const call = vi.spyOn(botsApi, "getTopBotDepth").mockResolvedValue(reading({ imbalance: .2, persistence_seconds: 31 }));
  render(<TopBotLiveImbalance {...props} />); await flush();
  expect(screen.getByText(/Duration met/)).toBeTruthy();
  call.mockRejectedValue(new Error("offline"));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText("Connection interrupted")).toBeTruthy();
  expect(screen.queryByText(/Duration met/)).toBeNull();
});

it("still explains threshold resets while allowing fewer than 100 levels", async () => {
  vi.spyOn(botsApi, "getTopBotDepth").mockResolvedValue(reading({ imbalance: .159, bid_levels: 99, persistence_seconds: .1,
    qualification_reset: { reason: "below_threshold", at: new Date().toISOString(), bid_levels: 99, ask_levels: 99, imbalance: .09 } }));
  render(<TopBotLiveImbalance {...props} />); await flush();
  expect(screen.getByText("Time above threshold")).toBeTruthy();
  expect(screen.getByText("0.1 / 30s")).toBeTruthy();
  expect(screen.getByText(/Last timer reset: Imbalance fell below the threshold/)).toBeTruthy();
  expect(screen.getByText(/Level-count changes alone do not reset the timer/)).toBeTruthy();
});
