// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { accountsApi } from "../../lib/api";
import { getTradingDayRange } from "../../lib/tradingDay";
import { ACCOUNT_TRADES_SYNCED_EVENT } from "../../lib/tradeSyncEvents";
import type {
  AccountInfo,
  AccountPnlCalendarDay,
  AccountSummary,
  AccountSummaryWithPointBases,
  AccountTrade,
} from "../../lib/types";
import { DashboardPage } from "./DashboardPage";
import type { CompactDashboardViewProps } from "./components/CompactDashboardView";
import { writeStoredCopyTradeSettings } from "./copyTrade";

const compactViewRender = vi.hoisted(() => vi.fn());

vi.mock("./components/CompactDashboardView", () => ({
  CompactDashboardSkeleton: () => <div data-testid="compact-skeleton">Compact loading</div>,
  CompactDashboardView: (props: CompactDashboardViewProps) => {
    compactViewRender(props);
    return (
      <section data-testid="compact-view" data-account-name={props.accountName}>
        <button type="button" onClick={() => props.onDaySelect(TEST_DAY)}>
          Select test day
        </button>
      </section>
    );
  },
}));

const LEADER_ID = 1_011;
const FOLLOWER_ID = 2_022;
const SECOND_ACCOUNT_ID = 3_033;
const TEST_DAY = "2026-07-24";
const NEXT_DAY = "2026-07-25";

const emptySizingBenchmark: AccountSummary["sizingBenchmark"] = {
  benchmarkMode: "fixed_average_size",
  benchmarkSizeUsed: 0,
  benchmarkGrossPnl: 0,
  benchmarkNetPnl: 0,
  benchmarkDiff: 0,
  benchmarkRatio: null,
  benchmarkLabel: "In Line With Benchmark",
};

const emptyPointPayoffByBasis: AccountSummaryWithPointBases["point_payoff_by_basis"] = {
  MNQ: { avgPointGain: null, avgPointLoss: null },
  MES: { avgPointGain: null, avgPointLoss: null },
  NQ: { avgPointGain: null, avgPointLoss: null },
  ES: { avgPointGain: null, avgPointLoss: null },
  MGC: { avgPointGain: null, avgPointLoss: null },
  SIL: { avgPointGain: null, avgPointLoss: null },
};

function makeAccount(id: number, name: string, options: { isMain?: boolean; balance?: number } = {}): AccountInfo {
  return {
    id,
    name,
    provider_name: name,
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: options.balance ?? 50_000,
    provider_data_stale: false,
    last_seen_at: "2026-07-26T12:00:00Z",
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: options.isMain ?? false,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: "2026-07-25T15:30:00Z",
  };
}

function makeSummary(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return {
    realized_pnl: 0,
    gross_pnl: 0,
    fees: 0,
    net_pnl: 0,
    win_rate: 0,
    win_count: 0,
    loss_count: 0,
    breakeven_count: 0,
    profit_factor: 0,
    avg_win: 0,
    avg_loss: 0,
    avg_win_duration_minutes: 0,
    avg_loss_duration_minutes: 0,
    expectancy_per_trade: 0,
    tail_risk_5pct: 0,
    max_drawdown: 0,
    average_drawdown: 0,
    risk_drawdown_score: 0,
    max_drawdown_length_hours: 0,
    recovery_time_hours: 0,
    average_recovery_length_hours: 0,
    trade_count: 0,
    half_turn_count: 0,
    execution_count: 0,
    day_win_rate: 0,
    green_days: 0,
    red_days: 0,
    flat_days: 0,
    avg_trades_per_day: 0,
    active_days: 0,
    efficiency_per_hour: 0,
    profit_per_day: 0,
    averagePositionSize: 0,
    medianPositionSize: 0,
    tradeCountUsedForSizingStats: 0,
    avgPointGain: null,
    avgPointLoss: null,
    pointsBasisUsed: "auto",
    sizingBenchmark: emptySizingBenchmark,
    ...overrides,
  };
}

