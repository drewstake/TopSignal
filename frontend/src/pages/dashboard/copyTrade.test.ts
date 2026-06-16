import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountInfo, AccountTrade } from "../../lib/types";
import {
  COPY_TRADE_ENABLE_CONFIRMATION_MESSAGE,
  COPY_TRADE_SETTINGS_STORAGE_KEY,
  buildCopyTradeAccountRows,
  combineCopyTradePnlCalendarDays,
  computeCopyTradeDriftSummary,
  computeCopyTradeTotals,
  getCopyTradeDailyNetPnlForTradingDay,
  getCopyTradeRosterAccountIds,
  getCopyTradeSelectedFollowerAccountIds,
  getCopyTradeUncopyEventsResetAt,
  getDailyNetPnlForTradingDay,
  prepareCopyTradeModeToggle,
  readStoredCopyTradeSettings,
  updateCopyTradeFollowerAccountIds,
  updateCopyTradeModeSetting,
  updateCopyTradeUncopyEventsResetAt,
  writeStoredCopyTradeSettings,
  type CopyTradeAccountRow,
} from "./copyTrade";

function row(overrides: Partial<CopyTradeAccountRow>): CopyTradeAccountRow {
  return {
    accountId: 1,
    accountName: "Account",
    role: "Follower",
    status: "Active",
    balance: 50_000,
    dailyPnl: 300,
    netPnl: 300,
    openPositions: 0,
    contributionNetPnl: overrides.role === "Leader" ? 300 : 300,
    contributionDailyPnl: overrides.role === "Leader" ? 300 : 300,
    includedInTotals: true,
    exclusionReason: null,
    loadError: null,
    ...overrides,
  };
}

function trade(overrides: Partial<AccountTrade>): AccountTrade {
  return {
    id: 1,
    account_id: 1,
    contract_id: "MNQ",
    symbol: "MNQ",
    side: "LONG",
    size: 1,
    price: 20_000,
    timestamp: "2026-05-28T14:02:00.000Z",
    entry_time: "2026-05-28T14:00:00.000Z",
    exit_time: "2026-05-28T14:02:00.000Z",
    duration_minutes: 2,
    entry_price: 20_000,
    exit_price: 20_030,
    fees: 1.4,
    pnl: 300,
    order_id: "order-1",
    source_trade_id: "trade-1",
    ...overrides,
  };
}

function account(overrides: Partial<AccountInfo> & Pick<AccountInfo, "id" | "name">): AccountInfo {
  return {
    provider_name: overrides.name,
    custom_display_name: null,
    balance: 50_000,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
    ...overrides,
  };
}

describe("computeCopyTradeTotals", () => {
  it("combines a $300 leader gain across five copied accounts into $1,500", () => {
    const totals = computeCopyTradeTotals([
      row({ accountId: 1, accountName: "Leader", role: "Leader" }),
      row({ accountId: 2, accountName: "Follower 1" }),
      row({ accountId: 3, accountName: "Follower 2" }),
      row({ accountId: 4, accountName: "Follower 3" }),
      row({ accountId: 5, accountName: "Follower 4" }),
    ]);

    expect(totals.canCalculate).toBe(true);
    expect(totals.combinedNetPnl).toBe(1_500);
    expect(totals.activeCopiedAccountCount).toBe(5);
    expect(totals.followersCopyingCount).toBe(4);
  });

  it("excludes inactive and errored followers", () => {
    const totals = computeCopyTradeTotals([
      row({ accountId: 1, accountName: "Leader", role: "Leader" }),
      row({
        accountId: 2,
        accountName: "Inactive Follower",
        status: "Inactive",
        contributionNetPnl: 0,
        contributionDailyPnl: 0,
        includedInTotals: false,
        exclusionReason: "Inactive follower",
      }),
      row({
        accountId: 3,
        accountName: "Error Follower",
        status: "Error",
        contributionNetPnl: 0,
        contributionDailyPnl: 0,
        includedInTotals: false,
        exclusionReason: "Error follower",
      }),
    ]);

    expect(totals.combinedNetPnl).toBe(300);
    expect(totals.followersCopyingCount).toBe(0);
    expect(totals.warnings).toContain("Inactive Follower is inactive and is excluded.");
    expect(totals.warnings).toContain("Error Follower is error and is excluded.");
  });

  it("requires one leader before copy totals can calculate", () => {
    const totals = computeCopyTradeTotals([
      row({
        accountId: null,
        accountName: "Follower Slot 1",
        status: "Inactive",
        includedInTotals: false,
        contributionNetPnl: 0,
        contributionDailyPnl: 0,
      }),
    ]);

    expect(totals.canCalculate).toBe(false);
    expect(totals.combinedNetPnl).toBe(0);
    expect(totals.warnings[0]).toContain("needs one leader account");
  });

  it("includes locked-out leader and follower loaded P&L", () => {
    const totals = computeCopyTradeTotals([
      row({ accountId: 1, accountName: "Lockout Leader", role: "Leader", status: "Locked Out" }),
      row({
        accountId: 2,
        accountName: "Lockout Follower",
        status: "Locked Out",
      }),
    ]);

    expect(totals.canCalculate).toBe(true);
    expect(totals.combinedNetPnl).toBe(600);
    expect(totals.followersCopyingCount).toBe(1);
    expect(totals.warnings).toEqual([]);
  });
});

