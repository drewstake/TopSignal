// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { accountsApi, botsApi } from "../../lib/api";
import type {
  AccountEmergencyFlattenResult,
  AccountInfo,
  BotActivity,
  BotConfig,
  BotRuntimeStatus,
  BotEvaluation,
} from "../../lib/types";

vi.mock("./BotSignalChart", () => ({ BotSignalChart: () => <div>Chart stub</div> }));
vi.mock("./OrderBookPanel", () => ({ OrderBookPanel: () => <div>Order book stub</div> }));
vi.mock("./BotAnalysisPanel", () => ({ default: () => <div>Analysis stub</div>, BotAnalysisPanel: () => <div>Analysis stub</div> }));
vi.mock("./BotBacktestPanel", () => ({ default: () => <div>Backtest stub</div>, BotBacktestPanel: () => <div>Backtest stub</div> }));

import { BotPage } from "./BotPage";

function account(id: number, isMain: boolean): AccountInfo {
  return {
    id,
    name: `Express ${id}`,
    provider_name: `Express ${id}`,
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 50_000,
    provider_data_stale: false,
    provider_simulated: true,
    provider_classification_observed_at: new Date().toISOString(),
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: isMain,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
  };
}

function bot(id: number, accountId: number): BotConfig {
  return {
    id,
    name: `Bot ${id}`,
    account_id: accountId,
    provider: "projectx",
    enabled: false,
    execution_mode: "live",
    strategy_type: "topbot_adaptive",
    strategy_params: {},
    contract_id: "CON.F.US.MNQ.U26",
    symbol: "MNQ",
    timeframe_unit: "minute",
    timeframe_unit_number: 5,
    lookback_bars: 100,
    fast_period: 9,
    slow_period: 20,
    order_size: 1,
    max_contracts: 1,
    max_daily_loss: 250,
    max_trades_per_day: 3,
    max_open_position: 1,
    allowed_contracts: ["CON.F.US.MNQ.U26"],
    trading_start_time: "09:30:00",
    trading_end_time: "16:00:00",
    cooldown_seconds: 60,
    max_data_staleness_seconds: 600,
    allow_market_depth: false,
    created_at: "2026-08-01T12:00:00Z",
    updated_at: "2026-08-01T12:00:00Z",
  };
}

const accountA = account(2001, true);
const accountB = account(2002, false);
const botA = bot(41, accountA.id);
const botB = bot(42, accountB.id);

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, reject, resolve };
}

function activity(config: BotConfig): BotActivity {
  return {
    config,
    runs: [],
    decisions: [],
    order_attempts: [],
    risk_events: [],
  };
}

function BotHarness() {
  const [, setSearchParams] = useSearchParams();
  return (
    <>
      <button type="button" onClick={() => setSearchParams({ account: String(accountB.id) })}>
        Switch to empty account
      </button>
      <button type="button" onClick={() => setSearchParams({ account: String(accountA.id) })}>
        Switch to first account
      </button>
      <Outlet context={{ accounts: [accountA, accountB], accountsLoading: false }} />
    </>
  );
}