function makeDay(date: string, netPnl: number, tradeCount = 1): AccountPnlCalendarDay {
  return {
    date,
    trade_count: tradeCount,
    win_count: netPnl > 0 ? tradeCount : 0,
    loss_count: netPnl < 0 ? tradeCount : 0,
    breakeven_count: netPnl === 0 ? tradeCount : 0,
    gross_pnl: netPnl,
    non_commission_fees: 0,
    commissions: 0,
    fees: 0,
    net_pnl: netPnl,
  };
}

function makeTrade(
  id: number,
  accountId: number,
  timestamp: string,
  pnl: number,
  side: "LONG" | "SHORT" = pnl >= 0 ? "LONG" : "SHORT",
): AccountTrade {
  return {
    id,
    account_id: accountId,
    contract_id: `CON-${id}`,
    symbol: "MNQ",
    side,
    size: 1,
    price: 20_000 + id,
    timestamp,
    entry_time: timestamp,
    exit_time: timestamp,
    duration_minutes: 2,
    entry_price: 20_000 + id - 1,
    exit_price: 20_000 + id,
    fees: 0,
    non_commission_fees: 0,
    commissions: 0,
    pnl,
    order_id: `ORDER-${id}`,
    source_trade_id: `SOURCE-${id}`,
  };
}

function summaryBundle(summary: AccountSummary): AccountSummaryWithPointBases {
  return {
    summary,
    point_payoff_by_basis: emptyPointPayoffByBasis,
  };
}

type Fixture<T> = T | Error | Promise<T>;

interface ApiFixtures {
  accounts: AccountInfo[];
  summaries?: Readonly<Record<number, Fixture<AccountSummary>>>;
  calendars?: Readonly<Record<number, Fixture<AccountPnlCalendarDay[]>>>;
  trades?: Readonly<Record<number, Fixture<AccountTrade[]>>>;
}

function resolveFixture<T>(fixture: Fixture<T> | undefined, fallback: T): Promise<T> {
  if (fixture instanceof Error) {
    return Promise.reject(fixture);
  }
  return Promise.resolve(fixture ?? fallback);
}

function installAccountsApi({ accounts, summaries = {}, calendars = {}, trades = {} }: ApiFixtures) {
  const getSelectableAccountsLocalFirst = vi
    .spyOn(accountsApi, "getSelectableAccountsLocalFirst")
    .mockResolvedValue(accounts);
  const getSelectableAccounts = vi.spyOn(accountsApi, "getSelectableAccounts").mockResolvedValue(accounts);
  const getSummaryWithPointBases = vi
    .spyOn(accountsApi, "getSummaryWithPointBases")
    .mockImplementation(async (accountId) => summaryBundle(await resolveFixture(summaries[accountId], makeSummary())));
  const getPnlCalendar = vi
    .spyOn(accountsApi, "getPnlCalendar")
    .mockImplementation(async (accountId) => resolveFixture(calendars[accountId], []));
  const getTrades = vi
    .spyOn(accountsApi, "getTrades")
    .mockImplementation(async (accountId) => resolveFixture(trades[accountId], []));
  const getJournalDays = vi.spyOn(accountsApi, "getJournalDays").mockResolvedValue({ days: [] });
  const refreshTrades = vi.spyOn(accountsApi, "refreshTrades").mockResolvedValue({
    fetched_count: 0,
    inserted_count: 0,
  });

  return {
    getSelectableAccountsLocalFirst,
    getSelectableAccounts,
    getSummaryWithPointBases,
    getPnlCalendar,
    getTrades,
    getJournalDays,
    refreshTrades,
  };
}

