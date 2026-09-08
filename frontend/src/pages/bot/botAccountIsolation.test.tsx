// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { botsApi } from "../../lib/api";
import type { AccountInfo, BotConfig } from "../../lib/types";
import { BotProjectXAccountNotice, BotProviderWorkspaceBoundary } from "./BotAccountGate";
import { BotPage } from "./BotPage";
import {
  filterBotConfigsByAccount,
  getBotProviderAccountId,
  getProjectXBotAccounts,
  loadBotConfigsForProviderAccount,
  reconcileBotConfigs,
  resolveActiveBotAccount,
} from "./botAccountIsolation";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function account(
  id: number,
  tradeDataSource: AccountInfo["trade_data_source"],
  isMain = false,
): AccountInfo {
  return {
    id,
    name: tradeDataSource === "csv_import" ? `Live ${id}` : `Express ${id}`,
    provider_name: `Account ${id}`,
    custom_display_name: null,
    trade_data_source: tradeDataSource,
    balance: null,
    provider_data_stale: tradeDataSource === "projectx",
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: isMain,
    is_archived: false,
    can_trade: null,
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
    execution_mode: "dry_run",
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
    max_daily_loss: 100,
    max_trades_per_day: 2,
    max_open_position: 1,
    allowed_contracts: ["CON.F.US.MNQ.U26"],
    trading_start_time: "09:30:00",
    trading_end_time: "16:00:00",
    cooldown_seconds: 60,
    max_data_staleness_seconds: 600,
    allow_market_depth: false,
    created_at: "2026-07-25T12:00:00Z",
    updated_at: "2026-07-25T12:00:00Z",
  };
}

