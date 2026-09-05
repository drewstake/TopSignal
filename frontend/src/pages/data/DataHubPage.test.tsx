// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DataHubPage } from "./DataHubPage";

const state = vi.hoisted(() => ({ account: 1 as number | null, demo: false, request: vi.fn() }));
vi.mock("../../lib/api", () => ({ requestJson: state.request }));
vi.mock("../../lib/demoMode", () => ({ useDemoMode: () => ({ enabled: state.demo }) }));
vi.mock("react-router-dom", async () => ({ ...await vi.importActual<typeof import("react-router-dom")>("react-router-dom"), useOutletContext: () => ({ selectedAccountId: state.account }) }));

const research = (reason = "Test decision") => ({ items: [{ id: 1, decision_id: 1, observed_at: "2026-09-05T10:00:00Z", contract_id: "MNQ", action: "HOLD", reason, score: null, outcome: "no_geometry" }], summary: { total: 1, pending: 0, labeled: 0, ambiguous: 0 }, execution: { matched_orders: 0, order_attempts: 0, matched_fill_count: 0, mean_signed_price_difference: null, limitations: [] } });
function defaults(path: string, options?: { method?: string }) {
  if (options?.method === "POST") return Promise.resolve({ inserted_rows: 10, unchanged_rows: 20, conflicting_rows: 1 });
  if (path === "/api/market-data/inventory") return Promise.resolve({ generated_at: "2026-09-05T10:00:00Z", database_rows: 100, streams: [], archive: { status: "available", series: [], schemas: {}, note: "Stored archive" }, local_capture: { status: "available", rows: 30, matching_database_rows: 20, note: "Verified capture", research_exposure: "Previously evaluated history" }, feeds: [{ key: "depth", label: "Depth feed", status: "unconfirmed", detail: "Best bid and ask only" }] });
  if (path === "/api/market-data/context") return Promise.resolve({ items: [{ symbol: "VIX", status: "missing", close: null, change_pct: null, candle_timestamp: null }], note: "Closed candles only" });
  if (path === "/api/market-data/public-status") return Promise.resolve({ generated_at: "2026-09-05T10:00:00Z", sources: [{ symbol: "US10Y", source: "federal_reserve_h15", label: "US 10-year Treasury yield", status: "ready", enabled: true, stored_rows: 0, latest_observation_date: null, source_url: "https://www.federalreserve.gov/releases/h15/", data_notice: "Daily annual yield; not a traded price" }, { symbol: "VIX", source: "cboe", label: "Cboe VIX", status: "disabled", enabled: false, stored_rows: 0, latest_observation_date: null, source_url: "https://www.cboe.com/", data_notice: "Requires appropriate data-use rights" }] });
  if (path === "/api/market-events") return Promise.resolve({ events: [], sources: [{ source: "bls", label: "BLS calendar", status: "error", event_count: 0, coverage_scope: "BLS schedule only", error_code: "http_403", actuals_available: false, consensus_available: false }], risk: { level: "unknown", coverage_trusted: false, reason: "Partial calendar coverage" } });
  if (path === "/api/market-observations/status") return Promise.resolve({ enabled: true, capture_mode: "viewer_driven", retention_days: 3, event_count: 0, counts: {}, contracts: [], profile: { trade_count: 0, delta: null, levels: [], basis: "observed_gateway_trade_prints" }, spread: { latest: null }, warnings: [] });
  if (path === "/api/decision-research") return Promise.resolve(research());
  return Promise.reject(new Error("Unexpected path"));
}
beforeEach(() => { state.account = 1; state.demo = false; state.request.mockReset().mockImplementation(defaults); });
afterEach(cleanup);
const page = () => <MemoryRouter><DataHubPage /></MemoryRouter>;

describe("Data hub", () => {
  it("shows missing feeds and keeps unavailable data distinct from zero", async () => {
    render(page());
    await screen.findByText("VIX");
    expect(screen.getByText("missing")).toBeTruthy();
    expect(screen.getByText("Best bid and ask only")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Calendar & news" }));
    await screen.findByText("BLS calendar");
    expect(screen.getByText("http 403")).toBeTruthy();
    expect(screen.getByText(/Actuals: unavailable/)).toBeTruthy();
    expect(state.request.mock.calls.some(([, options]) => options?.method === "POST")).toBe(false);
  });
  it("imports explicitly and reports conflicts without overwriting", async () => {
    render(page());
    const button = await screen.findByRole("button", { name: "Import captured history" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    expect(await screen.findByText("History import: 10 added, 20 already present, 1 conflicts preserved.")).toBeTruthy();
    expect(state.request).toHaveBeenCalledWith("/api/market-data/import-local-history", expect.objectContaining({ method: "POST", body: undefined }));
  });
  it("makes no live requests in demo mode", () => {
    state.demo = true; render(page());
    expect(screen.getByText(/Data collection is unavailable in Demo Mode/)).toBeTruthy();
    expect(state.request).not.toHaveBeenCalled();
  });
  it("refreshes only enabled public sources and renders yields in basis points", async () => {
    state.request.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/market-data/context") return Promise.resolve({ items: [{ symbol: "US10Y", status: "stale", close: 4.1, change_pct: null, change_bps: -2, observation_date: "2026-09-04", source: "federal_reserve_h15" }] });
      if (path === "/api/market-data/refresh-public") return Promise.resolve({ items: [{ symbol: "US10Y", status: "updated", inserted_rows: 250, detail: "Daily observations collected" }] });
      return defaults(path, options);
    });
    render(page());
    expect(await screen.findByText("4.1%", { exact: false })).toBeTruthy();
    expect(screen.getByText("-2 bps")).toBeTruthy();
    expect(screen.getByText("Daily reference · 2026-09-04")).toBeTruthy();
    const button = screen.getByRole("button", { name: "Collect public references" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await screen.findByText(/250 new observations/);
    expect(state.request).toHaveBeenCalledWith("/api/market-data/refresh-public", expect.objectContaining({ method: "POST", body: { symbols: ["US10Y"], days: 365 } }));
  });
  it("hides prior-account decisions and ignores a late response", async () => {
    let finish: (value: unknown) => void = () => {};
    state.request.mockImplementation((path: string, options?: {query?: {account_id?: number}}) => path === "/api/decision-research" && options?.query?.account_id === 1 ? new Promise(resolve => { finish = resolve; }) : defaults(path));
    const view = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Decision research" }));
    state.account = 2; view.rerender(page());
    await screen.findByText("Test decision");
    await act(async () => { finish(research("Private old-account decision")); });
    expect(screen.queryByText("Private old-account decision")).toBeNull();
    expect(screen.getByText("Test decision")).toBeTruthy();
  });
  it("shows the full date-only meeting range without inventing an announcement time", async () => {
    state.request.mockImplementation((path: string, options?: { method?: string }) => {
      if (path === "/api/market-events") return Promise.resolve({ events: [{ id: "fomc", source: "federal_reserve_calendar", title: "FOMC meeting", time_precision: "date", scheduled_date: "2026-09-15", scheduled_end_at: "2026-09-17T04:00:00Z", state: "scheduled", importance: "high" }], sources: [], risk: { level: "unknown", coverage_trusted: false, reason: "Partial coverage" } });
      return defaults(path, options);
    });
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Calendar & news" }));
    expect(await screen.findByText("Sep 15, 2026 – Sep 16, 2026 · date only")).toBeTruthy();
  });
});