function mountDashboard(options: {
  compact: boolean;
  accounts: AccountInfo[];
  accountId?: number;
  accountsError?: string | null;
  reloadAccounts?: () => void;
}) {
  const accountId = options.accountId ?? options.accounts[0]?.id;
  const setCompactEnabled = vi.fn();
  const reloadAccounts = options.reloadAccounts ?? vi.fn();
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: (
          <Outlet
            context={{
              accounts: options.accounts,
              accountsLoading: false,
              accountsError: options.accountsError ?? null,
              reloadAccounts,
              selectedAccountId: accountId ?? null,
              compactMode: { enabled: options.compact, setEnabled: setCompactEnabled },
            }}
          />
        ),
        children: [{ path: "dashboard", element: <DashboardPage /> }],
      },
    ],
    { initialEntries: [`/dashboard${accountId ? `?account=${accountId}` : ""}`] },
  );

  render(<RouterProvider router={router} />);
  return { router, setCompactEnabled, reloadAccounts };
}

function latestCompactProps(): CompactDashboardViewProps {
  const call = compactViewRender.mock.calls.at(-1);
  if (!call) {
    throw new Error("CompactDashboardView has not rendered");
  }
  return call[0] as CompactDashboardViewProps;
}

function requireValue<T>(value: T | undefined, label: string): T {
  if (value === undefined) {
    throw new Error(`Expected ${label}`);
  }
  return value;
}

async function waitForCompactSettled() {
  await screen.findByTestId("compact-view");
  await waitFor(() => {
    const props = latestCompactProps();
    expect(props.summaryLoading).toBe(false);
    expect(props.daysLoading).toBe(false);
    expect(props.tradesLoading).toBe(false);
  });
  return latestCompactProps();
}

function enableCopyMode(leaderId = LEADER_ID, followerIds: number[] = [FOLLOWER_ID]) {
  writeStoredCopyTradeSettings({
    modeEnabled: true,
    followerAccountIdsByLeaderAccountId: { [String(leaderId)]: followerIds },
  });
}

