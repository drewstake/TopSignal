// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { TopBotResearchStatus, type ResearchStatus } from "./TopBotResearchStatus";

vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

const status = (account_id: number, overrides: Partial<ResearchStatus> = {}): ResearchStatus => ({
  account_id, as_of: "2026-11-11T16:00:00Z",
  position_recording: { enrolled: true, healthy: true, error: null, heartbeat_at: "2026-11-11T15:59:58Z", age_seconds: 2, stale: false,
    scope: "Open-position path from entry to exit (1-second samples); not post-exit research" },
  post_exit_recording: { problems: ["capture_restarted_inside_window"], scope: "Market data retained after each exit",
    capture: { state: "recording", durable_age_seconds: 0.4, durable_progress: "advancing", dropped_events: 27965,
      last_drop: { received_at: "2026-11-11T14:35:25Z" }, uncommitted_events: 12 },
    open_windows: [{ entry_id: 183, exit_at: "2026-11-11T15:26:30Z", exit_reason: "take_profit", cutoff: "2026-11-11T17:25:45Z",
      seconds_remaining: 5100, position_closed: true, retention: { state: "retained" }, capture_matches_entry: false }] },
  analysis: { reader: { state: "replaying", last_cycle_at: "2026-11-11T15:59:50Z", checkpoint_reader: { available: true, reason: null } },
    active: { entry_id: 166, phase: "index", events: 1_200_000, received_at: "2026-11-11T09:00:00Z" },
    readiness: { experiment_id: "dom-rr-x", registered_filled_entries: 20, pending: 16, processed: 4, deferred: 3,
      counts: { observing: 3, queued: 9, processing: 1, retrying: 2, failed: 1, completed_valid: 3, completed_excluded: 1 },
      processed_coverage: { numerator: 3, denominator: 4, rate: .75, label: "Valid paired comparisons ÷ processed entries" },
      overall_usable: { numerator: 3, denominator: 20, rate: .15, label: "Valid paired comparisons ÷ all registered filled entries (pending included)" },
      oldest_overdue: { entry_id: 142, cutoff: "2026-11-10T20:53:17Z", overdue_seconds: 69000, state: "failed" },
      unfilled_submissions: [{ entry_id: 190, kind: "awaiting_fill", label: "Submitted · awaiting fill confirmation" }],
      not_trades: { blocked: 26, submission_error: 1 }, prior_experiment_entries: 0 },
    entries: [{ entry_id: 142, state: "failed", job: { attempts: 7, consecutive_failures: 7, next_attempt_at: "2026-11-11T21:00:00Z",
      last_failure: { category: "no_progress", phase: "replay" } } }] },
  backup: { state: "failed_stale", status: "failed", verified: false, stale: true, last_verified_at: "2026-11-04T20:52:13Z",
    last_attempt_at: "2026-11-10T22:15:01Z", age_seconds: 586000, last_failure: { type: "TimeoutExpired" } },
  ...overrides,
});

it("separates the four sections with denominators, overdue work and actionable failures", async () => {
  vi.spyOn(botsApi, "getTopBotResearchStatus").mockResolvedValue(status(7));
  render(<TopBotResearchStatus accountId={7} />);
  const position = await screen.findByRole("article", { name: "Position recording" });
  expect(position.textContent).toMatch(/Recording/);
  expect(position.textContent).toMatch(/not post-exit research/);
  const post = screen.getByRole("article", { name: "Post-exit recording" });
  expect(post.textContent).toMatch(/1 observation window open/);
  expect(post.textContent).toMatch(/27,965 events lost this capture/);
  expect(post.textContent).toMatch(/recorder restarted since entry \(gap\)/);
  expect(screen.getByRole("alert").textContent).toMatch(/excluded, not filled in/);
  const analysis = screen.getByRole("article", { name: "Analysis progress" });
  expect(analysis.textContent).toMatch(/20 registered filled entries: 3 valid · 1 excluded · 16 pending/);
  expect(analysis.textContent).toMatch(/3\/4 \(75%\)/);
  expect(analysis.textContent).toMatch(/3\/20 \(15%\)/);
  expect(analysis.textContent).toMatch(/Oldest overdue: entry 142, 19.2 h past its window \(failed repeatedly\)/);
  expect(analysis.textContent).toMatch(/Deferred on this account: 3/);
  expect(analysis.textContent).toMatch(/Entry 142 · Failed repeatedly · 7 attempts · Replay stopped making progress and was ended \(during replay\)/);
  expect(analysis.textContent).toMatch(/1 submission awaiting fill confirmation/);
  expect(analysis.textContent).toMatch(/26 blocked · 1 submission error attempts never filled and are not trades/);
  const backup = screen.getByRole("article", { name: "Backup health" });
  expect(backup.textContent).toMatch(/Failed · stale, not verified/);
  expect(backup.textContent).toMatch(/6.8 days ago/);
  expect(backup.textContent).not.toMatch(/Verified$/);
});

it("never shows another account's status, including a late response after switching", async () => {
  let resolveOld!: (value: ResearchStatus) => void;
  vi.spyOn(botsApi, "getTopBotResearchStatus").mockImplementation(id => id === 1
    ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(status(2)));
  const view = render(<TopBotResearchStatus key={1} accountId={1} />);
  view.rerender(<TopBotResearchStatus key={2} accountId={2} />);
  await screen.findByRole("article", { name: "Analysis progress" });
  await act(async () => { resolveOld(status(1, { analysis: { ...status(1).analysis, readiness: { ...status(1).analysis.readiness, registered_filled_entries: 999 } } })); });
  expect(screen.queryByText(/999 registered/)).toBeNull();
  cleanup();
  vi.mocked(botsApi.getTopBotResearchStatus).mockResolvedValue(status(5));
  render(<TopBotResearchStatus accountId={6} />);
  await screen.findByText("Status unavailable.");
  expect(screen.queryByRole("article")).toBeNull();
});

it("marks data stale after a failed refresh and aborts polling on unmount", async () => {
  vi.useFakeTimers();
  const signals: AbortSignal[] = [];
  const read = vi.spyOn(botsApi, "getTopBotResearchStatus").mockImplementation(async (_id, options) => {
    signals.push(options!.signal!);
    if (signals.length > 1) throw new Error("offline");
    return status(3);
  });
  const view = render(<TopBotResearchStatus accountId={3} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10); });
  expect(screen.getByRole("article", { name: "Backup health" })).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(read).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/Status could not be refreshed\. Showing data from/)).toBeTruthy();
  view.unmount();
  expect(signals.at(-1)!.aborted || read.mock.calls.length === 2).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(read).toHaveBeenCalledTimes(2);
});

it("labels a verified backup only when the server says it is verified and recent", async () => {
  vi.spyOn(botsApi, "getTopBotResearchStatus").mockResolvedValue(status(4, { backup: { state: "verified", status: "verified", verified: true,
    stale: false, last_verified_at: "2026-11-11T05:00:00Z", age_seconds: 39600, last_attempt_at: "2026-11-11T04:00:00Z", last_failure: null } }));
  render(<TopBotResearchStatus accountId={4} />);
  await waitFor(() => expect(screen.getByRole("article", { name: "Backup health" }).textContent).toMatch(/^Off-device backupVerified/));
});
