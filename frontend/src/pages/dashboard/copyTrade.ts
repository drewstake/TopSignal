import { tradingDayKey } from "../../lib/tradingDay";
import type { AccountInfo, AccountPnlCalendarDay, AccountSummary, AccountTrade } from "../../lib/types";

export const COPY_TRADE_SETTINGS_STORAGE_KEY = "topsignal.dashboard.copyTradeSettings";

export type CopyTradeRole = "Leader" | "Follower";
export type CopyTradeStatus = "Active" | "Inactive" | "Locked Out" | "Error" | "Syncing";

export interface CopyTradeSettings {
  modeEnabled: boolean;
  uncopyEventsResetAtByLeaderAccountId?: Record<string, string>;
}

export interface CopyTradeMetricSnapshot {
  netPnl: number;
  dailyPnl: number;
  openPositions: number;
  loadError?: string | null;
}

export interface CopyTradeAccountRow {
  accountId: number | null;
  accountName: string;
  role: CopyTradeRole;
  status: CopyTradeStatus;
  balance: number;
  dailyPnl: number;
  netPnl: number;
  openPositions: number;
  contributionNetPnl: number;
  contributionDailyPnl: number;
  includedInTotals: boolean;
  exclusionReason: string | null;
  loadError: string | null;
}

export interface CopyTradeTotals {
  hasLeader: boolean;
  canCalculate: boolean;
  combinedNetPnl: number;
  combinedDailyPnl: number;
  combinedBalance: number;
  leaderNetPnl: number;
  leaderDailyPnl: number;
  followerContributionNetPnl: number;
  followerContributionDailyPnl: number;
  activeCopiedAccountCount: number;
  followersCopyingCount: number;
  warnings: string[];
}

export interface CopyTradeDriftAccountBreakdown {
  accountId: number;
  accountName: string;
  followerOnlyTradeCount: number;
  netPnl: number;
}

export interface CopyTradeDriftSummary {
  likelyUncopyEventCount: number;
  followerOnlyTradeCount: number;
  followerOnlyNetPnl: number;
  affectedAccountCount: number;
  matchWindowMinutes: number;
  accounts: CopyTradeDriftAccountBreakdown[];
}

interface BuildCopyTradeAccountRowsInput {
  accounts: readonly AccountInfo[];
  leaderAccountId: number | null;
  snapshotsByAccountId: Record<number, CopyTradeMetricSnapshot | undefined>;
}

const MAX_COPY_TRADE_ACCOUNTS = 5;
const MAX_COPY_TRADE_FOLLOWERS = 4;
const COPY_TRADE_MATCH_WINDOW_MS = 2 * 60 * 1000;
const MAX_LIVE_BALANCE_DAILY_PNL_FALLBACK_ABS = 10_000;

const defaultSettings: CopyTradeSettings = {
  modeEnabled: false,
  uncopyEventsResetAtByLeaderAccountId: {},
};

export function readStoredCopyTradeSettings(): CopyTradeSettings {
  if (typeof window === "undefined") {
    return defaultSettings;
  }

  const rawValue = window.localStorage.getItem(COPY_TRADE_SETTINGS_STORAGE_KEY);
  if (!rawValue) {
    return defaultSettings;
  }

  try {
    return normalizeCopyTradeSettings(JSON.parse(rawValue));
  } catch {
    return defaultSettings;
  }
}

export function writeStoredCopyTradeSettings(settings: CopyTradeSettings): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(COPY_TRADE_SETTINGS_STORAGE_KEY, JSON.stringify(normalizeCopyTradeSettings(settings)));
}

export function updateCopyTradeModeSetting(settings: CopyTradeSettings, modeEnabled: boolean): CopyTradeSettings {
  return {
    ...normalizeCopyTradeSettings(settings),
    modeEnabled,
  };
}

export function updateCopyTradeUncopyEventsResetAt(
  settings: CopyTradeSettings,
  leaderAccountId: number | null,
  resetAt: string | null,
): CopyTradeSettings {
  const normalized = normalizeCopyTradeSettings(settings);
  if (leaderAccountId === null) {
    return normalized;
  }

  const resetAtByLeaderAccountId = { ...(normalized.uncopyEventsResetAtByLeaderAccountId ?? {}) };
  if (resetAt && Number.isFinite(Date.parse(resetAt))) {
    resetAtByLeaderAccountId[String(leaderAccountId)] = resetAt;
  } else {
    delete resetAtByLeaderAccountId[String(leaderAccountId)];
  }

  return {
    ...normalized,
    uncopyEventsResetAtByLeaderAccountId: resetAtByLeaderAccountId,
  };
}