describe("buildCopyTradeAccountRows", () => {
  it("builds one leader and four followers with one-to-one contributions", () => {
    const accounts: AccountInfo[] = [1, 2, 3, 4, 5].map((id) => ({
      id,
      name: `Account ${id}`,
      provider_name: `Account ${id}`,
      custom_display_name: null,
      balance: 50_000,
      status: "ACTIVE",
      account_state: "ACTIVE",
      is_main: id === 1,
      can_trade: true,
      is_visible: true,
      last_trade_at: null,
    }));

    const rows = buildCopyTradeAccountRows({
      accounts,
      leaderAccountId: 1,
      snapshotsByAccountId: {
        1: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
        2: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
        3: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
        4: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
        5: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
      },
    });

    const totals = computeCopyTradeTotals(rows);

    expect(rows.filter((candidate) => candidate.role === "Leader")).toHaveLength(1);
    expect(rows.filter((candidate) => candidate.role === "Follower" && candidate.accountId !== null)).toHaveLength(4);
    expect(totals.combinedNetPnl).toBe(1_500);
  });

  it("maps ProjectX lockout accounts to locked out and keeps copy contributions", () => {
    const accounts: AccountInfo[] = [
      {
        id: 1,
        name: "EXPRESS-V2-DLL-192577-18143397",
        provider_name: "EXPRESS-V2-DLL-192577-18143397",
        custom_display_name: null,
        balance: 50_000,
        status: "LOCKED_OUT",
        account_state: "LOCKED_OUT",
        is_main: true,
        can_trade: false,
        is_visible: true,
        last_trade_at: null,
      },
      {
        id: 2,
        name: "50KTC-V2-DLL-192577-19128574",
        provider_name: "50KTC-V2-DLL-192577-19128574",
        custom_display_name: null,
        balance: 50_000,
        status: "LOCKED_OUT",
        account_state: "LOCKED_OUT",
        is_main: false,
        can_trade: false,
        is_visible: true,
        last_trade_at: null,
      },
    ];

    const rows = buildCopyTradeAccountRows({
      accounts,
      leaderAccountId: 1,
      snapshotsByAccountId: {
        1: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
        2: { netPnl: 300, dailyPnl: 300, openPositions: 0 },
      },
    });

    expect(rows[0].status).toBe("Locked Out");
    expect(rows[0].includedInTotals).toBe(true);
    expect(rows[1].status).toBe("Locked Out");
    expect(rows[1].includedInTotals).toBe(true);
    expect(rows[1].contributionNetPnl).toBe(300);
  });

  it("uses explicit follower selections instead of the first four accounts", () => {
    const accounts: AccountInfo[] = [
      account({ id: 10, name: "EXPRESS-V2-DLL-192577-16782575", is_main: true }),
      account({ id: 20, name: "50KTC-V2-DLL-192577-11530403", account_state: "LOCKED_OUT", status: "LOCKED_OUT", can_trade: false }),
      account({ id: 21, name: "50KTC-V2-DLL-192577-15694349", account_state: "LOCKED_OUT", status: "LOCKED_OUT", can_trade: false }),
      account({ id: 30, name: "EXPRESS-V2-DLL-192577-50519642" }),
      account({ id: 31, name: "EXPRESS-V2-DLL-192577-93912778" }),
    ];

    const rows = buildCopyTradeAccountRows({
      accounts,
      leaderAccountId: 10,
      followerAccountIds: [30, 31],
      snapshotsByAccountId: {
        10: { netPnl: 200, dailyPnl: 200, openPositions: 0 },
        20: { netPnl: -1_000, dailyPnl: -1_000, openPositions: 0 },
        21: { netPnl: -1_000, dailyPnl: -1_000, openPositions: 0 },
        30: { netPnl: 200, dailyPnl: 200, openPositions: 0 },
        31: { netPnl: 200, dailyPnl: 200, openPositions: 0 },
      },
    });

    expect(rows.map((candidate) => candidate.accountId)).toEqual([10, 30, 31, null, null]);
    expect(computeCopyTradeTotals(rows).combinedNetPnl).toBe(600);
  });
});