function countAccountCalls(calls: readonly (readonly unknown[])[]) {
  return calls.reduce<Record<number, number>>((counts, [accountId]) => {
    const id = Number(accountId);
    counts[id] = (counts[id] ?? 0) + 1;
    return counts;
  }, {});
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  compactViewRender.mockClear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("DashboardPage Compact Mode request and state integration", () => {
  it("offers a working retry when the AppShell account request fails", async () => {
    const reloadAccounts = vi.fn();
    mountDashboard({
      compact: true,
      accounts: [],
      accountsError: "Failed to fetch",
      reloadAccounts,
    });

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Compact Mode could not load an account: Failed to fetch",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry accounts" }));

    expect(reloadAccounts).toHaveBeenCalledTimes(1);
  });

  it("keeps Standard Mode isolated and preserves its original controls and request shape", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true });
    const api = installAccountsApi({
      accounts: [leader],
      summaries: { [LEADER_ID]: makeSummary({ net_pnl: 75, trade_count: 1, win_count: 1, win_rate: 100 }) },
      calendars: { [LEADER_ID]: [makeDay(TEST_DAY, 75)] },
      trades: { [LEADER_ID]: [makeTrade(1, LEADER_ID, "2026-07-24T15:00:00Z", 75)] },
    });

    mountDashboard({ compact: false, accounts: [leader] });

    expect(await screen.findByLabelText("Custom start date")).toBeTruthy();
    expect(screen.getByLabelText("Custom end date")).toBeTruthy();
    ["1D", "1W", "1M", "6M", "All"].forEach((label) => {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "1D" }).className).not.toContain("!h-11");
    expect(screen.getByRole("switch", { name: "Live Account CSV mode is off" })).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Copy Trade Mode is off" })).toBeTruthy();
    expect(
      await screen.findByRole("button", { name: "Copy Full Stats" }, { timeout: 10_000 }),
    ).toBeTruthy();
    expect(screen.getByText("Import trades")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Upload trade file" }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByText("Copy Trade accounts")).toBeNull();
    expect(screen.queryByTestId("compact-view")).toBeNull();
    expect(compactViewRender).not.toHaveBeenCalled();

    await waitFor(() => {
      expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(1);
      expect(api.getPnlCalendar).toHaveBeenCalledTimes(1);
      expect(api.getTrades).toHaveBeenCalledTimes(2);
    });
    expect(api.getTrades.mock.calls.map(([, query]) => query?.limit).sort((left, right) => (left ?? 0) - (right ?? 0))).toEqual([
      200,
      1_000,
    ]);
    expect(api.getTrades.mock.calls.some(([, query]) => query?.limit === 7)).toBe(false);
  });

  it("uses one request per Compact region and one shared bounded scope for summary, trades, and calendar", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true });
    const api = installAccountsApi({
      accounts: [leader],
      summaries: { [LEADER_ID]: makeSummary({ net_pnl: 100, trade_count: 1, win_count: 1 }) },
      calendars: { [LEADER_ID]: [makeDay(TEST_DAY, 100)] },
      trades: { [LEADER_ID]: [makeTrade(1, LEADER_ID, "2026-07-24T15:00:00Z", 100)] },
    });
    mountDashboard({ compact: true, accounts: [leader] });
    await waitForCompactSettled();

    expect(screen.getByRole("button", { name: "1D" }).className).toContain("!h-11");
    expect(api.getSelectableAccountsLocalFirst).not.toHaveBeenCalled();
    expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(1);
    expect(api.getPnlCalendar).toHaveBeenCalledTimes(1);
    expect(api.getTrades).toHaveBeenCalledTimes(1);
    expect(api.getSummaryWithPointBases).toHaveBeenLastCalledWith(LEADER_ID, {
      start: undefined,
      end: undefined,
      refresh: false,
    });
    expect(api.getPnlCalendar).toHaveBeenLastCalledWith(LEADER_ID, {
      start: undefined,
      end: undefined,
      all_time: true,
      refresh: false,
    });
    expect(api.getTrades).toHaveBeenLastCalledWith(LEADER_ID, {
      limit: 7,
      start: undefined,
      end: undefined,
      refresh: false,
      includeLifecycle: false,
    });

    api.getSummaryWithPointBases.mockClear();
    api.getPnlCalendar.mockClear();
    api.getTrades.mockClear();
    fireEvent.change(screen.getByLabelText("Custom start date"), { target: { value: "2026-07-20" } });
    fireEvent.change(screen.getByLabelText("Custom end date"), { target: { value: "2026-07-24" } });

    await waitFor(() => {
      expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(1);
      expect(api.getPnlCalendar).toHaveBeenCalledTimes(1);
      expect(api.getTrades).toHaveBeenCalledTimes(1);
      expect(latestCompactProps().summaryLoading).toBe(false);
      expect(latestCompactProps().daysLoading).toBe(false);
      expect(latestCompactProps().tradesLoading).toBe(false);
    });

    const summaryQuery = requireValue(api.getSummaryWithPointBases.mock.calls[0]?.[1], "summary query");
    const tradeQuery = requireValue(api.getTrades.mock.calls[0]?.[1], "trade query");
    const calendarQuery = requireValue(api.getPnlCalendar.mock.calls[0]?.[1], "calendar query");
    expect(summaryQuery.start).toBe("2026-07-19T22:00:00.000Z");
    expect(summaryQuery.end).toBe("2026-07-24T20:59:59.999999Z");
    expect({ start: tradeQuery.start, end: tradeQuery.end }).toEqual({
      start: summaryQuery.start,
      end: summaryQuery.end,
    });
    expect({ start: calendarQuery.start, end: calendarQuery.end }).toEqual({
      start: summaryQuery.start,
      end: summaryQuery.end,
    });
    expect(summaryQuery.refresh).toBe(false);
    expect(tradeQuery).toMatchObject({ limit: 7, refresh: false, includeLifecycle: false });
    expect(calendarQuery).toMatchObject({ all_time: false, refresh: false });
  });

  it("narrows summary and trades to a selected day without clearing or refetching calendar context", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true });
    const calendar = [makeDay(TEST_DAY, 100), makeDay(NEXT_DAY, -25)];
    const api = installAccountsApi({
      accounts: [leader],
      summaries: { [LEADER_ID]: makeSummary({ net_pnl: 75, trade_count: 2, win_count: 1, loss_count: 1 }) },
      calendars: { [LEADER_ID]: calendar },
      trades: {
        [LEADER_ID]: [
          makeTrade(2, LEADER_ID, "2026-07-25T15:00:00Z", -25),
          makeTrade(1, LEADER_ID, "2026-07-24T15:00:00Z", 100),
        ],
      },
    });
    const user = userEvent.setup();

    mountDashboard({ compact: true, accounts: [leader] });
    const initialProps = await waitForCompactSettled();
    expect(initialProps.calendarDays).toEqual(calendar);

    api.getSummaryWithPointBases.mockClear();
    api.getPnlCalendar.mockClear();
    api.getTrades.mockClear();
    await user.click(screen.getByRole("button", { name: "Select test day" }));

    await waitFor(() => {
      expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(1);
      expect(api.getTrades).toHaveBeenCalledTimes(1);
      expect(latestCompactProps().selectedDate).toBe(TEST_DAY);
      expect(latestCompactProps().summaryLoading).toBe(false);
      expect(latestCompactProps().tradesLoading).toBe(false);
    });
    const selectedRange = getTradingDayRange(TEST_DAY);
    expect(selectedRange).not.toBeNull();
    expect(api.getSummaryWithPointBases).toHaveBeenLastCalledWith(LEADER_ID, {
      start: selectedRange?.start,
      end: selectedRange?.end,
      refresh: true,
    });
    expect(api.getTrades).toHaveBeenLastCalledWith(LEADER_ID, {
      limit: 7,
      start: selectedRange?.start,
      end: selectedRange?.end,
      refresh: true,
      includeLifecycle: false,
    });
    expect(api.getPnlCalendar).not.toHaveBeenCalled();
    expect(latestCompactProps().days).toEqual([calendar[0]]);
    expect(latestCompactProps().calendarDays).toEqual(calendar);
  });

  it("forces all three unbounded Compact reads after an explicit ALL reload", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true });
    const api = installAccountsApi({
      accounts: [leader],
      summaries: { [LEADER_ID]: makeSummary({ net_pnl: 100, trade_count: 1, win_count: 1 }) },
      calendars: { [LEADER_ID]: [makeDay(TEST_DAY, 100)] },
      trades: { [LEADER_ID]: [makeTrade(1, LEADER_ID, "2026-07-24T15:00:00Z", 100)] },
    });

    mountDashboard({ compact: true, accounts: [leader] });
    await waitForCompactSettled();
    api.getSummaryWithPointBases.mockClear();
    api.getPnlCalendar.mockClear();
    api.getTrades.mockClear();

    act(() => {
      window.dispatchEvent(
        new CustomEvent(ACCOUNT_TRADES_SYNCED_EVENT, {
          detail: { accountId: LEADER_ID, fetchedCount: 1, insertedCount: 1 },
        }),
      );
    });

    await waitFor(() => {
      expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(1);
      expect(api.getPnlCalendar).toHaveBeenCalledTimes(1);
      expect(api.getTrades).toHaveBeenCalledTimes(1);
      expect(latestCompactProps().summaryLoading).toBe(false);
      expect(latestCompactProps().daysLoading).toBe(false);
      expect(latestCompactProps().tradesLoading).toBe(false);
    });
    expect(api.getSummaryWithPointBases).toHaveBeenLastCalledWith(LEADER_ID, {
      start: undefined,
      end: undefined,
      refresh: true,
    });
    expect(api.getPnlCalendar).toHaveBeenLastCalledWith(LEADER_ID, {
      start: undefined,
      end: undefined,
      all_time: true,
      refresh: true,
    });
    expect(api.getTrades).toHaveBeenLastCalledWith(LEADER_ID, {
      limit: 7,
      start: undefined,
      end: undefined,
      refresh: true,
      includeLifecycle: false,
    });
  });

  it("keeps a primary summary failure local to KPIs while calendar and trades remain available", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true });
    const calendar = [makeDay(TEST_DAY, 40)];
    const trades = [makeTrade(1, LEADER_ID, "2026-07-24T15:00:00Z", 40)];
    installAccountsApi({
      accounts: [leader],
      summaries: { [LEADER_ID]: new Error("Primary summary unavailable") },
      calendars: { [LEADER_ID]: calendar },
      trades: { [LEADER_ID]: trades },
    });

    mountDashboard({ compact: true, accounts: [leader] });
    const props = await waitForCompactSettled();

    expect(props.summaryError).toBe("Primary summary unavailable");
    expect(props.summary.net_pnl).toBe(0);
    expect(props.daysError).toBeNull();
    expect(props.tradesError).toBeNull();
    expect(props.days).toEqual(calendar);
    expect(props.calendarDays).toEqual(calendar);
    expect(props.trades).toEqual(trades);
    expect(props.dataWarnings).toEqual([]);
  });

  it("excludes a failed follower only from its failed region and names that omission", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true });
    const follower = makeAccount(FOLLOWER_ID, "Follower");
    enableCopyMode();
    const leaderSummary = makeSummary({ net_pnl: 90, trade_count: 2, win_count: 1, loss_count: 1 });
    const leaderDays = [makeDay(TEST_DAY, 90, 2)];
    const followerDays = [makeDay(TEST_DAY, -30, 1)];
    const leaderTrade = makeTrade(1, LEADER_ID, "2026-07-24T15:00:00Z", 90);
    const followerTrade = makeTrade(2, FOLLOWER_ID, "2026-07-24T16:00:00Z", -30);
    const api = installAccountsApi({
      accounts: [leader, follower],
      summaries: {
        [LEADER_ID]: leaderSummary,
        [FOLLOWER_ID]: new Error("Follower summary unavailable"),
      },
      calendars: { [LEADER_ID]: leaderDays, [FOLLOWER_ID]: followerDays },
      trades: { [LEADER_ID]: [leaderTrade], [FOLLOWER_ID]: [followerTrade] },
    });

    mountDashboard({ compact: true, accounts: [leader, follower] });
    const props = await waitForCompactSettled();

    await waitFor(() => {
      expect(api.refreshTrades).toHaveBeenCalledTimes(2);
      expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(4);
      expect(api.getPnlCalendar).toHaveBeenCalledTimes(4);
      expect(api.getTrades).toHaveBeenCalledTimes(4);
    });
    expect(props.summaryError).toBeNull();
    expect(props.summary.net_pnl).toBe(leaderSummary.net_pnl);
    expect(props.daysError).toBeNull();
    expect(props.calendarDays).toEqual([expect.objectContaining({ date: TEST_DAY, net_pnl: 60, trade_count: 3 })]);
    expect(props.tradesError).toBeNull();
    expect(props.trades.map((trade) => trade.account_id)).toEqual([FOLLOWER_ID, LEADER_ID]);
    expect(props.dataWarnings).toEqual([
      "Follower could not contribute to KPIs; other Compact regions remain available.",
    ]);
    expect(countAccountCalls(api.getSummaryWithPointBases.mock.calls)).toEqual({
      [LEADER_ID]: 2,
      [FOLLOWER_ID]: 2,
    });
  });

  it("does not let a stale account A response replace account B after navigation", async () => {
    const accountA = makeAccount(LEADER_ID, "Account A", { isMain: true });
    const accountB = makeAccount(SECOND_ACCOUNT_ID, "Account B");
    const summaryA = deferred<AccountSummary>();
    const calendarA = deferred<AccountPnlCalendarDay[]>();
    const tradesA = deferred<AccountTrade[]>();
    const summaryB = makeSummary({ net_pnl: 222, trade_count: 1, win_count: 1 });
    const calendarB = [makeDay(NEXT_DAY, 222)];
    const tradesB = [makeTrade(22, SECOND_ACCOUNT_ID, "2026-07-25T15:00:00Z", 222)];
    const api = installAccountsApi({
      accounts: [accountA, accountB],
      summaries: { [LEADER_ID]: summaryA.promise, [SECOND_ACCOUNT_ID]: summaryB },
      calendars: { [LEADER_ID]: calendarA.promise, [SECOND_ACCOUNT_ID]: calendarB },
      trades: { [LEADER_ID]: tradesA.promise, [SECOND_ACCOUNT_ID]: tradesB },
    });

    const { router } = mountDashboard({ compact: true, accounts: [accountA, accountB], accountId: LEADER_ID });
    await waitFor(() => {
      expect(api.getSummaryWithPointBases).toHaveBeenCalledWith(LEADER_ID, expect.any(Object));
      expect(api.getPnlCalendar).toHaveBeenCalledWith(LEADER_ID, expect.any(Object));
      expect(api.getTrades).toHaveBeenCalledWith(LEADER_ID, expect.any(Object));
    });

    await act(async () => {
      await router.navigate(`/dashboard?account=${SECOND_ACCOUNT_ID}`);
    });
    await waitFor(() => {
      const props = latestCompactProps();
      expect(props.accountName).toBe("Account B");
      expect(props.summaryLoading).toBe(false);
      expect(props.daysLoading).toBe(false);
      expect(props.tradesLoading).toBe(false);
      expect(props.summary.net_pnl).toBe(222);
    });

    await act(async () => {
      summaryA.resolve(makeSummary({ net_pnl: -999, trade_count: 1, loss_count: 1 }));
      calendarA.resolve([makeDay(TEST_DAY, -999)]);
      tradesA.resolve([makeTrade(99, LEADER_ID, "2026-07-24T15:00:00Z", -999)]);
      await Promise.all([summaryA.promise, calendarA.promise, tradesA.promise]);
    });

    const props = latestCompactProps();
    expect(props.accountName).toBe("Account B");
    expect(props.summary.net_pnl).toBe(222);
    expect(props.calendarDays).toEqual(calendarB);
    expect(props.trades).toEqual(tradesB);
    expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(2);
    expect(api.getPnlCalendar).toHaveBeenCalledTimes(2);
    expect(api.getTrades).toHaveBeenCalledTimes(2);
  });

  it("nets opposite follower data consistently across KPIs, performance, calendar, and global Recent Trades", async () => {
    const leader = makeAccount(LEADER_ID, "Leader", { isMain: true, balance: 50_000 });
    const follower = makeAccount(FOLLOWER_ID, "Follower", { balance: 25_000 });
    enableCopyMode();
    const leaderSummary = makeSummary({
      realized_pnl: 90,
      gross_pnl: 90,
      net_pnl: 90,
      trade_count: 4,
      half_turn_count: 4,
      execution_count: 8,
      win_count: 2,
      loss_count: 1,
      breakeven_count: 1,
      win_rate: 50,
      avg_win: 50,
      avg_loss: -10,
      active_days: 2,
      green_days: 1,
      red_days: 1,
      tradeCountUsedForSizingStats: 4,
      averagePositionSize: 1,
      medianPositionSize: 1,
    });
    const followerSummary = makeSummary({
      realized_pnl: -20,
      gross_pnl: -20,
      net_pnl: -20,
      trade_count: 4,
      half_turn_count: 4,
      execution_count: 8,
      win_count: 2,
      loss_count: 2,
      win_rate: 50,
      avg_win: 15,
      avg_loss: -25,
      active_days: 2,
      green_days: 1,
      red_days: 1,
      tradeCountUsedForSizingStats: 4,
      averagePositionSize: 1,
      medianPositionSize: 1,
    });
    const leaderDays = [makeDay(TEST_DAY, 100, 2), makeDay(NEXT_DAY, -10, 2)];
    const followerDays = [makeDay(TEST_DAY, -50, 2), makeDay(NEXT_DAY, 30, 2)];
    const leaderTrades = [
      makeTrade(1_101, LEADER_ID, "2026-07-24T14:00:00Z", 60),
      makeTrade(1_102, LEADER_ID, "2026-07-24T15:00:00Z", 40),
      makeTrade(1_103, LEADER_ID, "2026-07-25T14:00:00Z", -10),
      makeTrade(1_104, LEADER_ID, "2026-07-25T15:00:00Z", 0),
    ];
    const followerTrades = [
      makeTrade(2_201, FOLLOWER_ID, "2026-07-24T14:30:00Z", -30),
      makeTrade(2_202, FOLLOWER_ID, "2026-07-24T15:30:00Z", -20),
      makeTrade(2_203, FOLLOWER_ID, "2026-07-25T14:30:00Z", 20),
      makeTrade(2_204, FOLLOWER_ID, "2026-07-25T15:30:00Z", 10),
    ];
    const api = installAccountsApi({
      accounts: [leader, follower],
      summaries: { [LEADER_ID]: leaderSummary, [FOLLOWER_ID]: followerSummary },
      calendars: { [LEADER_ID]: leaderDays, [FOLLOWER_ID]: followerDays },
      trades: { [LEADER_ID]: leaderTrades, [FOLLOWER_ID]: followerTrades },
    });

    mountDashboard({ compact: true, accounts: [leader, follower] });
    await waitForCompactSettled();
    await waitFor(() => {
      expect(api.getSummaryWithPointBases).toHaveBeenCalledTimes(4);
      expect(api.getPnlCalendar).toHaveBeenCalledTimes(4);
      expect(api.getTrades).toHaveBeenCalledTimes(4);
    });
    const props = latestCompactProps();

    expect(props.accountName).toBe("Leader copy group (2 accounts)");
    expect(props.summary).toMatchObject({
      net_pnl: 70,
      realized_pnl: 70,
      trade_count: 8,
      win_count: 4,
      loss_count: 3,
      breakeven_count: 1,
      win_rate: 50,
    });
    expect(props.calendarDays).toEqual([
      expect.objectContaining({ date: TEST_DAY, net_pnl: 50, trade_count: 4 }),
      expect.objectContaining({ date: NEXT_DAY, net_pnl: 20, trade_count: 4 }),
    ]);
    expect(props.days).toEqual(props.calendarDays);
    expect(props.performanceContext).toMatchObject({
      tradingDayCount: 2,
      maxDrawdown: 0,
      riskBase: 75_000,
      riskBaseLabel: "Combined balances (2 accounts)",
    });
    expect(props.scoreBreakdown?.sampleSize).toBe(2);
    expect(props.trades.map((trade) => trade.id)).toEqual([2_204, 1_104, 2_203, 1_103, 2_202, 1_102, 2_201]);
    expect(props.accountNameById).toEqual({
      [LEADER_ID]: "Leader",
      [FOLLOWER_ID]: "Follower",
    });
    expect(props.dataWarnings).toEqual([]);
    expect(countAccountCalls(api.getSummaryWithPointBases.mock.calls)).toEqual({
      [LEADER_ID]: 2,
      [FOLLOWER_ID]: 2,
    });
    expect(countAccountCalls(api.getPnlCalendar.mock.calls)).toEqual({
      [LEADER_ID]: 2,
      [FOLLOWER_ID]: 2,
    });
    expect(countAccountCalls(api.getTrades.mock.calls)).toEqual({
      [LEADER_ID]: 2,
      [FOLLOWER_ID]: 2,
    });
  });
});