export function getCopyTradeUncopyEventsResetAt(settings: CopyTradeSettings, leaderAccountId: number | null): string | null {
  if (leaderAccountId === null) {
    return null;
  }

  const resetAt = normalizeCopyTradeSettings(settings).uncopyEventsResetAtByLeaderAccountId?.[String(leaderAccountId)] ?? null;
  return resetAt && Number.isFinite(Date.parse(resetAt)) ? resetAt : null;
}

export function getCopyTradeRosterAccountIds(accounts: readonly AccountInfo[], leaderAccountId: number | null): number[] {
  if (leaderAccountId === null) {
    return [];
  }

  const leader = accounts.find((account) => account.id === leaderAccountId);
  if (!leader) {
    return [];
  }

  return [
    leader.id,
    ...accounts
      .filter((account) => account.id !== leader.id)
      .slice(0, MAX_COPY_TRADE_FOLLOWERS)
      .map((account) => account.id),
  ].slice(0, MAX_COPY_TRADE_ACCOUNTS);
}

export function buildCopyTradeAccountRows(input: BuildCopyTradeAccountRowsInput): CopyTradeAccountRow[] {
  const { accounts, leaderAccountId, snapshotsByAccountId } = input;
  const leader = leaderAccountId === null ? null : accounts.find((account) => account.id === leaderAccountId) ?? null;
  const rows: CopyTradeAccountRow[] = [];

  if (leader) {
    rows.push(buildAccountRow(leader, "Leader", snapshotsByAccountId[leader.id]));
  }

  const followers = leader
    ? accounts.filter((account) => account.id !== leader.id).slice(0, MAX_COPY_TRADE_FOLLOWERS)
    : [];

  followers.forEach((account) => {
    rows.push(buildAccountRow(account, "Follower", snapshotsByAccountId[account.id]));
  });

  while (rows.length < MAX_COPY_TRADE_ACCOUNTS) {
    const followerSlot = Math.max(rows.length, 1);
    rows.push(buildEmptyFollowerSlot(followerSlot));
  }

  return rows.map(applyContribution);
}

export function computeCopyTradeTotals(rows: readonly CopyTradeAccountRow[]): CopyTradeTotals {
  const leader = rows.find((row) => row.role === "Leader" && row.accountId !== null) ?? null;
  const hasLeader = leader !== null;
  const canCalculate = hasLeader && leader.status !== "Error" && leader.status !== "Syncing";
  const warnings: string[] = [];

  if (!hasLeader) {
    warnings.push("Copy Trade Mode needs one leader account before combined stats can be calculated.");
  } else if (leader.status === "Error" || leader.status === "Syncing") {
    warnings.push(`Leader account ${leader.accountName} is ${leader.status.toLowerCase()}; copy totals are unavailable.`);
  } else if (leader.status === "Inactive" || leader.status === "Locked Out") {
    warnings.push(`Leader account ${leader.accountName} is ${leader.status.toLowerCase()}; loaded P&L is included but live copy trading may be blocked.`);
  }

  rows.forEach((row) => {
    if (row.role !== "Follower" || row.accountId === null) {
      return;
    }

    if (row.loadError) {
      warnings.push(`${row.accountName} could not load and is excluded: ${row.loadError}`);
      return;
    }

    if (row.status === "Locked Out") {
      warnings.push(`${row.accountName} is locked out; loaded P&L is included but live copy trading may be blocked.`);
      return;
    }

    if (row.status !== "Active") {
      warnings.push(`${row.accountName} is ${row.status.toLowerCase()} and is excluded.`);
    }
  });

  if (!canCalculate) {
    return {
      hasLeader,
      canCalculate,
      combinedNetPnl: 0,
      combinedDailyPnl: 0,
      combinedBalance: 0,
      leaderNetPnl: 0,
      leaderDailyPnl: 0,
      followerContributionNetPnl: 0,
      followerContributionDailyPnl: 0,
      activeCopiedAccountCount: 0,
      followersCopyingCount: 0,
      warnings,
    };
  }

  const includedRows = rows.filter((row) => getExclusionReason(row) === null);
  const followerRows = includedRows.filter((row) => row.role === "Follower");

  return {
    hasLeader,
    canCalculate,
    combinedNetPnl: sum(includedRows.map((row) => row.netPnl)),
    combinedDailyPnl: sum(includedRows.map((row) => row.dailyPnl)),
    combinedBalance: sum(includedRows.map((row) => row.balance)),
    leaderNetPnl: leader.netPnl,
    leaderDailyPnl: leader.dailyPnl,
    followerContributionNetPnl: sum(followerRows.map((row) => row.netPnl)),
    followerContributionDailyPnl: sum(followerRows.map((row) => row.dailyPnl)),
    activeCopiedAccountCount: includedRows.length,
    followersCopyingCount: followerRows.length,
    warnings,
  };
}