describe("getCopyTradeRosterAccountIds", () => {
  it("defaults an EXPRESS leader to active EXPRESS followers before locked accounts from other account families", () => {
    const accounts: AccountInfo[] = [
      account({ id: 50, name: "50KTC-V2-DLL-192577-11530403", account_state: "LOCKED_OUT", status: "LOCKED_OUT", can_trade: false }),
      account({ id: 51, name: "50KTC-V2-DLL-192577-15694349", account_state: "LOCKED_OUT", status: "LOCKED_OUT", can_trade: false }),
      account({ id: 10, name: "EXPRESS-V2-DLL-192577-16782575", account_state: "LOCKED_OUT", status: "LOCKED_OUT", can_trade: false }),
      account({ id: 11, name: "EXPRESS-V2-DLL-192577-50519642" }),
      account({ id: 12, name: "EXPRESS-V2-DLL-192577-93912778" }),
      account({ id: 13, name: "EXPRESS-V2-DLL-192577-95520881" }),
      account({ id: 14, name: "EXPRESS-V2-DLL-192577-98052478" }),
    ];

    expect(getCopyTradeRosterAccountIds(accounts, 10)).toEqual([10, 11, 12, 13, 14]);
  });

  it("persists explicit follower selections per leader, including an intentionally empty roster", () => {
    const accounts: AccountInfo[] = [
      account({ id: 1, name: "150KTC-V2-DLL-192577-16577193" }),
      account({ id: 2, name: "150KTC-V2-DLL-192577-45224872" }),
      account({ id: 3, name: "150KTC-V2-DLL-192577-52861877" }),
    ];
    const settings = updateCopyTradeFollowerAccountIds({ modeEnabled: true }, 1, [3]);
    const emptySettings = updateCopyTradeFollowerAccountIds(settings, 1, []);

    expect(getCopyTradeSelectedFollowerAccountIds(settings, 1)).toEqual([3]);
    expect(getCopyTradeRosterAccountIds(accounts, 1, settings)).toEqual([1, 3]);
    expect(getCopyTradeSelectedFollowerAccountIds(emptySettings, 1)).toEqual([]);
    expect(getCopyTradeRosterAccountIds(accounts, 1, emptySettings)).toEqual([1]);
  });
});

describe("combineCopyTradePnlCalendarDays", () => {
  it("combines only included account calendar days one-to-one", () => {
    const days = combineCopyTradePnlCalendarDays(
      [
        row({ accountId: 1, role: "Leader" }),
        row({ accountId: 2 }),
        row({
          accountId: 3,
          status: "Inactive",
          includedInTotals: false,
          contributionNetPnl: 0,
          contributionDailyPnl: 0,
        }),
      ],
      {
        1: [{ date: "2026-05-28", trade_count: 1, gross_pnl: 310, fees: 10, net_pnl: 300 }],
        2: [{ date: "2026-05-28", trade_count: 1, gross_pnl: 310, fees: 10, net_pnl: 300 }],
        3: [{ date: "2026-05-28", trade_count: 1, gross_pnl: 310, fees: 10, net_pnl: 300 }],
      },
    );

    expect(days).toEqual([
      {
        date: "2026-05-28",
        trade_count: 2,
        gross_pnl: 620,
        fees: 20,
        net_pnl: 600,
      },
    ]);
  });
});

