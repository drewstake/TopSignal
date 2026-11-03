// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { BotActivity } from "../../lib/types";
import { TopBotEntryChecks, type EntryMonitor } from "./TopBotEntryChecks";

afterEach(cleanup);
const now = Date.parse("2026-11-03T15:00:00Z");
const at = new Date(now).toISOString();
const episode = "2026-11-03T14:59:15Z";
const contractId = "CON.F.US.MNQ.Z26";
const props = { now, contractId, fresh: true, ready: true, qualified: true, durationMet: true, episodeAt: episode };
function monitor(): EntryMonitor {
  return { status: { color: "green", label: "Running" }, updatedAt: at, error: null,
    activity: { config: { id: 1, account_id: 2, contract_id: contractId, enabled: true, execution_mode: "live" },
      runs: [{ id: 3, bot_config_id: 1, account_id: 2, status: "running", raw_state: { dom_episode_evaluated: null } }],
      decisions: [{ id: 4, bot_config_id: 1, account_id: 2, bot_run_id: 3, contract_id: contractId, created_at: at,
        decision_type: "signal", raw_payload: { depth_entry: { episode_at: episode }, entry_checks: {
          checked_at: at, episode_at: episode, phase: "broker", checks: [
            { id: "loss", label: "Daily loss budget and stop risk", status: "blocked", detail: "Insufficient loss budget." },
            { id: "cooldown", label: "Entry cooldown", status: "passed", detail: "Passed at the recorded broker check." },
          ],
        } } }], order_attempts: [], risk_events: [],
    } as unknown as BotActivity };
}
const row = (label: string) => within(screen.getByText(label).closest("li")!);

it("shows live passes and explicit broker blockers directly in the entry list", () => {
  render(<TopBotEntryChecks {...props} monitor={monitor()} />);
  expect(row("Imbalance and duration").getByText("Passing")).toBeTruthy();
  expect(row("Daily loss budget and stop risk").getByText("Blocking")).toBeTruthy();
  expect(row("Entry cooldown").getByText("Passing")).toBeTruthy();
  expect(row("Practice account eligible").getByText("Not checked")).toBeTruthy();
});

it("does not infer any broker passes when checks have never been recorded", () => {
  const state = monitor(); state.activity!.decisions = [];
  render(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Daily loss budget and stop risk").getByText("Not checked")).toBeTruthy();
  expect(row("Entry cooldown").getByText("Not checked")).toBeTruthy();
});

it("expires broker evidence with time, a new episode, a failed refresh, or stale depth", () => {
  const state = monitor();
  const view = render(<TopBotEntryChecks {...props} monitor={state} now={now + 31_000} />);
  expect(row("Entry cooldown").getByText("Stale")).toBeTruthy();
  view.rerender(<TopBotEntryChecks {...props} monitor={state} episodeAt="new-episode" />);
  expect(row("Entry cooldown").getByText("Stale")).toBeTruthy();
  view.rerender(<TopBotEntryChecks {...props} monitor={{ ...state, error: "offline" }} />);
  expect(row("Entry cooldown").getByText("Stale")).toBeTruthy();
  expect(row("Bot running").getByText("Not checked")).toBeTruthy();
  view.rerender(<TopBotEntryChecks {...props} monitor={state} fresh={false} />);
  expect(row("Entry cooldown").getByText("Stale")).toBeTruthy();
});

it("ignores evidence belonging to another account, contract, or run", () => {
  const state = monitor(); const decision = state.activity!.decisions[0];
  decision.account_id = 99;
  const view = render(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Entry cooldown").getByText("Not checked")).toBeTruthy();
  decision.account_id = 2; decision.bot_run_id = 99;
  view.rerender(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Entry cooldown").getByText("Not checked")).toBeTruthy();
  decision.bot_run_id = 3; decision.contract_id = "another-contract";
  view.rerender(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Entry cooldown").getByText("Not checked")).toBeTruthy();
});

it("shows consumed episodes, stopped runs, and dry runs as blockers", () => {
  const state = monitor();
  state.activity!.runs[0].raw_state = { dom_episode_evaluated: episode };
  state.activity!.config.enabled = false;
  state.activity!.config.execution_mode = "dry_run";
  render(<TopBotEntryChecks {...props} monitor={state} />);
  for (const label of ["New signal episode", "Bot running", "Broker execution mode"]) {
    expect(row(label).getByText("Blocking")).toBeTruthy();
  }
});

it("does not display future-dated checks as current passes", () => {
  const state = monitor();
  const snapshot = state.activity!.decisions[0].raw_payload!.entry_checks as Record<string, unknown>;
  snapshot.checked_at = new Date(now + 60_000).toISOString();
  render(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Entry cooldown").getByText("Stale")).toBeTruthy();
});

it("reads nested rejection evidence without inventing a broker pass", () => {
  const state = monitor(); const decision = state.activity!.decisions[0];
  decision.decision_type = "risk_reject";
  decision.raw_payload = { signal_order_plan: decision.raw_payload,
    risk_blocks: [{ code: "protective_stop_bracket_missing", message: "Protective stop is missing." }] };
  render(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Daily loss budget and stop risk").getByText("Blocking")).toBeTruthy();
  expect(row("Recorded rejection").getByText("Blocking")).toBeTruthy();
  expect(screen.getByText("Protective stop is missing.")).toBeTruthy();
});

it("shows armed profit protection and the measured account P&L", () => {
  const state = monitor();
  state.activity!.profit_guard = { available: true, armed: true, trigger: null, observed_at: at, pnl: 150, peak_pnl: 220 };
  render(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Daily profit protection").getByText("Passing")).toBeTruthy();
  expect(screen.getByText(/Break-even protection armed: stop at \$0/)).toBeTruthy();
  expect(screen.getByText(/Daily account P&L: \+\$150.00 · Highest observed: \+\$220.00/)).toBeTruthy();
});

it("keeps a triggered daily stop blocking even after price observations go stale", () => {
  const state = monitor();
  state.activity!.profit_guard = { available: true, armed: true, trigger: "daily_profit_target_reached",
    observed_at: new Date(now - 120_000).toISOString(), pnl: 400, peak_pnl: 400 };
  render(<TopBotEntryChecks {...props} monitor={state} fresh={false} />);
  expect(row("Daily profit protection").getByText("Blocking")).toBeTruthy();
  expect(screen.getByText(/\+\$400 target reached/)).toBeTruthy();
});

it("shows an unavailable profit monitor as an entry blocker", () => {
  const state = monitor();
  state.activity!.profit_guard = { available: false, message: "Daily profit protection unavailable." };
  render(<TopBotEntryChecks {...props} monitor={state} />);
  expect(row("Daily profit protection").getByText("Blocking")).toBeTruthy();
});
