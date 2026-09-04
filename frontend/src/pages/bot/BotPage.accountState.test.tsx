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
  ProjectXContract,
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
    enabled: true,
    execution_mode: "live",
    strategy_type: "sma_cross",
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

describe("BotPage account-scoped edit state", () => {
  it("requires confirmation before arming a continuous live run", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const start = vi.spyOn(botsApi, "start").mockImplementation(() => new Promise(() => undefined));

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
    await waitFor(() => expect((armButton as HTMLButtonElement).disabled).toBe(false));
    await user.click(armButton);

    expect(confirm).toHaveBeenCalledOnce();
    expect(String(confirm.mock.calls[0]?.[0])).toContain("eligible to attempt order routing");
    expect(String(confirm.mock.calls[0]?.[0])).toContain("restart automatically disarms routing");
    expect(String(confirm.mock.calls[0]?.[0])).toContain("before explicitly rearming");
    expect(String(confirm.mock.calls[0]?.[0])).not.toContain("will resume");
    expect(start).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await user.click(armButton);
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(botA.id, {
        dryRun: false,
        confirmLiveOrderRouting: true,
        continuous: true,
        stopAtSessionEnd: false,
      }),
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
    const start = vi.spyOn(botsApi, "start");

    renderBotPage();
    const armButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
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
    const unknownButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
    expect((unknownButton as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText(/ProjectX has not verified this account as simulated Practice/)).not.toBeNull();
    cleanup();

    accountA.provider_simulated = true;
    accountA.provider_classification_observed_at = new Date(Date.now() - 6 * 60 * 1_000).toISOString();
    renderBotPage();
    const staleButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
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
    const armButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
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
    const armButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
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
    const armButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
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
    const armButton = await screen.findByRole("button", { name: "Request Continuous Live Arming" });
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
    expect(await screen.findByText("No bot configuration saved.")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: `Emergency: Flatten Account ${accountA.id}` }));
    await waitFor(() => expect(flatten).toHaveBeenCalledWith(accountA.id, true));
  });

  it("persists execution mode changes without starting the bot", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    const update = vi.spyOn(botsApi, "updateConfig").mockResolvedValue({
      ...botA,
      execution_mode: "dry_run",
    });
    const start = vi.spyOn(botsApi, "start");

    renderBotPage();
    await screen.findByRole("button", { name: "Update Bot" });
    fireEvent.change(screen.getByLabelText(/^Execution/), { target: { value: "dry_run" } });
    await user.click(screen.getByRole("button", { name: "Update Bot" }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      execution_mode: "dry_run",
      strategy_params: {
        protective_stop_ticks: 8,
        take_profit_ticks: 16,
      },
    }));
    expect(start).not.toHaveBeenCalled();
  });

  it("clears the old edit ID when switching to an account with no bots", async () => {
    const user = userEvent.setup();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockImplementation(async (accountId) => ({
      configs: {
        items: accountId === accountA.id ? [botA] : [],
        total: accountId === accountA.id ? 1 : 0,
      },
      cacheScope: "user:test",
    }));
    vi.spyOn(botsApi, "getActivity").mockResolvedValue({
      config: botA,
      runs: [],
      decisions: [],
      order_attempts: [],
      risk_events: [],
    } satisfies BotActivity);
    const updateConfig = vi.spyOn(botsApi, "updateConfig");
    const createConfig = vi.spyOn(botsApi, "createConfig");

    renderBotPage();
    expect(await screen.findByRole("button", { name: "Update Bot" })).not.toBeNull();

    await user.click(screen.getByRole("button", { name: "Switch to empty account" }));

    expect(await screen.findByRole("button", { name: "Save Bot" })).not.toBeNull();
    const accountSelect = screen.getByLabelText(/^Account/) as HTMLSelectElement;
    expect(accountSelect.value).toBe(String(accountB.id));
    expect(accountSelect.disabled).toBe(true);
    expect(screen.queryByText("Bot 41")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Save Bot" }));
    expect(updateConfig).not.toHaveBeenCalled();
    expect(createConfig).not.toHaveBeenCalled();
  });

  it("rejects a partially parsed strategy integer instead of updating", async () => {
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockResolvedValue({
      configs: { items: [botA], total: 1 },
      cacheScope: "user:test",
    });
    vi.spyOn(botsApi, "getActivity").mockResolvedValue({
      config: botA,
      runs: [],
      decisions: [],
      order_attempts: [],
      risk_events: [],
    } satisfies BotActivity);
    const updateConfig = vi.spyOn(botsApi, "updateConfig");

    renderBotPage();
    await screen.findByRole("button", { name: "Update Bot" });
    const barsInput = screen.getByLabelText("Bars") as HTMLInputElement;
    fireEvent.change(barsInput, { target: { value: "100bars" } });
    fireEvent.click(screen.getByRole("button", { name: "Update Bot" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Numeric settings"));
    expect(updateConfig).not.toHaveBeenCalled();
  });

  it("does not let an old account delete completion replace the new account config list", async () => {
    const user = userEvent.setup();
    const pendingDelete = deferred<void>();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockImplementation(async (accountId) => ({
      configs: {
        items: accountId === accountA.id ? [botA] : [botB],
        total: 1,
      },
      cacheScope: "user:test",
    }));
    vi.spyOn(botsApi, "getActivity").mockImplementation(async (botId) => (
      activity(botId === botA.id ? botA : botB)
    ));
    vi.spyOn(botsApi, "deleteConfig").mockReturnValue(pendingDelete.promise);

    renderBotPage();
    await screen.findByRole("button", { name: "Delete Bot 41" });
    await user.click(screen.getByRole("button", { name: "Delete Bot 41" }));
    await waitFor(() => expect(botsApi.deleteConfig).toHaveBeenCalledWith(botA.id));

    await user.click(screen.getByRole("button", { name: "Switch to empty account" }));
    await screen.findByRole("option", { name: "Bot 42" });

    await act(async () => {
      pendingDelete.resolve();
      await pendingDelete.promise;
    });

    await waitFor(() => expect(screen.getByRole("option", { name: "Bot 42" })).not.toBeNull());
    expect(screen.queryByRole("option", { name: "Bot 41" })).toBeNull();
  });

  it("invalidates an old contract search after switching away and back to the same account", async () => {
    const user = userEvent.setup();
    const pendingSearch = deferred<ProjectXContract[]>();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockImplementation(async (accountId) => ({
      configs: {
        items: accountId === accountA.id ? [botA] : [],
        total: accountId === accountA.id ? 1 : 0,
      },
      cacheScope: "user:test",
    }));
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    vi.spyOn(botsApi, "searchContracts").mockReturnValue(pendingSearch.promise);

    renderBotPage();
    await screen.findByRole("button", { name: "Update Bot" });
    fireEvent.change(screen.getByDisplayValue("MNQ"), { target: { value: "ES" } });
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(botsApi.searchContracts).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Switch to empty account" }));
    await screen.findByRole("button", { name: "Save Bot" });
    await user.click(screen.getByRole("button", { name: "Switch to first account" }));
    await screen.findByRole("button", { name: "Update Bot" });

    await act(async () => {
      pendingSearch.resolve([{
        id: "CON.F.US.ES.U26",
        name: "Stale ES Contract",
        description: null,
        tick_size: 0.25,
        tick_value: 12.5,
        active_contract: true,
        symbol_id: "ES",
      }]);
      await pendingSearch.promise;
    });

    expect(screen.queryByDisplayValue("Stale ES Contract")).toBeNull();
    expect(screen.getByDisplayValue("MNQ")).not.toBeNull();
  });

  it("does not surface a stale save error on the next account", async () => {
    const user = userEvent.setup();
    const pendingSave = deferred<BotConfig>();
    vi.spyOn(botsApi, "listConfigsWithCacheScope").mockImplementation(async (accountId) => ({
      configs: {
        items: accountId === accountA.id ? [botA] : [],
        total: accountId === accountA.id ? 1 : 0,
      },
      cacheScope: "user:test",
    }));
    vi.spyOn(botsApi, "getActivity").mockResolvedValue(activity(botA));
    vi.spyOn(botsApi, "updateConfig").mockReturnValue(pendingSave.promise);

    renderBotPage();
    await user.click(await screen.findByRole("button", { name: "Update Bot" }));
    await waitFor(() => expect(botsApi.updateConfig).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Switch to empty account" }));
    await screen.findByRole("button", { name: "Save Bot" });

    await act(async () => {
      pendingSave.reject(new Error("Account A save failed"));
      await pendingSave.promise.catch(() => undefined);
    });

    expect(screen.queryByText("Account A save failed")).toBeNull();
    expect((screen.getByLabelText(/^Account/) as HTMLSelectElement).value).toBe(String(accountB.id));
  });
});
