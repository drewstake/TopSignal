// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "./sessionOrderFlow";
import { type SessionOrderFlow, type FlowWindow } from "./sessionOrderFlow";
import { SessionOrderFlowPanel, SessionOrderFlowView } from "./SessionOrderFlowPanel";
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const window = (buy: number, sell: number): FlowWindow => ({ start: "2026-11-03T23:00:00Z", end: "2026-11-04T00:00:00Z",
  buy, sell, unknown: 3, invalid: 0, bad_time: 0, trades: buy+sell, delta: buy-sell,
  buy_percent: 100*buy/(buy+sell), delta_percent: 100*(buy-sell)/(buy+sell), classified_percent: 95,
  first_price: 100, last_price: 102, first_price_at: "2026-11-03T23:45:00Z", last_price_at: "2026-11-03T23:59:00Z", price_change: 2 });
const sample = (): SessionOrderFlow => ({ schema: "session-provider-flow-v1", contract_id: "CON.F.US.MNQ.Z26", data_live: false,
  generated_at: new Date().toISOString(), session_start: "2026-11-03T23:00:00Z", through: "2026-11-04T00:00:00Z", source: "Test source",
  session: window(40, 60), windows: { "5": window(7, 3), "15": window(20, 10), "60": window(40, 60) },
  classification: { method: "projectx_reported_type", aggressor_verified: false, source: "https://gateway.docs.projectx.com/docs/realtime/" },
  coverage: { status: "partial", reasons: { session_start_missing: 1 }, first_received_at: null, last_received_at: null,
    stale: false, processed_segments: 5, pending_segments: 0, exchange_completeness_verified: false, sealed_only: true },
  bias: "unavailable", absorption: "unavailable", notes: [] });
const props = { accountId: 1, contractId: "CON.F.US.MNQ.Z26", enabled: true, scope: "user-a" };

it("shows qualified reported pressure, price divergence and partial coverage", async () => {
  vi.spyOn(api, "getSessionOrderFlow").mockResolvedValue(sample());
  render(<SessionOrderFlowPanel {...props} />);
  await act(async () => {});
  expect(screen.getByText(/Bearish reported pressure in available recordings; buying leads/)).not.toBeNull();
  expect(screen.getByText("40.0% buy · 60.0% sell")).not.toBeNull();
  expect(screen.getByText("Observed price rose 2 pts")).not.toBeNull();
  expect(screen.getAllByText("3 contracts unclassified")).toHaveLength(2);
  expect(screen.getByText("Recording does not cover the session open")).not.toBeNull();
  expect(screen.getByText(/not a price forecast/)).not.toBeNull();
  expect(screen.getByText(/sample consistency check, not independent exchange verification/)).not.toBeNull();
  fireEvent.change(screen.getByLabelText("Recent order-flow window"), { target: { value: "5" } });
  expect(screen.getByText("70.0% buy · 30.0% sell")).not.toBeNull();
});

it("suppresses current conclusions on stale data, errors and rollover while retaining labeled old readings", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-04T00:01:00Z"));
  const fetch = vi.spyOn(api, "getSessionOrderFlow").mockResolvedValue(sample());
  render(<SessionOrderFlowPanel {...props} />);
  await act(async () => {});
  fetch.mockRejectedValue(new Error("Recording host offline"));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(screen.getByText("Session order flow is unavailable.")).not.toBeNull();
  expect(screen.getByText("Stale / unavailable")).not.toBeNull();
  expect(screen.queryByText(/Bearish reported pressure in available recordings;/)).toBeNull();
});

it("does not turn unknown volume into a directional verdict or invent absorption", () => {
  const value = sample();
  value.session = { ...window(51, 49), unknown: 3 };
  expect(api.reportedBias(value.session)).toBe("uncertain");
  expect(api.flowSummary(value)).toMatch(/pressure is uncertain/);
  expect(api.reportedBias({ ...window(60, 40), invalid: 1 })).toBe("uncertain");
  expect(api.reportedBias({ ...window(50, 50), unknown: 0 })).toBe("balanced");
  expect(api.priceResponse(window(40, 60))).toMatch(/flow\/price divergence/);
  expect(api.priceResponse(window(60, 40))).toMatch(/flow and price agree/);
  expect(api.priceResponse({ ...window(60, 40), price_change: 0 })).toMatch(/prices are equal/);
});

it("clears prior account and delivery readings immediately, ignoring late responses", async () => {
  let finish: (value: SessionOrderFlow) => void = () => {};
  const fetch = vi.spyOn(api, "getSessionOrderFlow").mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const view = render(<SessionOrderFlowPanel {...props} />);
  fetch.mockReturnValue(new Promise(() => {}));
  view.rerender(<SessionOrderFlowPanel {...props} accountId={2} contractId="CON.F.US.MNQ.H27" scope="user-b" />);
  await act(async () => { finish(sample()); });
  expect(screen.queryByText("40.0% buy · 60.0% sell")).toBeNull();
  expect(screen.getByText("Checking recorded trades…")).not.toBeNull();
});

it("does not request live data in demo mode or without an account", async () => {
  const fetch = vi.spyOn(api, "getSessionOrderFlow");
  const view = render(<SessionOrderFlowPanel {...props} enabled={false} />);
  await act(async () => {});
  expect(fetch).not.toHaveBeenCalled();
  view.rerender(<SessionOrderFlowPanel {...props} accountId={null} />);
  await act(async () => {});
  expect(fetch).not.toHaveBeenCalled();
});

it("labels incomplete builds without treating zero counts as balanced", () => {
  const value = sample(); value.coverage.status = "building";
  expect(api.flowSummary(value)).toBe("Building session history from recorded trades.");
  expect(api.reportedPressure({ ...value.session, buy: 0, sell: 0, buy_percent: null })).toBe("No classified trades");
});

it("withdraws yesterday's verdict immediately at the New York session boundary", () => {
  const flow = sample();
  flow.generated_at = "2026-11-04T22:59:59Z";
  render(<SessionOrderFlowView {...props} flow={flow} error={null} minutes="15" setMinutes={() => {}}
    clock={Date.parse("2026-11-04T23:00:00Z")} />);
  expect(screen.getByText("New session started. Awaiting fresh session recordings.")).not.toBeNull();
  expect(screen.getByText("Previous session volume")).not.toBeNull();
  expect(screen.queryByText(/Bearish reported pressure/)).toBeNull();
});

it("keeps the 6 p.m. boundary correct across daylight saving and the weekend", () => {
  expect(api.sessionKey(Date.parse("2026-11-01T22:59:59Z"))).toBe("2026-10-29");
  expect(api.sessionKey(Date.parse("2026-11-01T23:00:00Z"))).toBe("2026-11-01");
  expect(api.sessionKey(Date.parse("2026-03-08T21:59:59Z"))).toBe("2026-03-05");
  expect(api.sessionKey(Date.parse("2026-03-08T22:00:00Z"))).toBe("2026-03-08");
});