describe("Bot Live/ProjectX isolation", () => {
  it("preserves unchanged configs without overlooking nested changes or removals", () => {
    const current = [bot(1, 2001), bot(2, 2001)];
    expect(reconcileBotConfigs(current, structuredClone(current))).toBe(current);
    const incoming = structuredClone(current);
    incoming[1].allowed_contracts.push("CON.F.US.MNQ.Z26");
    const next = reconcileBotConfigs(current, incoming);
    expect(next[0]).toBe(current[0]);
    expect(next[1]).toBe(incoming[1]);
    expect(reconcileBotConfigs(current, [incoming[1]])).toEqual([incoming[1]]);
  });

  it.each([
    { name: "no saved accounts", accounts: [] },
    { name: "only a Live CSV account", accounts: [account(9001, "csv_import", true)] },
    { name: "a Live CSV account active alongside a saved ProjectX account", accounts: [account(9001, "csv_import", true), account(2001, "projectx")] },
  ])("keeps bot execution disabled and CSV accounts disconnected for $name", async ({ accounts }) => {
    vi.useFakeTimers();
    const fetchSpy = vi.fn(() => Promise.reject(new Error("provider request must not start")));
    vi.stubGlobal("fetch", fetchSpy);
    const webSocketSpy = vi.fn();
    vi.stubGlobal("WebSocket", webSocketSpy);
    const search = vi.spyOn(botsApi, "searchContracts").mockRejectedValue(new Error("ProjectX connection unavailable"));
    const providerCalls = [
      vi.spyOn(botsApi, "getRuntimeStatus"),
      vi.spyOn(botsApi, "listConfigsWithCacheScope"),
      vi.spyOn(botsApi, "getActivity"),
      vi.spyOn(botsApi, "getCandles"),
      vi.spyOn(botsApi, "createConfig"),
      vi.spyOn(botsApi, "updateConfig"),
      vi.spyOn(botsApi, "start"),
      vi.spyOn(botsApi, "startTopBot"),
      vi.spyOn(botsApi, "evaluate"),
    ];
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <Outlet context={{ accounts, accountsLoading: false }} />,
          children: [{ index: true, element: <BotPage /> }],
        },
      ],
      { initialEntries: ["/"] },
    );

    render(<RouterProvider router={router} />);
    await act(async () => { await import("./BotAnalysisPanel"); });

    for (const title of ["Explore Bot without an account", "TopBot", "Signal Chart", "Order Book", "Evaluation & market analysis", "Run activity"]) {
      expect(screen.getByRole("heading", { name: title })).not.toBeNull();
    }
    expect(screen.getByText("View only")).not.toBeNull();
    expect(screen.queryByRole("button", { name: /Emergency|Verify Practice/ })).toBeNull();
    for (const name of ["Dry Run", "Live Run", "Stop Automation"]) {
      const button = screen.getByRole("button", { name }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      fireEvent.click(button);
    }
    if (!accounts.length) {
      expect(screen.getByText(/No account is selected/)).not.toBeNull();
      expect(screen.getByRole("link", { name: "Open Accounts" }).getAttribute("href")).toBe("/accounts");
      expect(screen.queryByText(/Live CSV account/)).toBeNull();
    }
    // No-account browsing may discover a market, but cannot start account/bot reads or mutations.
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(webSocketSpy).not.toHaveBeenCalled();
    expect(search).toHaveBeenCalledTimes(accounts.length ? 0 : 1);
    for (const call of providerCalls) expect(call).not.toHaveBeenCalled();
  });

  it("does not load configs or mount any provider workspace child while Live is active", async () => {
    const live = account(9001, "csv_import", true);
    const loadConfigs = vi.fn();
    const providerMount = vi.fn();

    function ProviderWorkspaceProbe() {
      providerMount();
      return <div>Provider chart, search, price, depth, and polling workspace</div>;
    }

    await expect(
      loadBotConfigsForProviderAccount(getBotProviderAccountId(live), loadConfigs),
    ).resolves.toBeNull();
    const markup = renderToStaticMarkup(
      <BotProviderWorkspaceBoundary
        activeAccount={live}
        fallback={<div>Live-safe Bot message</div>}
      >
        <ProviderWorkspaceProbe />
      </BotProviderWorkspaceBoundary>,
    );

    expect(loadConfigs).not.toHaveBeenCalled();
    expect(providerMount).not.toHaveBeenCalled();
    expect(markup).toContain("Live-safe Bot message");
    expect(markup).not.toContain("Provider chart");
  });

  it("loads and retains only configs owned by the explicitly active Express account", async () => {
    const express = account(2001, "projectx", true);
    const loader = vi.fn().mockResolvedValue({
      configs: {
        items: [bot(1, 2001), bot(2, 2002)],
        total: 2,
        warnings: ["cached warning"],
      },
      cacheScope: "user:one",
    });

    const result = await loadBotConfigsForProviderAccount(getBotProviderAccountId(express), loader);

    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledWith(2001);
    expect(result?.configs.items.map(({ id }) => id)).toEqual([1]);
    expect(result?.configs.total).toBe(1);
    expect(result?.configs.warnings).toEqual(["cached warning"]);
  });

  it("resolves the selected account, excludes Live from Bot selectors, and filters configs", () => {
    const rows = [
      account(9001, "csv_import", true),
      account(2001, "projectx"),
      account(2002, "projectx"),
    ];

    expect(resolveActiveBotAccount(rows, 2002)?.id).toBe(2002);
    expect(resolveActiveBotAccount(rows, null)?.id).toBe(9001);
    expect(getProjectXBotAccounts(rows).map(({ id }) => id)).toEqual([2001, 2002]);
    expect(filterBotConfigsByAccount([bot(1, 2001), bot(2, 2002)], 2002).map(({ id }) => id)).toEqual([2]);
  });

  it("offers an explicit ProjectX selector that also accepts Practice accounts", () => {
    const live = account(9001, "csv_import", true);
    const practice = { ...account(2001, "projectx"), name: "Practice 2001" };
    const onSelectAccount = vi.fn();
    render(
      <BotProjectXAccountNotice
        activeAccount={live}
        projectXAccounts={[practice]}
        onSelectAccount={onSelectAccount}
      />,
    );

    expect(screen.queryByText(/Express account/)).toBeNull();
    expect(screen.getByRole("option", { name: "Practice 2001 (2001)" })).not.toBeNull();
    expect(screen.queryByRole("option", { name: "Live 9001 (9001)" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Select a ProjectX account for Bot" }), { target: { value: "2001" } });
    expect(onSelectAccount).toHaveBeenCalledExactlyOnceWith(2001);
  });
});
