// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { botsApi } from "../../lib/api";
import type { AccountInfo, BotConfig } from "../../lib/types";
import { BotExpressAccountRequired, BotProviderWorkspaceBoundary } from "./BotAccountGate";
import { BotPage } from "./BotPage";
import {
  filterBotConfigsByAccount,
  getBotProviderAccountId,
  getProjectXBotAccounts,
  loadBotConfigsForProviderAccount,
  resolveActiveBotAccount,
} from "./botAccountIsolation";

afterEach(() => {
  cleanup();
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
  it("mounts the production Bot page without any provider request while Live is active", async () => {
    const live = account(9001, "csv_import", true);
    const express = account(2001, "projectx");
    const fetchSpy = vi.fn(() => Promise.reject(new Error("provider request must not start")));
    vi.stubGlobal("fetch", fetchSpy);
    const providerCalls = [
      vi.spyOn(botsApi, "listConfigsWithCacheScope"),
      vi.spyOn(botsApi, "getActivity"),
      vi.spyOn(botsApi, "searchContracts"),
      vi.spyOn(botsApi, "getCandles"),
      vi.spyOn(botsApi, "createConfig"),
      vi.spyOn(botsApi, "updateConfig"),
      vi.spyOn(botsApi, "start"),
      vi.spyOn(botsApi, "evaluate"),
    ];
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <Outlet context={{ accounts: [live, express], accountsLoading: false }} />,
          children: [{ index: true, element: <BotPage /> }],
        },
      ],
      { initialEntries: ["/"] },
    );

    render(<RouterProvider router={router} />);

    expect(await screen.findByText("Select an Express account to use Bot")).not.toBeNull();
    await waitFor(() => {
      expect(fetchSpy).not.toHaveBeenCalled();
      for (const call of providerCalls) {
        expect(call).not.toHaveBeenCalled();
      }
    });
    expect(screen.queryByText("ProjectX candles, server-side audit trail")).toBeNull();
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

  it("renders a concise Live-safe message with an explicit Express selector", () => {
    const live = account(9001, "csv_import", true);
    const markup = renderToStaticMarkup(
      <BotExpressAccountRequired
        activeAccount={live}
        expressAccounts={[account(2001, "projectx")]}
        onSelectAccount={() => undefined}
      />,
    );

    expect(markup).toContain("Select an Express account to use Bot");
    expect(markup).toContain("no ProjectX charts, searches, polling, or streams start");
    expect(markup).toContain('aria-label="Select an Express account for Bot"');
    expect(markup).toContain("Express 2001");
    expect(markup).not.toContain("Live 9001 (9001)");
  });
});