function renderBotPage() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <BotHarness />,
        children: [{ path: "bot", element: <BotPage /> }],
      },
    ],
    { initialEntries: [`/bot?account=${accountA.id}`] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

function healthyRuntimeStatus(): BotRuntimeStatus {
  return {
    ready: true,
    state: "running",
    provider_status: "ok",
    checks: {
      worker_enabled: true,
      worker_task_healthy: true,
      lease_healthy: true,
      runs_armed: true,
      live_gate: true,
      account_classification_fresh: true,
      accounts_simulated: true,
      provider_healthy: true,
      submissions_reconciled: true,
      account_emergency_clear: true,
    },
    counts: {
      enabled_configs: 1,
      running_runs: 0,
      unresolved_live_submissions: 0,
      unresolved_account_emergency_actions: 0,
    },
  };
}

beforeEach(() => {
  accountA.provider_simulated = true;
  accountA.provider_classification_observed_at = new Date().toISOString();
  accountB.provider_simulated = true;
  accountB.provider_classification_observed_at = new Date().toISOString();
  vi.spyOn(botsApi, "getRuntimeStatus").mockResolvedValue(healthyRuntimeStatus());
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BotPage account-scoped run controls", () => {
  it("requires confirmation before arming a continuous live run", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const start = vi.spyOn(botsApi, "startTopBot").mockImplementation(() => new Promise(() => undefined));

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Live Run" });
    await waitFor(() => expect((armButton as HTMLButtonElement).disabled).toBe(false));
    await user.click(armButton);

    expect(confirm).toHaveBeenCalledOnce();
    expect(String(confirm.mock.calls[0]?.[0])).toContain("MNQ orders");
    expect(String(confirm.mock.calls[0]?.[0])).toContain("restart disarms routing");
    expect(String(confirm.mock.calls[0]?.[0])).toContain("start a new Live Run");
    expect(String(confirm.mock.calls[0]?.[0])).not.toContain("will resume");
    expect(start).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await user.click(armButton);
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(accountA.id, false),
    );
  });

  it("blocks continuous arming when the worker lease is not healthy and shows the exact reason", async () => {
    const status = healthyRuntimeStatus();
    status.checks.lease_healthy = false;
    vi.mocked(botsApi.getRuntimeStatus).mockResolvedValue(status);
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const start = vi.spyOn(botsApi, "startTopBot");

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Live Run" });
    expect((armButton as HTMLButtonElement).disabled).toBe(true);
    expect((await screen.findAllByText("No healthy worker lease is currently confirmed.")).length).toBeGreaterThan(0);
    fireEvent.click(armButton);
    expect(start).not.toHaveBeenCalled();
  });

  it("blocks live arming for unknown or stale account classification with an explicit reason", async () => {
    accountA.provider_simulated = null;
    accountA.provider_classification_observed_at = null;
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));

    renderBotPage();
    const unknownButton = await screen.findByRole("button", { name: "Live Run" });
    expect((unknownButton as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText(/ProjectX has not verified this account as simulated Practice/)).not.toBeNull();
    cleanup();

    accountA.provider_simulated = true;
    accountA.provider_classification_observed_at = new Date(Date.now() - 6 * 60 * 1_000).toISOString();
    renderBotPage();
    const staleButton = await screen.findByRole("button", { name: "Live Run" });
    expect((staleButton as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText(/classification is stale/)).not.toBeNull();
  });

  it("provides a bounded Practice-account verification path before live arming", async () => {
    const user = userEvent.setup();
    accountA.provider_simulated = null;
    accountA.provider_classification_observed_at = null;
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const verify = vi.spyOn(accountsApi, "refreshAutomationClassification").mockResolvedValue({
      account_id: accountA.id,
      provider_simulated: true,
      provider_classification_observed_at: new Date().toISOString(),
      source: "projectx_user_hub",
    });

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Live Run" });
    expect((armButton as HTMLButtonElement).disabled).toBe(true);
    await user.click(await screen.findByRole("button", { name: "Verify Practice account" }));

    await waitFor(() => expect(verify).toHaveBeenCalledWith(accountA.id));
    expect(await screen.findByText(`Account ${accountA.id} classification verified`)).not.toBeNull();
    await waitFor(() => expect((armButton as HTMLButtonElement).disabled).toBe(false));
  });

  it("blocks continuous arming while provider health or submissions are unresolved", async () => {
    const status = healthyRuntimeStatus();
    status.provider_status = "throttled";
    status.checks.provider_healthy = false;
    status.checks.submissions_reconciled = false;
    status.counts.unresolved_live_submissions = 2;
    vi.mocked(botsApi.getRuntimeStatus).mockResolvedValue(status);
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Live Run" });
    expect((armButton as HTMLButtonElement).disabled).toBe(true);
    expect((await screen.findAllByText("ProjectX provider health is throttled.")).length).toBeGreaterThan(0);
  });

  it("reports the exact unresolved-submission count before continuous arming", async () => {
    const status = healthyRuntimeStatus();
    status.checks.submissions_reconciled = false;
    status.counts.unresolved_live_submissions = 2;
    vi.mocked(botsApi.getRuntimeStatus).mockResolvedValue(status);
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Live Run" });
    expect((armButton as HTMLButtonElement).disabled).toBe(true);
    expect((await screen.findAllByText("2 live submission(s) still require reconciliation.")).length).toBeGreaterThan(0);
  });

  it("blocks arming while an account emergency-flatten outcome is unresolved", async () => {
    const status = healthyRuntimeStatus();
    status.checks.account_emergency_clear = false;
    status.counts.unresolved_account_emergency_actions = 1;
    vi.mocked(botsApi.getRuntimeStatus).mockResolvedValue(status);
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Live Run" });
    expect((armButton as HTMLButtonElement).disabled).toBe(true);
    expect(
      (await screen.findAllByText(/1 account emergency-flatten outcome\(s\) remain unresolved/)).length,
    ).toBeGreaterThan(0);
  });

  it("keeps ordinary stop separate from broker-wide emergency flatten", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const stop = vi.spyOn(botsApi, "stop").mockResolvedValue({
      id: 9,
      bot_config_id: botA.id,
      account_id: botA.account_id,
      status: "stopped",
      dry_run: false,
      started_at: "2026-09-03T12:00:00Z",
      stopped_at: "2026-09-03T12:01:00Z",
      stop_reason: "manual_stop",
      last_heartbeat_at: null,
    });
    const emergencyFlatten = vi.spyOn(botsApi, "emergencyFlattenAccount");

    renderBotPage();
    await screen.findByRole("button", { name: "Stop Automation" });
    expect(screen.getByText(/does not cancel broker orders or close positions/i)).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Stop Automation" }));

    await waitFor(() => expect(stop).toHaveBeenCalledWith(botA.id));
    expect(emergencyFlatten).not.toHaveBeenCalled();
  });

  it("requires the account-specific phrase and shows a durable confirmed-flat result", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("FLATTEN 9999");
    const emergencyFlatten = vi.spyOn(botsApi, "emergencyFlattenAccount").mockResolvedValue({
      account_id: botA.account_id,
      audit_id: 712,
      confirmed_flat: true,
      status: "confirmed_account_flat",
      risk_block: null,
      audit: { scope: "entire_account" },
      disabled_bot_config_ids: [botA.id],
      stopped_bot_run_ids: [10],
    });

    renderBotPage();
    const emergencyButton = await screen.findByRole("button", { name: `Emergency: Flatten Account ${botA.account_id}` });
    await user.click(emergencyButton);
    expect(emergencyFlatten).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain(`FLATTEN ${botA.account_id}`);

    prompt.mockReturnValue(`FLATTEN ${botA.account_id}`);
    await user.click(emergencyButton);
    await waitFor(() => expect(emergencyFlatten).toHaveBeenCalledWith(botA.account_id, true));
    expect(prompt.mock.calls.at(-1)?.[0]).toContain("every open position");
    expect(await screen.findByText(`Account ${botA.account_id} confirmed flat`)).not.toBeNull();
    expect(screen.getByText(/Audit #712/)).not.toBeNull();
    expect(screen.getByText(/Recorded/)).not.toBeNull();
  });

  it("keeps a structured 409/unconfirmed account outcome visible", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    vi.spyOn(window, "prompt").mockReturnValue(`FLATTEN ${botA.account_id}`);
    vi.spyOn(botsApi, "emergencyFlattenAccount").mockResolvedValue({
      account_id: botA.account_id,
      audit_id: 713,
      confirmed_flat: false,
      status: "unconfirmed",
      risk_block: {
        code: "broker_account_flatten_unconfirmed",
        message: "The provider still reports an open account position.",
        severity: "critical",
      },
      audit: { scope: "entire_account" },
      disabled_bot_config_ids: [botA.id],
      stopped_bot_run_ids: [11],
    });

    renderBotPage();
    await user.click(await screen.findByRole("button", { name: `Emergency: Flatten Account ${botA.account_id}` }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "The provider still reports an open account position.",
      ),
    );
    expect(screen.getByRole("alert").textContent).toContain(`Account ${botA.account_id} flatten unconfirmed`);
    expect(screen.getByRole("alert").textContent).toContain("Audit #713");
  });

  it("retains the original account outcome when the operator switches accounts mid-request", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockImplementation(async (accountId) => ({
      configs: { items: accountId === accountA.id ? [botA] : [botB], total: 1 },
      cacheScope: "user:test",
    }));
    vi.spyOn(botsApi, "getActivity").mockImplementation(async (botId) => activity(botId === botA.id ? botA : botB));
    vi.spyOn(window, "prompt").mockReturnValue(`FLATTEN ${accountA.id}`);
    const request = deferred<AccountEmergencyFlattenResult>();
    vi.spyOn(botsApi, "emergencyFlattenAccount").mockReturnValue(request.promise);

    renderBotPage();
    await user.click(await screen.findByRole("button", { name: `Emergency: Flatten Account ${accountA.id}` }));
    await waitFor(() => expect(botsApi.emergencyFlattenAccount).toHaveBeenCalledWith(accountA.id, true));
    await user.click(screen.getByRole("button", { name: "Switch to empty account" }));
    expect(await screen.findByRole("button", { name: `Flatten pending for account ${accountA.id}` })).not.toBeNull();

    await act(async () => {
      request.resolve({
        account_id: accountA.id,
        audit_id: 714,
        confirmed_flat: true,
        status: "confirmed_account_flat",
        risk_block: null,
        audit: { scope: "entire_account" },
        disabled_bot_config_ids: [botA.id],
        stopped_bot_run_ids: [12],
      });
      await request.promise;
    });

    expect(await screen.findByText(`Account ${accountA.id} confirmed flat`)).not.toBeNull();
    expect(screen.getByRole("button", { name: `Emergency: Flatten Account ${accountB.id}` })).not.toBeNull();
    expect(screen.getByText(/Audit #714/)).not.toBeNull();
  });

  it("shows an explicit account-scoped unknown outcome after a transport ambiguity", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    vi.spyOn(window, "prompt").mockReturnValue(`FLATTEN ${accountA.id}`);
    vi.spyOn(botsApi, "emergencyFlattenAccount").mockRejectedValue(new Error("connection lost after send"));

    renderBotPage();
    await user.click(await screen.findByRole("button", { name: `Emergency: Flatten Account ${accountA.id}` }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(`Account ${accountA.id} flatten outcome unknown`);
    expect(alert.textContent).toContain("may or may not be flat");
    expect(alert.textContent).toContain("do not retry blindly");
    expect(alert.textContent).toContain("Recorded");
  });

  it("keeps account emergency flatten discoverable and usable when no live bot exists", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [], total: 0 },
      cacheScope: "user:test",
    });
    vi.spyOn(window, "prompt").mockReturnValue(`FLATTEN ${accountA.id}`);
    const flatten = vi.spyOn(botsApi, "emergencyFlattenAccount").mockResolvedValue({
      account_id: accountA.id,
      audit_id: 715,
      confirmed_flat: true,
      status: "confirmed_account_flat",
      risk_block: null,
      audit: { scope: "entire_account" },
      disabled_bot_config_ids: [],
      stopped_bot_run_ids: [],
    });

    renderBotPage();
    expect(await screen.findByText("Ready for your first run.")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: `Emergency: Flatten Account ${accountA.id}` }));
    await waitFor(() => expect(flatten).toHaveBeenCalledWith(accountA.id, true));
  });

  it("starts a dry run without any saved configuration or setup form", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [], total: 0 }, cacheScope: "user:test",
    });
    const start = vi.spyOn(botsApi, "startTopBot").mockImplementation(() => new Promise(() => undefined));
    const save = vi.spyOn(botsApi, "createConfig");
    renderBotPage();
    const dryRun = await screen.findByRole("button", { name: "Dry Run" });
    await waitFor(() => expect((dryRun as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByText("Configuration")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    await user.click(dryRun);
    expect(start).toHaveBeenCalledWith(accountA.id, true);
    expect(save).not.toHaveBeenCalled();
  });

  it("keeps Dry Run available when live routing is blocked", async () => {
    accountA.provider_simulated = false;
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [], total: 0 }, cacheScope: "user:test",
    });
    renderBotPage();
    const dryRun = await screen.findByRole("button", { name: "Dry Run" });
    await waitFor(() => expect((dryRun as HTMLButtonElement).disabled).toBe(false));
    expect((screen.getByRole("button", { name: "Live Run" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("prevents mode changes while automation is already running", async () => {
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [{ ...botA, enabled: true }], total: 1 }, cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    renderBotPage();
    await screen.findByRole("button", { name: "Dry Run" });
    expect((screen.getByRole("button", { name: "Dry Run" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Live Run" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Stop Automation" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not apply a late start result after switching accounts", async () => {
    const user = userEvent.setup();
    const pending = deferred<BotEvaluation>();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [], total: 0 }, cacheScope: "user:test",
    });
    const start = vi.spyOn(botsApi, "startTopBot").mockReturnValue(pending.promise);
    renderBotPage();
    await user.click(await screen.findByRole("button", { name: "Dry Run" }));
    expect(start).toHaveBeenCalledWith(accountA.id, true);
    await user.click(screen.getByRole("button", { name: "Switch to empty account" }));
    await screen.findByText(`MNQ · TopBot Adaptive · ${accountB.name} (${accountB.id})`);
    await act(async () => pending.resolve({ config: { ...botA, enabled: true } } as BotEvaluation));
    expect(screen.getByText("Ready for your first run.")).not.toBeNull();
    expect(screen.queryByText("Live Run active")).toBeNull();
    expect((screen.getByRole("button", { name: "Stop Automation" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("refreshes a persisted run after a failed start and keeps the error visible", async () => {
    const user = userEvent.setup();
    const list = vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [], total: 0 }, cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    vi.spyOn(botsApi, "startTopBot").mockImplementation(async () => {
      list.mockResolvedValue({ configs: { items: [{ ...botA, enabled: true }], total: 1 }, cacheScope: "user:test" });
      throw new Error("Provider unavailable after start");
    });
    renderBotPage();
    await user.click(await screen.findByRole("button", { name: "Dry Run" }));
    expect(await screen.findByText("Provider unavailable after start")).not.toBeNull();
    expect((screen.getByRole("button", { name: "Stop Automation" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
