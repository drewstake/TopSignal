// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { TopBotStrategyPanel, type TopBotStatistics } from "./TopBotStrategyPanel";
import type { BotActivity, BotConfig } from "../../lib/types";
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));
const fixedFuture = { exit_mode: "fixed" as const, take_profit_points: 25, initial_stop_points: 25, trail_activation_points: null,
  trail_points: null, persistent: false, fixed_bracket_session: "2026-11-04", bot_enabled: true, source: "Bot configuration" };
const trailingFuture = { ...fixedFuture, exit_mode: "trailing" as const, take_profit_points: null, trail_activation_points: 25, trail_points: 25 };
const stats: TopBotStatistics = { scope: "Account only", items: [], closed_trades: 2, wins: 1, losses: 1,
  net_pnl_usd: 23.56, winner_hold_mean_seconds: 420, winner_hold_median_seconds: 420,
  loser_hold_mean_seconds: 120, loser_hold_median_seconds: 120, account_id: 123,
  exit_settings: { future_entries: fixedFuture, open_trades: [], as_of: "2026-11-04T15:00:00Z" } };
beforeEach(() => {
  vi.spyOn(botsApi, "getTopBotStatistics").mockResolvedValue(stats);
  vi.spyOn(botsApi, "getTopBotAudit").mockResolvedValue({ items: [], next_before: null, storage: "Worker" });
  vi.spyOn(botsApi, "getTopBotComparison").mockReturnValue(new Promise(() => {}));
  vi.spyOn(botsApi, "getTopBotResearchStatus").mockReturnValue(new Promise(() => {}));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it.each([true, false])("shows the server-resolved exit policy for new entries (fixed=%s)", async fixed => {
  vi.mocked(botsApi.getTopBotStatistics).mockResolvedValue({ ...stats, exit_settings: { ...stats.exit_settings!, future_entries: fixed ? fixedFuture : trailingFuture } });
  render(<TopBotStrategyPanel accountId={123}
    bot={{ id: 39, strategy_params: { fixed_bracket_session: "2026-11-04" } } as BotConfig}
    activity={{ runs: [], profit_guard: { available: true, trading_day: "2026-11-04" } } as unknown as BotActivity}
    demoMode={false} onChanged={() => {}} />);
  expect(screen.getByText(/New entries: checking exit settings/)).toBeTruthy();  // Never a hard-coded guess.
  expect(await screen.findByText(fixed ? /New entries: Fixed stop 25 pts · Take-profit 25 pts · No trailing · Session 2026-11-04 only/ : /New entries: Initial stop 25 pts · Trail 25 pts after \+25/)).toBeTruthy();
  expect(screen.getByText(/Daily profit target \+\$500 · Daily loss stop −\$500/)).toBeTruthy();
  expect(screen.getByText(/Daily loss limit \$500/)).toBeTruthy();
  if (fixed) expect(screen.getByText(/Trailing exits resume for new entries next session/)).toBeTruthy();
  expect(screen.getByText("$23.56")).toBeTruthy();
});
it("keeps exactly one audit poller when the panel re-renders", async () => {
  // Sibling children sharing one React key make React orphan the earlier one on
  // re-render without running its effect cleanup, leaking a 15 s polling loop each time.
  const props = { accountId: 123, bot: null, activity: null, demoMode: false };
  const { rerender } = render(<TopBotStrategyPanel {...props} onChanged={() => {}} />);
  await waitFor(() => expect(screen.getByText("$23.56")).toBeTruthy());
  for (let i = 0; i < 5; i += 1) rerender(<TopBotStrategyPanel {...props} onChanged={() => {}} />);
  await waitFor(() => expect(screen.getByText(/No audit events recorded yet/)).toBeTruthy());
  expect(screen.getAllByRole("region", { name: "TopBot action audit" })).toHaveLength(1);
  expect(botsApi.getTopBotAudit).toHaveBeenCalledTimes(1);
  expect(botsApi.getTopBotResearchStatus).toHaveBeenCalledTimes(1);
  expect(botsApi.getTopBotComparison).toHaveBeenCalledTimes(1);
});
it("stops all audit polling when a re-rendered panel unmounts", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const props = { accountId: 123, bot: null, activity: null, demoMode: false };
    const { rerender, unmount } = render(<TopBotStrategyPanel {...props} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("$23.56")).toBeTruthy());
    for (let i = 0; i < 5; i += 1) rerender(<TopBotStrategyPanel {...props} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getAllByText(/No audit events recorded yet/).length).toBeGreaterThan(0));
    const before = vi.mocked(botsApi.getTopBotAudit).mock.calls.length;
    unmount();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(vi.mocked(botsApi.getTopBotAudit).mock.calls.length - before).toBe(0);
  } finally { vi.useRealTimers(); }
});
it("shows the saved entry-limit preference", async () => {
  render(<TopBotStrategyPanel accountId={123} bot={{ id: 39, enabled: true, strategy_params: { daily_entry_limit_enabled: false } } as BotConfig}
    activity={null} demoMode={false} onChanged={() => {}} />);
  expect(screen.getByText(/Daily entry-count limit disabled/)).toBeTruthy();
  await waitFor(() => expect(screen.getByText("$23.56")).toBeTruthy());
});
it("shows persistent fixed exits and keeps an open trade's saved protection separate", async () => {
  vi.mocked(botsApi.getTopBotStatistics).mockResolvedValue({ ...stats, exit_settings: {
    future_entries: { ...fixedFuture, persistent: true, take_profit_points: 40 },
    open_trades: [{ entry_id: 188, exit_mode: "fixed", take_profit_points: 25, initial_stop_points: 25, confirmed_stop: 28373, confirmed_target: 28323 }],
    as_of: "2026-11-04T15:00:00Z" } });
  render(<TopBotStrategyPanel accountId={123}
    bot={{ id: 39, strategy_params: { exit_mode: "fixed", fixed_bracket_session: "2026-11-04" } } as BotConfig}
    activity={null} demoMode={false} onChanged={() => {}} />);
  expect(await screen.findByText(/New entries: Fixed stop 25 pts · Take-profit 40 pts · No trailing/)).toBeTruthy();
  expect(screen.getByText(/Open entry 188: saved fixed take-profit 25 \/ stop 25 pts · broker-confirmed stop 28373, target 28323/)).toBeTruthy();
  expect(screen.getByText(/Fixed exits stay selected across sessions and restarts/)).toBeTruthy();
  expect(screen.queryByText(/Trailing exits resume/)).toBeNull();
  expect(screen.queryByText(/Session 2026-11-04 only/)).toBeNull();
});
it("labels blocked and failed attempts truthfully and keeps genuine unresolved submissions distinct", async () => {
  const row = (entry_id: number, attempt_status: string, kind: string, label: string, reason: string | null = null) => ({
    entry_id, side: "BUY", stats_status: "awaiting_fill", attempt_status, display_status: { kind, label, reason } });
  vi.mocked(botsApi.getTopBotStatistics).mockResolvedValue({ ...stats, items: [
    row(1, "blocked", "blocked", "Blocked before submission · no order sent", "Bot execution state changed before provider routing."),
    row(2, "error", "submission_error", "Submission failed · no fill"),
    row(3, "submitted", "awaiting_fill", "Submitted · awaiting fill confirmation")] });
  render(<TopBotStrategyPanel accountId={123} bot={null} activity={null} demoMode={false} onChanged={() => {}} />);
  expect(await screen.findByText(/Blocked before submission · no order sent/)).toBeTruthy();
  expect(screen.getByText(/Bot execution state changed before provider routing/)).toBeTruthy();
  expect(screen.getByText(/Submission failed · no fill/)).toBeTruthy();
  expect(screen.getByText(/Submitted · awaiting fill confirmation/)).toBeTruthy();
  expect(screen.queryByText("Awaiting fill")).toBeNull();
});
it("ignores a statistics response for a different account", async () => {
  vi.mocked(botsApi.getTopBotStatistics).mockResolvedValue({ ...stats, account_id: 999, net_pnl_usd: 777 });
  render(<TopBotStrategyPanel accountId={123} bot={null} activity={null} demoMode={false} onChanged={() => {}} />);
  expect(await screen.findByText(/Statistics unavailable/)).toBeTruthy();
  expect(screen.queryByText("$777.00")).toBeNull();
});
it("starts broker practice routing for the selected account, not dry run", async () => {
  const start = vi.spyOn(botsApi, "startTopBot").mockResolvedValue({} as never);
  const changed = vi.fn();
  render(<TopBotStrategyPanel accountId={123} bot={null} activity={null} demoMode={false} onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Start practice bot" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith(123, false));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(screen.getByText("$23.56")).toBeTruthy();
  expect(screen.getByText("7.0m / 7.0m")).toBeTruthy();
});
it("stops entries using the exact bot and explains protection remains", async () => {
  const stop = vi.spyOn(botsApi, "stop").mockResolvedValue({} as never);
  render(<TopBotStrategyPanel accountId={123} bot={{ id: 44, enabled: true } as BotConfig}
    activity={{ runs: [{ status: "running" }] } as BotActivity} demoMode={false} onChanged={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Stop new entries" }));
  await waitFor(() => expect(stop).toHaveBeenCalledWith(44));
  expect(screen.getByText(/Stopping entries keeps protection/)).toBeTruthy();
});
it("never routes orders or fetches practice stats in demo mode", () => {
  const start = vi.spyOn(botsApi, "startTopBot");
  render(<TopBotStrategyPanel accountId={123} bot={null} activity={null} demoMode onChanged={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Start practice bot" }));
  expect(start).not.toHaveBeenCalled();
  expect(botsApi.getTopBotStatistics).not.toHaveBeenCalled();
});