describe("getDailyNetPnlForTradingDay", () => {
  it("returns zero when the current trading session has no closed P&L yet", () => {
    expect(
      getDailyNetPnlForTradingDay(
        [
          { date: "2026-05-28", trade_count: 4, gross_pnl: 1_500, fees: 18.3, net_pnl: 1_481.7 },
          { date: "2026-05-27", trade_count: 2, gross_pnl: 200, fees: 8, net_pnl: 192 },
        ],
        "2026-05-29",
      ),
    ).toBe(0);
  });

  it("returns the matching trading session P&L when present", () => {
    expect(
      getDailyNetPnlForTradingDay(
        [
          { date: "2026-05-28", trade_count: 4, gross_pnl: 1_500, fees: 18.3, net_pnl: 1_481.7 },
          { date: "2026-05-29", trade_count: 1, gross_pnl: -50, fees: 2.8, net_pnl: -52.8 },
        ],
        "2026-05-29",
      ),
    ).toBe(-52.8);
  });
});

describe("getCopyTradeDailyNetPnlForTradingDay", () => {
  it("uses the current trading-day calendar value when present", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [{ date: "2026-05-29", trade_count: 1, gross_pnl: 750, fees: 30.2, net_pnl: 719.8 }],
      "2026-05-29",
      { active_days: 1, net_pnl: 6_415.35 },
      "2026-05-29T14:30:00.000Z",
    );

    expect(dailyPnl).toBe(719.8);
  });

  it("falls back to one-day summary net P&L when a current follower calendar row is missing", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [],
      "2026-05-29",
      { active_days: 1, net_pnl: 6_415.35 },
      "2026-05-29T14:30:00.000Z",
    );

    expect(dailyPnl).toBe(6_415.35);
  });

  it("does not treat multi-day all-time net P&L as current daily P&L", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [],
      "2026-05-29",
      { active_days: 3, net_pnl: 6_415.35 },
      "2026-05-29T14:30:00.000Z",
    );

    expect(dailyPnl).toBe(0);
  });

  it("falls back to live balance for same-day accounts with no local closed P&L yet", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [],
      "2026-05-29",
      { active_days: 0, net_pnl: 0 },
      "2026-05-29T13:45:00.000Z",
      719.8,
    );

    expect(dailyPnl).toBe(719.8);
  });

  it("does not use live balance as daily P&L for older last-trade dates", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [],
      "2026-05-29",
      { active_days: 0, net_pnl: 0 },
      "2026-05-27T13:39:00.000Z",
      4_136.25,
    );

    expect(dailyPnl).toBe(0);
  });

  it("does not use live balance when summary already has multi-day history", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [],
      "2026-05-29",
      { active_days: 3, net_pnl: 6_415.35 },
      "2026-05-29T13:45:00.000Z",
      7_135.15,
    );

    expect(dailyPnl).toBe(0);
  });

  it("does not treat nominal account balances as daily P&L", () => {
    const dailyPnl = getCopyTradeDailyNetPnlForTradingDay(
      [],
      "2026-05-29",
      { active_days: 0, net_pnl: 0 },
      "2026-05-29T13:45:00.000Z",
      50_719.8,
    );

    expect(dailyPnl).toBe(0);
  });
});

