// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { botsApi } from "../../lib/api";
import type { AccountInfo, BotActivity, BotConfig, ProjectXContract } from "../../lib/types";

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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("BotPage account-scoped edit state", () => {
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