export function combineCopyTradePnlCalendarDays(
  rows: readonly CopyTradeAccountRow[],
  calendarDaysByAccountId: Record<number, AccountPnlCalendarDay[] | undefined>,
): AccountPnlCalendarDay[] {
  const byDate = new Map<string, AccountPnlCalendarDay>();

  rows.forEach((row) => {
    if (row.accountId === null || getExclusionReason(row) !== null) {
      return;
    }

    const days = calendarDaysByAccountId[row.accountId] ?? [];
    days.forEach((day) => {
      const current =
        byDate.get(day.date) ??
        ({
          date: day.date,
          trade_count: 0,
          gross_pnl: 0,
          fees: 0,
          net_pnl: 0,
        } satisfies AccountPnlCalendarDay);

      current.trade_count += day.trade_count;
      current.gross_pnl += day.gross_pnl;
      current.fees += day.fees;
      current.net_pnl += day.net_pnl;
      byDate.set(day.date, current);
    });
  });

  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

export function getDailyNetPnlForTradingDay(days: readonly AccountPnlCalendarDay[], tradingDay: string): number {
  const currentDay = days.find((day) => day.date === tradingDay && Number.isFinite(day.net_pnl));
  return currentDay?.net_pnl ?? 0;
}

export function getCopyTradeDailyNetPnlForTradingDay(
  days: readonly AccountPnlCalendarDay[],
  tradingDay: string,
  summary: Pick<AccountSummary, "active_days" | "net_pnl"> | null | undefined,
  lastTradeAt: string | null | undefined,
  currentBalance?: number | null,
): number {
  const calendarValue = getDailyNetPnlForTradingDay(days, tradingDay);
  if (calendarValue !== 0 || days.some((day) => day.date === tradingDay)) {
    return calendarValue;
  }

  if (!lastTradeAt) {
    return 0;
  }

  try {
    if (tradingDayKey(lastTradeAt) !== tradingDay) {
      return 0;
    }

    if (summary?.active_days === 1 && Number.isFinite(summary.net_pnl)) {
      return summary.net_pnl;
    }

    const balanceFallback = safeNumber(currentBalance);
    if (
      (!summary || (summary.active_days === 0 && summary.net_pnl === 0)) &&
      balanceFallback !== 0 &&
      Math.abs(balanceFallback) <= MAX_LIVE_BALANCE_DAILY_PNL_FALLBACK_ABS
    ) {
      return balanceFallback;
    }

    return 0;
  } catch {
    return 0;
  }
}

export function computeCopyTradeDriftSummary(
  rows: readonly CopyTradeAccountRow[],
  tradesByAccountId: Record<number, AccountTrade[] | undefined>,
  options: { resetAt?: string | null } = {},
): CopyTradeDriftSummary {
  const leader = rows.find((row) => row.role === "Leader" && row.accountId !== null) ?? null;
  const leaderTrades = leader?.accountId === null || leader?.accountId === undefined ? [] : tradesByAccountId[leader.accountId] ?? [];
  const comparableLeaderTrades = leaderTrades.filter(hasComparableTradeTime);
  const resetAtMs = parseResetAtMs(options.resetAt);
  const unmatchedFollowerTrades: Array<{
    accountId: number;
    accountName: string;
    trade: AccountTrade;
    startedAtMs: number;
    symbol: string;
    side: string;
    netPnl: number;
  }> = [];
  const accountBreakdowns = new Map<number, CopyTradeDriftAccountBreakdown>();

  rows.forEach((row) => {
    if (row.role !== "Follower" || row.accountId === null || row.status === "Error" || row.status === "Syncing") {
      return;
    }

    const accountId = row.accountId;
    const followerTrades = tradesByAccountId[accountId] ?? [];
    followerTrades.forEach((trade) => {
      const startedAtMs = getTradeStartedAtMs(trade);
      if (startedAtMs === null || (resetAtMs !== null && startedAtMs <= resetAtMs) || hasMatchingLeaderTrade(trade, comparableLeaderTrades)) {
        return;
      }

      const netPnl = safeNumber(trade.pnl);
      unmatchedFollowerTrades.push({
        accountId,
        accountName: row.accountName,
        trade,
        startedAtMs,
        symbol: getComparableSymbol(trade),
        side: normalizeSide(trade.side),
        netPnl,
      });

      const current =
        accountBreakdowns.get(accountId) ??
        ({
          accountId,
          accountName: row.accountName,
          followerOnlyTradeCount: 0,
          netPnl: 0,
        } satisfies CopyTradeDriftAccountBreakdown);
      current.followerOnlyTradeCount += 1;
      current.netPnl += netPnl;
      accountBreakdowns.set(accountId, current);
    });
  });

  const groups: Array<{ startedAtMs: number; symbol: string; side: string; tradeCount: number; netPnl: number; accountIds: Set<number> }> = [];
  unmatchedFollowerTrades
    .sort((left, right) => left.startedAtMs - right.startedAtMs)
    .forEach((sample) => {
      const existingGroup = groups.find(
        (group) =>
          group.symbol === sample.symbol &&
          group.side === sample.side &&
          Math.abs(group.startedAtMs - sample.startedAtMs) <= COPY_TRADE_MATCH_WINDOW_MS,
      );

      if (existingGroup) {
        existingGroup.tradeCount += 1;
        existingGroup.netPnl += sample.netPnl;
        existingGroup.accountIds.add(sample.accountId);
        return;
      }

      groups.push({
        startedAtMs: sample.startedAtMs,
        symbol: sample.symbol,
        side: sample.side,
        tradeCount: 1,
        netPnl: sample.netPnl,
        accountIds: new Set([sample.accountId]),
      });
    });

  return {
    likelyUncopyEventCount: groups.length,
    followerOnlyTradeCount: unmatchedFollowerTrades.length,
    followerOnlyNetPnl: sum(unmatchedFollowerTrades.map((sample) => sample.netPnl)),
    affectedAccountCount: accountBreakdowns.size,
    matchWindowMinutes: COPY_TRADE_MATCH_WINDOW_MS / 60_000,
    accounts: [...accountBreakdowns.values()].sort((left, right) => right.followerOnlyTradeCount - left.followerOnlyTradeCount),
  };
}

function buildAccountRow(
  account: AccountInfo,
  role: CopyTradeRole,
  snapshot: CopyTradeMetricSnapshot | undefined,
): CopyTradeAccountRow {
  const status = snapshot?.loadError ? "Error" : snapshot === undefined ? "Syncing" : getCopyTradeStatusForAccount(account);

  return {
    accountId: account.id,
    accountName: account.name || account.provider_name || `Account ${account.id}`,
    role,
    status,
    balance: safeNumber(account.balance),
    dailyPnl: safeNumber(snapshot?.dailyPnl),
    netPnl: safeNumber(snapshot?.netPnl),
    openPositions: Math.max(0, safeNumber(snapshot?.openPositions)),
    contributionNetPnl: 0,
    contributionDailyPnl: 0,
    includedInTotals: false,
    exclusionReason: null,
    loadError: snapshot?.loadError ?? null,
  };
}

function buildEmptyFollowerSlot(slotNumber: number): CopyTradeAccountRow {
  return {
    accountId: null,
    accountName: `Follower Slot ${slotNumber}`,
    role: "Follower",
    status: "Inactive",
    balance: 0,
    dailyPnl: 0,
    netPnl: 0,
    openPositions: 0,
    contributionNetPnl: 0,
    contributionDailyPnl: 0,
    includedInTotals: false,
    exclusionReason: "No account assigned",
    loadError: null,
  };
}

function applyContribution(row: CopyTradeAccountRow): CopyTradeAccountRow {
  const exclusionReason = getExclusionReason(row);
  const includedInTotals = exclusionReason === null;

  return {
    ...row,
    contributionNetPnl: includedInTotals ? row.netPnl : 0,
    contributionDailyPnl: includedInTotals ? row.dailyPnl : 0,
    includedInTotals,
    exclusionReason,
  };
}

function getExclusionReason(row: CopyTradeAccountRow): string | null {
  if (row.accountId === null) {
    return row.exclusionReason ?? "No account assigned";
  }

  if (row.role === "Leader") {
    return row.status === "Error" || row.status === "Syncing" ? `Leader is ${row.status.toLowerCase()}` : null;
  }

  if (row.status === "Locked Out") {
    return null;
  }

  if (row.status !== "Active") {
    return `${row.status} follower`;
  }

  return null;
}

function getCopyTradeStatusForAccount(account: AccountInfo): CopyTradeStatus {
  if (account.account_state === "ACTIVE") {
    return "Active";
  }

  if (account.account_state === "LOCKED_OUT") {
    return "Locked Out";
  }

  return "Inactive";
}

function normalizeCopyTradeSettings(value: unknown): CopyTradeSettings {
  if (!value || typeof value !== "object") {
    return defaultSettings;
  }

  const candidate = value as Partial<CopyTradeSettings>;
  const uncopyEventsResetAtByLeaderAccountId: Record<string, string> = {};

  const rawUncopyEventResets = candidate.uncopyEventsResetAtByLeaderAccountId;
  if (rawUncopyEventResets && typeof rawUncopyEventResets === "object") {
    Object.entries(rawUncopyEventResets).forEach(([leaderAccountId, resetAt]) => {
      if (!/^\d+$/.test(leaderAccountId) || typeof resetAt !== "string" || !Number.isFinite(Date.parse(resetAt))) {
        return;
      }
      uncopyEventsResetAtByLeaderAccountId[leaderAccountId] = resetAt;
    });
  }

  return {
    modeEnabled: candidate.modeEnabled === true,
    uncopyEventsResetAtByLeaderAccountId,
  };
}

function parseResetAtMs(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }

  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function safeNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function hasMatchingLeaderTrade(followerTrade: AccountTrade, leaderTrades: readonly AccountTrade[]): boolean {
  return leaderTrades.some((leaderTrade) => areCopyTradeMatches(leaderTrade, followerTrade));
}

function areCopyTradeMatches(leaderTrade: AccountTrade, followerTrade: AccountTrade): boolean {
  if (getComparableSymbol(leaderTrade) !== getComparableSymbol(followerTrade)) {
    return false;
  }

  if (normalizeSide(leaderTrade.side) !== normalizeSide(followerTrade.side)) {
    return false;
  }

  if (Math.abs(safeNumber(leaderTrade.size)) !== Math.abs(safeNumber(followerTrade.size))) {
    return false;
  }

  const leaderStartedAtMs = getTradeStartedAtMs(leaderTrade);
  const followerStartedAtMs = getTradeStartedAtMs(followerTrade);
  if (leaderStartedAtMs === null || followerStartedAtMs === null) {
    return false;
  }

  if (Math.abs(leaderStartedAtMs - followerStartedAtMs) > COPY_TRADE_MATCH_WINDOW_MS) {
    return false;
  }

  const leaderEndedAtMs = getTradeEndedAtMs(leaderTrade);
  const followerEndedAtMs = getTradeEndedAtMs(followerTrade);
  if (leaderEndedAtMs !== null && followerEndedAtMs !== null) {
    return Math.abs(leaderEndedAtMs - followerEndedAtMs) <= COPY_TRADE_MATCH_WINDOW_MS;
  }

  return true;
}

function hasComparableTradeTime(trade: AccountTrade): boolean {
  return getTradeStartedAtMs(trade) !== null;
}

function getTradeStartedAtMs(trade: AccountTrade): number | null {
  return parseTradeTimestamp(trade.entry_time ?? trade.timestamp);
}

function getTradeEndedAtMs(trade: AccountTrade): number | null {
  return parseTradeTimestamp(trade.exit_time ?? trade.timestamp);
}

function parseTradeTimestamp(value: string | null | undefined): number | null {
  if (!value) {
    return null;
  }
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function getComparableSymbol(trade: AccountTrade): string {
  return (trade.contract_id || trade.symbol || "").trim().toUpperCase();
}

function normalizeSide(side: string): string {
  const normalized = side.trim().toUpperCase();
  if (normalized === "BUY") {
    return "LONG";
  }
  if (normalized === "SELL") {
    return "SHORT";
  }
  return normalized;
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