describe("computeCopyTradeDriftSummary", () => {
  it("does not flag copied follower trades that match the leader", () => {
    const summary = computeCopyTradeDriftSummary(
      [row({ accountId: 1, role: "Leader" }), row({ accountId: 2, accountName: "Follower 1" })],
      {
        1: [trade({ id: 1, account_id: 1 })],
        2: [trade({ id: 2, account_id: 2, entry_time: "2026-05-28T14:00:08.000Z", exit_time: "2026-05-28T14:02:08.000Z" })],
      },
    );

    expect(summary.likelyUncopyEventCount).toBe(0);
    expect(summary.followerOnlyTradeCount).toBe(0);
    expect(summary.followerOnlyNetPnl).toBe(0);
  });

  it("groups the same follower-only trade across multiple followers as one likely uncopy event", () => {
    const summary = computeCopyTradeDriftSummary(
      [
        row({ accountId: 1, role: "Leader" }),
        row({ accountId: 2, accountName: "Follower 1" }),
        row({ accountId: 3, accountName: "Follower 2" }),
        row({ accountId: 4, accountName: "Follower 3" }),
        row({ accountId: 5, accountName: "Follower 4" }),
      ],
      {
        1: [trade({ id: 1, account_id: 1, entry_time: "2026-05-28T14:00:00.000Z", exit_time: "2026-05-28T14:02:00.000Z" })],
        2: [trade({ id: 2, account_id: 2, entry_time: "2026-05-28T16:00:00.000Z", exit_time: "2026-05-28T16:03:00.000Z", pnl: -500 })],
        3: [trade({ id: 3, account_id: 3, entry_time: "2026-05-28T16:00:12.000Z", exit_time: "2026-05-28T16:03:10.000Z", pnl: -510 })],
        4: [trade({ id: 4, account_id: 4, entry_time: "2026-05-28T16:00:25.000Z", exit_time: "2026-05-28T16:03:18.000Z", pnl: -505 })],
        5: [trade({ id: 5, account_id: 5, entry_time: "2026-05-28T16:00:40.000Z", exit_time: "2026-05-28T16:03:28.000Z", pnl: -495 })],
      },
    );

    expect(summary.likelyUncopyEventCount).toBe(1);
    expect(summary.followerOnlyTradeCount).toBe(4);
    expect(summary.affectedAccountCount).toBe(4);
    expect(summary.followerOnlyNetPnl).toBe(-2_010);
  });

  it("flags a follower trade outside the leader match window", () => {
    const summary = computeCopyTradeDriftSummary(
      [row({ accountId: 1, role: "Leader" }), row({ accountId: 2, accountName: "Follower 1" })],
      {
        1: [trade({ id: 1, account_id: 1, entry_time: "2026-05-28T14:00:00.000Z", exit_time: "2026-05-28T14:02:00.000Z" })],
        2: [trade({ id: 2, account_id: 2, entry_time: "2026-05-28T14:05:00.000Z", exit_time: "2026-05-28T14:07:00.000Z", pnl: -250 })],
      },
    );

    expect(summary.likelyUncopyEventCount).toBe(1);
    expect(summary.followerOnlyTradeCount).toBe(1);
    expect(summary.accounts[0]).toMatchObject({
      accountId: 2,
      followerOnlyTradeCount: 1,
      netPnl: -250,
    });
  });

  it("ignores follower-only trades before the uncopy reset time", () => {
    const summary = computeCopyTradeDriftSummary(
      [row({ accountId: 1, role: "Leader" }), row({ accountId: 2, accountName: "Follower 1" })],
      {
        1: [trade({ id: 1, account_id: 1, entry_time: "2026-05-28T14:00:00.000Z", exit_time: "2026-05-28T14:02:00.000Z" })],
        2: [
          trade({ id: 2, account_id: 2, entry_time: "2026-05-28T14:05:00.000Z", exit_time: "2026-05-28T14:07:00.000Z", pnl: -250 }),
          trade({ id: 3, account_id: 2, entry_time: "2026-05-28T14:20:00.000Z", exit_time: "2026-05-28T14:22:00.000Z", pnl: -125 }),
        ],
      },
      { resetAt: "2026-05-28T14:10:00.000Z" },
    );

    expect(summary.likelyUncopyEventCount).toBe(1);
    expect(summary.followerOnlyTradeCount).toBe(1);
    expect(summary.followerOnlyNetPnl).toBe(-125);
  });
});

describe("copy-trade uncopy event reset settings", () => {
  it("stores reset timestamps by leader account", () => {
    const settings = updateCopyTradeUncopyEventsResetAt(
      {
        modeEnabled: true,
      },
      10,
      "2026-05-28T22:34:00.000Z",
    );

    expect(getCopyTradeUncopyEventsResetAt(settings, 10)).toBe("2026-05-28T22:34:00.000Z");
    expect(getCopyTradeUncopyEventsResetAt(settings, 11)).toBeNull();
  });
});

