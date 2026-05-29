import { describe, expect, it } from "vitest";

import type { AccountInfo, AccountTrade } from "../../lib/types";
import {
  buildCopyTradeAccountRows,
  combineCopyTradePnlCalendarDays,
  computeCopyTradeDriftSummary,
  computeCopyTradeTotals,
  getCopyTradeDailyNetPnlForTradingDay,
  getCopyTradeUncopyEventsResetAt,
  getDailyNetPnlForTradingDay,
  updateCopyTradeUncopyEventsResetAt,
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
    expect(totals.warnings).toContain(
      "Leader account Lockout Leader is locked out; loaded P&L is included but live copy trading may be blocked.",
    );
    expect(totals.warnings).toContain(
      "Lockout Follower is locked out; loaded P&L is included but live copy trading may be blocked.",
    );
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