describe("copy-trade mode toggle decisions", () => {
  it("requires confirmation before enabling copy trade mode", () => {
    expect(COPY_TRADE_ENABLE_CONFIRMATION_MESSAGE).toContain("does not place orders");

    const decision = prepareCopyTradeModeToggle(
      { modeEnabled: false },
      true,
      {
        selectedAccountId: 10,
        enableConfirmed: false,
      },
    );

    expect(decision.status).toBe("cancelled");
    expect(decision.nextSettings.modeEnabled).toBe(false);
  });

  it("blocks enabling copy trade mode until a leader account is selected", () => {
    const decision = prepareCopyTradeModeToggle(
      { modeEnabled: false },
      true,
      {
        selectedAccountId: null,
        enableConfirmed: true,
      },
    );

    expect(decision.status).toBe("blocked");
    expect(decision.message).toContain("Select an account");
  });

  it("enables copy trade mode after confirmation without losing reset settings", () => {
    const settings = updateCopyTradeUncopyEventsResetAt({ modeEnabled: false }, 10, "2026-05-28T22:34:00.000Z");
    const decision = prepareCopyTradeModeToggle(settings, true, {
      selectedAccountId: 10,
      enableConfirmed: true,
    });

    expect(decision.status).toBe("ready");
    expect(decision.nextSettings.modeEnabled).toBe(true);
    expect(getCopyTradeUncopyEventsResetAt(decision.nextSettings, 10)).toBe("2026-05-28T22:34:00.000Z");
  });

  it("allows disabling copy trade mode without a selected account", () => {
    const decision = prepareCopyTradeModeToggle({ modeEnabled: true }, false, {
      selectedAccountId: null,
    });

    expect(decision.status).toBe("ready");
    expect(decision.nextSettings.modeEnabled).toBe(false);
  });

  it("treats repeated toggle requests as unchanged", () => {
    const decision = prepareCopyTradeModeToggle(updateCopyTradeModeSetting({ modeEnabled: false }, true), true, {
      selectedAccountId: 10,
      enableConfirmed: true,
    });

    expect(decision.status).toBe("unchanged");
    expect(decision.nextSettings.modeEnabled).toBe(true);
  });
});

describe("copy-trade settings storage", () => {
  let values: Map<string, string>;

  beforeEach(() => {
    values = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => (values.has(key) ? values.get(key)! : null),
        setItem: (key: string, value: string) => {
          values.set(key, value);
        },
        removeItem: (key: string) => {
          values.delete(key);
        },
        clear: () => {
          values.clear();
        },
      },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("persists normalized copy trade settings", () => {
    const settings = updateCopyTradeUncopyEventsResetAt({ modeEnabled: true }, 10, "2026-05-28T22:34:00.000Z");

    writeStoredCopyTradeSettings(settings);

    expect(JSON.parse(window.localStorage.getItem(COPY_TRADE_SETTINGS_STORAGE_KEY) ?? "{}")).toEqual({
      modeEnabled: true,
      followerAccountIdsByLeaderAccountId: {},
      uncopyEventsResetAtByLeaderAccountId: {
        "10": "2026-05-28T22:34:00.000Z",
      },
    });
    expect(readStoredCopyTradeSettings()).toEqual(settings);
  });

  it("falls back to disabled settings when localStorage cannot be read", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => {
          throw new Error("storage blocked");
        },
      },
    });

    expect(readStoredCopyTradeSettings()).toEqual({
      modeEnabled: false,
      followerAccountIdsByLeaderAccountId: {},
      uncopyEventsResetAtByLeaderAccountId: {},
    });
  });

  it("surfaces localStorage write failures so the UI can keep state consistent", () => {
    vi.stubGlobal("window", {
      localStorage: {
        setItem: () => {
          throw new Error("storage blocked");
        },
      },
    });

    expect(() => writeStoredCopyTradeSettings({ modeEnabled: true })).toThrow("storage blocked");
  });
});
