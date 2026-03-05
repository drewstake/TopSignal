import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { cn } from "../../components/ui/cn";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import {
  ACCOUNT_QUERY_PARAM,
  parseAccountId,
  readStoredAccountId,
  readStoredMainAccountId,
  writeStoredAccountId,
} from "../../lib/accountSelection";
import { sortAccountsForSelection } from "../../lib/accountOrdering";
import { accountsApi } from "../../lib/api";
import { formatTradeDirection, tradeDirectionBadgeVariant } from "../../lib/tradeDirection";
import { ACCOUNT_TRADES_SYNCED_EVENT, type AccountTradesSyncedDetail } from "../../lib/tradeSyncEvents";
import { buildTradeSymbolSearchText, getDisplayTradeSymbol } from "../../lib/tradeSymbol";
import type { AccountInfo, AccountSummary, AccountTrade } from "../../lib/types";
import { formatCurrency, formatInteger, formatNumber, formatPercent, formatPnl } from "../../utils/formatters";

const PAGE_SIZE = 50;
const DEFAULT_LIMIT = 200;

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
  timeZone: "America/New_York",
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});

const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 5,
});

const emptySummary: AccountSummary = {
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
  avgPointGain: null,
  avgPointLoss: null,
  pointsBasisUsed: "auto",
};

interface FilteredTradeStats {
  netPnl: number;
  fees: number;
  wins: number;
  losses: number;
  breakeven: number;
  longCount: number;
  shortCount: number;
  avgDurationMinutes: number | null;
  avgSize: number | null;
}

function formatFee(value: number) {
  return formatCurrency(-Math.abs(value));
}

function formatDurationCompact(minutes: number | null | undefined) {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) {
    return "-";
  }

  const safeMinutes = Math.max(0, minutes);
  const totalSeconds = Math.round(safeMinutes * 60);
  const totalMinutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const hours = Math.floor(totalMinutes / 60);
  const minutesRemainder = totalMinutes % 60;

  if (hours > 0) {
    return `${hours}h ${minutesRemainder}m`;
  }

  return `${minutesRemainder}m ${seconds}s`;
}

function formatTradePrice(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "-";
  }
  return priceFormatter.format(value);
}

function formatRatio(value: number) {
  if (!Number.isFinite(value)) {
    return value > 0 ? "Inf" : "N/A";
  }
  return formatNumber(value, 2);
}

function formatPointMove(value: number | null, basis: AccountSummary["pointsBasisUsed"]) {
  if (value === null || !Number.isFinite(value)) {
    return "N/A";
  }

  const decimals = basis === "SIL" ? 3 : 2;
  return `${formatNumber(Math.abs(value), decimals)} pts`;
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "-";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "-";
  }

  return timestampFormatter.format(parsed);
}

function formatDateLabel(value: string) {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return dateFormatter.format(parsed);
}

function formatWindowLabel(startDate: string, endDate: string) {
  if (startDate && endDate && startDate > endDate) {
    return "Invalid date range";
  }

  const startLabel = startDate ? formatDateLabel(startDate) : null;
  const endLabel = endDate ? formatDateLabel(endDate) : null;

  if (startLabel && endLabel) {
    return startLabel === endLabel ? startLabel : `${startLabel} to ${endLabel}`;
  }
  if (startLabel) {
    return `Since ${startLabel}`;
  }
  if (endLabel) {
    return `Through ${endLabel}`;
  }
  return "All stored trades";
}

function formatAccountStateLabel(state: AccountInfo["account_state"]) {
  if (state === "ACTIVE") {
    return "Active";
  }
  if (state === "LOCKED_OUT") {
    return "Locked out";
  }
  if (state === "HIDDEN") {
    return "Hidden";
  }
  return "Missing";
}

function accountStateBadgeVariant(state: AccountInfo["account_state"]) {
  if (state === "ACTIVE") {
    return "positive" as const;
  }
  if (state === "LOCKED_OUT" || state === "HIDDEN") {
    return "warning" as const;
  }
  return "negative" as const;
}

function tradingAccessBadge(canTrade: boolean | null) {
  if (canTrade === null) {
    return { label: "Trade status unknown", variant: "neutral" as const };
  }
  if (canTrade) {
    return { label: "Trading enabled", variant: "accent" as const };
  }
  return { label: "Trading disabled", variant: "warning" as const };
}

function pnlClass(value: number) {
  return value >= 0 ? "text-emerald-300" : "text-rose-300";
}

function share(part: number, total: number) {
  if (total <= 0) {
    return 0;
  }
  return (part / total) * 100;
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, value));
}

function toStartIso(date: string) {
  return `${date}T00:00:00Z`;
}

function toEndIso(date: string) {
  return `${date}T23:59:59.999Z`;
}

export function TradesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const accountFromQuery = parseAccountId(searchParams.get(ACCOUNT_QUERY_PARAM));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);

  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [symbolQuery, setSymbolQuery] = useState("");
  const [limit, setLimit] = useState(DEFAULT_LIMIT);

  const [summary, setSummary] = useState<AccountSummary>(emptySummary);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [trades, setTrades] = useState<AccountTrade[]>([]);
  const [tradesLoading, setTradesLoading] = useState(false);
  const [tradesError, setTradesError] = useState<string | null>(null);

  const [page, setPage] = useState(1);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const startIso = startDate ? toStartIso(startDate) : undefined;
  const endIso = endDate ? toEndIso(endDate) : undefined;
  const dateRangeInvalid = startDate !== "" && endDate !== "" && startDate > endDate;

  const setActiveAccount = useCallback(
    (accountId: number) => {
      const next = new URLSearchParams(searchParams);
      next.set(ACCOUNT_QUERY_PARAM, String(accountId));
      setSearchParams(next, { replace: true });
      writeStoredAccountId(accountId);
    },
    [searchParams, setSearchParams],
  );

  const loadAccounts = useCallback(async () => {
    try {
      const payload = await accountsApi.getSelectableAccounts();
      setAccounts(payload);
    } catch {
      setAccounts([]);
    }
  }, []);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  const orderedAccounts = useMemo(() => sortAccountsForSelection(accounts), [accounts]);

  useEffect(() => {
    if (orderedAccounts.length === 0) {
      return;
    }

    if (accountFromQuery && orderedAccounts.some((account) => account.id === accountFromQuery)) {
      writeStoredAccountId(accountFromQuery);
      return;
    }

    const persistedMainAccountId = orderedAccounts.find((account) => account.is_main)?.id ?? null;
    if (persistedMainAccountId) {
      setActiveAccount(persistedMainAccountId);
      return;
    }

    const storedMainAccountId = readStoredMainAccountId();
    if (storedMainAccountId && orderedAccounts.some((account) => account.id === storedMainAccountId)) {
      setActiveAccount(storedMainAccountId);
      return;
    }

    const storedAccountId = readStoredAccountId();
    if (storedAccountId && orderedAccounts.some((account) => account.id === storedAccountId)) {
      setActiveAccount(storedAccountId);
      return;
    }

    setActiveAccount(orderedAccounts[0].id);
  }, [orderedAccounts, accountFromQuery, setActiveAccount]);

  const selectedAccount = useMemo(
    () => orderedAccounts.find((account) => account.id === accountFromQuery) ?? null,
    [orderedAccounts, accountFromQuery],
  );
  const selectedAccountId = selectedAccount?.id ?? null;

  const loadTradesAndSummary = useCallback(async () => {
    if (!selectedAccountId) {
      setSummary(emptySummary);
      setTrades([]);
      setSummaryError(null);
      setTradesError(null);
      return;
    }

    if (dateRangeInvalid) {
      const message = "Start date must be before end date.";
      setSummary(emptySummary);
      setTrades([]);
      setSummaryError(message);
      setTradesError(message);
      return;
    }

    setSummaryLoading(true);
    setTradesLoading(true);
    setSummaryError(null);
    setTradesError(null);

    const summaryPromise = accountsApi.getSummary(selectedAccountId, {
      start: startIso,
      end: endIso,
    });
    const tradesPromise = accountsApi.getTrades(selectedAccountId, {
      limit,
      start: startIso,
      end: endIso,
    });

    try {
      const [nextSummary, nextTrades] = await Promise.all([summaryPromise, tradesPromise]);
      setSummary(nextSummary);
      setTrades(nextTrades);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load trade data";
      setSummaryError(message);
      setTradesError(message);
      setSummary(emptySummary);
      setTrades([]);
    } finally {
      setSummaryLoading(false);
      setTradesLoading(false);
    }
  }, [dateRangeInvalid, endIso, limit, selectedAccountId, startIso]);

  useEffect(() => {
    void loadTradesAndSummary();
  }, [loadTradesAndSummary]);

  useEffect(() => {
    setPage(1);
  }, [symbolQuery, selectedAccountId, startDate, endDate, limit]);

  useEffect(() => {
    function handleAccountTradesSynced(event: Event) {
      const detail = (event as CustomEvent<AccountTradesSyncedDetail>).detail;
      if (!selectedAccountId || detail.accountId !== selectedAccountId) {
        return;
      }

      if (detail.error) {
        setSyncMessage(detail.error);
        return;
      }

      setSyncMessage(`Fetched ${detail.fetchedCount}, stored ${detail.insertedCount} new events.`);
      void loadTradesAndSummary();
    }

    window.addEventListener(ACCOUNT_TRADES_SYNCED_EVENT, handleAccountTradesSynced as EventListener);
    return () => {
      window.removeEventListener(ACCOUNT_TRADES_SYNCED_EVENT, handleAccountTradesSynced as EventListener);
    };
  }, [loadTradesAndSummary, selectedAccountId]);

  const filteredTrades = useMemo(() => {
    const normalizedQuery = symbolQuery.trim().toLowerCase();
    if (!normalizedQuery) {
      return trades;
    }

    return trades.filter((trade) => {
      const symbol = buildTradeSymbolSearchText(trade.symbol, trade.contract_id);
      return symbol.includes(normalizedQuery);
    });
  }, [symbolQuery, trades]);

  const filteredTradeStats = useMemo<FilteredTradeStats>(() => {
    let netPnl = 0;
    let fees = 0;
    let wins = 0;
    let losses = 0;
    let breakeven = 0;
    let longCount = 0;
    let shortCount = 0;
    let durationTotal = 0;
    let durationCount = 0;
    let sizeTotal = 0;

    filteredTrades.forEach((trade) => {
      const pnlValue = trade.pnl ?? 0;
      const normalizedSide = formatTradeDirection(trade.side);

      netPnl += pnlValue;
      fees += Math.abs(trade.fees);
      sizeTotal += Math.abs(trade.size);

      if (pnlValue > 0) {
        wins += 1;
      } else if (pnlValue < 0) {
        losses += 1;
      } else {
        breakeven += 1;
      }

      if (normalizedSide === "LONG") {
        longCount += 1;
      } else if (normalizedSide === "SHORT") {
        shortCount += 1;
      }

      if (trade.duration_minutes !== null && trade.duration_minutes !== undefined && Number.isFinite(trade.duration_minutes)) {
        durationTotal += trade.duration_minutes;
        durationCount += 1;
      }
    });

    return {
      netPnl,
      fees,
      wins,
      losses,
      breakeven,
      longCount,
      shortCount,
      avgDurationMinutes: durationCount > 0 ? durationTotal / durationCount : null,
      avgSize: filteredTrades.length > 0 ? sizeTotal / filteredTrades.length : null,
    };
  }, [filteredTrades]);

  const totalPages = Math.max(1, Math.ceil(filteredTrades.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  const pagedTrades = useMemo(() => {
    const startIndex = (currentPage - 1) * PAGE_SIZE;
    return filteredTrades.slice(startIndex, startIndex + PAGE_SIZE);
  }, [currentPage, filteredTrades]);

  const pageStart = pagedTrades.length === 0 ? 0 : (currentPage - 1) * PAGE_SIZE + 1;
  const pageEnd = pagedTrades.length === 0 ? 0 : pageStart + pagedTrades.length - 1;
  const pageNetPnl = useMemo(
    () => pagedTrades.reduce((total, trade) => total + (trade.pnl ?? 0), 0),
    [pagedTrades],
  );

  const currentWindowLabel = useMemo(() => formatWindowLabel(startDate, endDate), [startDate, endDate]);
  const hasActiveFilters = Boolean(startDate || endDate || symbolQuery || limit !== DEFAULT_LIMIT);
  const searchIsActive = symbolQuery.trim().length > 0;
  const accountAccess = tradingAccessBadge(selectedAccount?.can_trade ?? null);

  const summaryWinShare = clampPercent(share(summary.win_count, summary.trade_count));
  const summaryLossShare = clampPercent(share(summary.loss_count, summary.trade_count));
  const summaryBreakevenShare = clampPercent(share(summary.breakeven_count, summary.trade_count));
  const filteredWinRate = clampPercent(share(filteredTradeStats.wins, filteredTrades.length));

  const heroNetValue = summaryLoading ? "Loading..." : summaryError ? "Unavailable" : formatPnl(summary.net_pnl);
  const heroWinRateValue = summaryLoading ? "Loading..." : summaryError ? "Unavailable" : formatPercent(summary.win_rate);
  const heroEventValue = tradesLoading ? "Loading..." : tradesError ? "Unavailable" : formatInteger(filteredTrades.length);
  const syncMessageIsError = Boolean(syncMessage && /failed|error/i.test(syncMessage));

  async function handleSyncNow() {
    if (!selectedAccountId || dateRangeInvalid) {
      return;
    }

    setSyncing(true);
    setSyncMessage(null);

    try {
      const result = await accountsApi.refreshTrades(selectedAccountId, {
        start: startIso,
        end: endIso,
      });
      await loadTradesAndSummary();
      setSyncMessage(`Fetched ${result.fetched_count}, stored ${result.inserted_count} new events.`);
    } catch (err) {
      setSyncMessage(err instanceof Error ? err.message : "Failed to sync trades");
    } finally {
      setSyncing(false);
    }
  }

  function handleClearFilters() {
    setStartDate("");
    setEndDate("");
    setSymbolQuery("");
    setLimit(DEFAULT_LIMIT);
    setSyncMessage(null);
  }

  return (
    <div className="flex flex-col gap-4 pb-8 lg:min-h-0 lg:flex-1 lg:gap-3 lg:overflow-hidden lg:pb-0">
      <section className="relative overflow-hidden rounded-[26px] border border-white/10 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.15),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(251,191,36,0.1),transparent_24%),linear-gradient(145deg,rgba(15,23,42,0.96),rgba(15,23,42,0.84))] p-4 shadow-[0_20px_70px_-42px_rgba(8,47,73,0.82)] lg:shrink-0">
        <div aria-hidden="true" className="pointer-events-none absolute -left-10 top-4 h-28 w-28 rounded-full bg-cyan-300/10 blur-3xl" />
        <div aria-hidden="true" className="pointer-events-none absolute bottom-0 right-0 h-32 w-32 rounded-full bg-amber-300/8 blur-3xl" />

        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-[11px] uppercase tracking-[0.26em] text-cyan-200/75">Trades Desk</p>
              {selectedAccount ? (
                <Badge variant={accountStateBadgeVariant(selectedAccount.account_state)}>
                  {formatAccountStateLabel(selectedAccount.account_state)}
                </Badge>
              ) : null}
              {selectedAccount?.is_main ? <Badge variant="accent">Main account</Badge> : null}
              {selectedAccount ? <Badge variant={accountAccess.variant}>{accountAccess.label}</Badge> : null}
            </div>

            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-white sm:text-[2rem]">
                {selectedAccount ? selectedAccount.name : "Trades"}
              </h1>
              <p className="mt-1 text-sm text-slate-400">
                {currentWindowLabel} <span className="text-slate-500">·</span>{" "}
                {selectedAccount?.last_trade_at ? formatTimestamp(selectedAccount.last_trade_at) : "No recent fills"}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={handleSyncNow} disabled={syncing || !selectedAccountId || dateRangeInvalid}>
              {syncing ? "Syncing..." : "Sync Latest"}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void loadTradesAndSummary()}
              disabled={summaryLoading || tradesLoading || !selectedAccountId || dateRangeInvalid}
            >
              Refresh
            </Button>
            {hasActiveFilters ? (
              <Button variant="ghost" size="sm" onClick={handleClearFilters}>
                Clear
              </Button>
            ) : null}
          </div>
        </div>

        <div className="relative mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <HeroStat
            label="Balance"
            value={selectedAccount ? formatCurrency(selectedAccount.balance) : "No account"}
            detail={selectedAccount ? `Account #${selectedAccount.id}` : "Choose an account"}
          />
          <HeroStat
            label="Window Net"
            value={heroNetValue}
            detail={currentWindowLabel}
            valueClassName={summaryLoading || summaryError ? undefined : pnlClass(summary.net_pnl)}
          />
          <HeroStat
            label="Win Rate"
            value={heroWinRateValue}
            detail={summaryError ? "Summary unavailable" : `${formatInteger(summary.trade_count)} trades`}
          />
          <HeroStat
            label={searchIsActive ? "Matches" : "Loaded"}
            value={heroEventValue}
            detail={searchIsActive ? symbolQuery.trim().toUpperCase() : `${formatInteger(limit)} row cap`}
          />
        </div>

        <div className="relative mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)_180px]">
          <FilterField label="Start">
            <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </FilterField>

          <FilterField label="End">
            <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </FilterField>

          <FilterField label="Symbol">
            <Input
              value={symbolQuery}
              onChange={(event) => setSymbolQuery(event.target.value)}
              placeholder="NQ, ES, CL..."
            />
          </FilterField>

          <FilterField label="Limit">
            <Select value={String(limit)} onChange={(event) => setLimit(Number(event.target.value))}>
              <option value={100}>100</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
              <option value={1000}>1000</option>
            </Select>
          </FilterField>
        </div>

        <div className="relative mt-3 flex flex-wrap gap-2 text-xs text-slate-300">
          <InlinePill label="Feed Net" value={tradesError ? "Unavailable" : formatPnl(filteredTradeStats.netPnl)} />
          <InlinePill label="Page" value={`${currentPage}/${totalPages}`} />
          <InlinePill label="Avg Hold" value={formatDurationCompact(filteredTradeStats.avgDurationMinutes)} />
          <InlinePill
            label="Avg Size"
            value={filteredTradeStats.avgSize === null ? "N/A" : formatNumber(filteredTradeStats.avgSize, 1)}
          />
        </div>

        {dateRangeInvalid ? (
          <div className="relative mt-3 rounded-2xl border border-rose-400/35 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            Start date must be before end date.
          </div>
        ) : null}

        {syncMessage ? (
          <div
            className={cn(
              "relative mt-3 rounded-2xl border px-3 py-2 text-xs",
              syncMessageIsError
                ? "border-rose-400/35 bg-rose-500/10 text-rose-200"
                : "border-cyan-400/25 bg-cyan-500/10 text-cyan-100",
            )}
          >
            {syncMessage}
          </div>
        ) : null}
      </section>

      {selectedAccount?.account_state === "MISSING" ? (
        <Card className="border-amber-400/40 bg-amber-500/10 p-4 lg:shrink-0">
          <p className="text-sm text-amber-100">
            This account is missing from ProjectX. Trade metrics are shown from locally stored data when live sync is unavailable.
          </p>
        </Card>
      ) : null}

      <section className="grid gap-3 lg:shrink-0 xl:grid-cols-12">
        {summaryLoading ? (
          Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={`summary-skeleton-${index}`} className="h-[220px] xl:col-span-4" />
          ))
        ) : summaryError ? (
          <Card className="xl:col-span-12">
            <p className="text-sm text-rose-300">{summaryError}</p>
          </Card>
        ) : (
          <>
            <Card className="xl:col-span-5 p-3 md:p-3.5">
              <CardHeader className="mb-2 space-y-0.5">
                <CardDescription className="text-[10px] uppercase tracking-[0.18em] text-cyan-200/70">Snapshot</CardDescription>
                <CardTitle className="text-base tracking-tight">P/L and output</CardTitle>
              </CardHeader>
              <CardContent className="space-y-0">
                <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
                  <MetricTile label="Net" value={formatPnl(summary.net_pnl)} valueClassName={pnlClass(summary.net_pnl)} />
                  <MetricTile label="Gross" value={formatPnl(summary.gross_pnl)} valueClassName={pnlClass(summary.gross_pnl)} />
                  <MetricTile label="Fees" value={formatCurrency(summary.fees)} />
                  <MetricTile
                    label="Expectancy"
                    value={formatPnl(summary.expectancy_per_trade)}
                    valueClassName={pnlClass(summary.expectancy_per_trade)}
                  />
                  <MetricTile label="Profit Factor" value={formatRatio(summary.profit_factor)} />
                  <MetricTile
                    label="Profit / Day"
                    value={formatPnl(summary.profit_per_day)}
                    valueClassName={pnlClass(summary.profit_per_day)}
                  />
                  <MetricTile label="Active Days" value={formatInteger(summary.active_days)} />
                  <MetricTile label="Executions" value={formatInteger(summary.execution_count)} />
                  <MetricTile label="Trades / Day" value={formatNumber(summary.avg_trades_per_day, 1)} />
                </div>
              </CardContent>
            </Card>

            <Card className="xl:col-span-4 p-3 md:p-3.5">
              <CardHeader className="mb-2 space-y-0.5">
                <CardDescription className="text-[10px] uppercase tracking-[0.18em] text-emerald-200/70">Outcomes</CardDescription>
                <CardTitle className="text-base tracking-tight">Hit rate and pacing</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="overflow-hidden rounded-full border border-slate-700/80 bg-slate-950/60">
                  <div className="flex h-2 w-full">
                    <div className="bg-emerald-300/85" style={{ width: `${summaryWinShare}%` }} />
                    <div className="bg-rose-300/85" style={{ width: `${summaryLossShare}%` }} />
                    <div className="bg-slate-500/80" style={{ width: `${summaryBreakevenShare}%` }} />
                  </div>
                </div>
                <div className="grid gap-1.5 sm:grid-cols-3">
                  <OutcomeRow
                    label="Wins"
                    count={summary.win_count}
                    percentage={summaryWinShare}
                    barClassName="bg-emerald-300/85"
                    countClassName="text-emerald-200"
                  />
                  <OutcomeRow
                    label="Losses"
                    count={summary.loss_count}
                    percentage={summaryLossShare}
                    barClassName="bg-rose-300/85"
                    countClassName="text-rose-200"
                  />
                  <OutcomeRow
                    label="Flat"
                    count={summary.breakeven_count}
                    percentage={summaryBreakevenShare}
                    barClassName="bg-slate-400/80"
                    countClassName="text-slate-200"
                  />
                </div>
                <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
                  <MetricTile label="Win Rate" value={formatPercent(summary.win_rate)} />
                  <MetricTile label="Day Win Rate" value={formatPercent(summary.day_win_rate)} />
                  <MetricTile label="Trades" value={formatInteger(summary.trade_count)} />
                  <MetricTile label="Green Days" value={formatInteger(summary.green_days)} />
                  <MetricTile label="Avg Win Hold" value={formatDurationCompact(summary.avg_win_duration_minutes)} />
                  <MetricTile label="Avg Loss Hold" value={formatDurationCompact(summary.avg_loss_duration_minutes)} />
                </div>
              </CardContent>
            </Card>

            <Card className="xl:col-span-3 p-3 md:p-3.5">
              <CardHeader className="mb-2 space-y-0.5">
                <CardDescription className="text-[10px] uppercase tracking-[0.18em] text-amber-200/70">Risk + Feed</CardDescription>
                <CardTitle className="text-base tracking-tight">Concise pressure check</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-2">
                  <MetricTile label="Max DD" value={formatPnl(summary.max_drawdown)} valueClassName="text-rose-300" />
                  <MetricTile label="Tail Risk" value={formatPnl(summary.tail_risk_5pct)} valueClassName="text-rose-300" />
                  <MetricTile label="Avg Win" value={formatPnl(summary.avg_win)} valueClassName={pnlClass(summary.avg_win)} />
                  <MetricTile label="Avg Loss" value={formatPnl(summary.avg_loss)} valueClassName={pnlClass(summary.avg_loss)} />
                  <MetricTile
                    label="Filtered PnL"
                    value={tradesError ? "Unavailable" : formatPnl(filteredTradeStats.netPnl)}
                    valueClassName={tradesError ? undefined : pnlClass(filteredTradeStats.netPnl)}
                  />
                  <MetricTile
                    label="Filtered Win"
                    value={`${formatNumber(filteredWinRate, 0)}%`}
                    hint={`${formatInteger(filteredTrades.length)} matches`}
                  />
                </div>
                {(summary.avgPointGain !== null || summary.avgPointLoss !== null) ? (
                  <div className="flex flex-wrap gap-1.5 text-xs text-slate-300">
                    <InlinePill label="Point Gain" value={formatPointMove(summary.avgPointGain, summary.pointsBasisUsed)} />
                    <InlinePill label="Point Loss" value={formatPointMove(summary.avgPointLoss, summary.pointsBasisUsed)} />
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </>
        )}
      </section>

      <Card className="overflow-hidden lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
        <CardHeader className="mb-0 flex flex-col gap-2 border-b border-slate-800/70 pb-3 xl:flex-row xl:items-end xl:justify-between lg:shrink-0">
          <div>
            <CardDescription className="uppercase tracking-[0.2em] text-amber-200/75">Execution Feed</CardDescription>
            <CardTitle className="text-lg tracking-tight">Trade Events</CardTitle>
            <CardDescription className="mt-1">
              {tradesError
                ? tradesError
                : pagedTrades.length === 0
                  ? "No trades match the current feed filters."
                  : `Showing ${formatInteger(pageStart)}-${formatInteger(pageEnd)} of ${formatInteger(filteredTrades.length)} matching events.`}
            </CardDescription>
          </div>

          <div className="flex flex-wrap gap-2 text-xs text-slate-300">
            <InlinePill label="Page Net" value={tradesError ? "Unavailable" : formatPnl(pageNetPnl)} />
            <InlinePill label="Matches" value={formatInteger(filteredTrades.length)} />
            <InlinePill label="Win" value={`${formatNumber(filteredWinRate, 0)}%`} />
            <InlinePill label="Avg Hold" value={formatDurationCompact(filteredTradeStats.avgDurationMinutes)} />
          </div>
        </CardHeader>

        <CardContent className="space-y-2.5 pt-3 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
          {tradesLoading ? (
            <>
              <div className="grid gap-2.5 lg:hidden">
                {Array.from({ length: 4 }).map((_, index) => (
                  <Skeleton key={`trade-card-skeleton-${index}`} className="h-20" />
                ))}
              </div>
              <div className="hidden lg:flex lg:min-h-0 lg:flex-1">
                <div className="flex min-h-0 flex-1 overflow-hidden rounded-[22px] border border-slate-800/80 bg-slate-950/45">
                  <div className="min-h-0 flex-1 overflow-auto">
                    <table className="w-full min-w-[1280px] table-fixed border-collapse">
                      <tbody>
                        {Array.from({ length: 8 }).map((_, index) => (
                          <tr key={`trade-table-skeleton-${index}`} className="border-b border-slate-800/70">
                            <td colSpan={11} className="px-3 py-2">
                              <Skeleton className="h-5 w-full" />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </>
          ) : tradesError ? (
            <div className="rounded-[24px] border border-rose-400/35 bg-rose-500/10 px-4 py-5 text-sm text-rose-200">
              {tradesError}
            </div>
          ) : pagedTrades.length === 0 ? (
            <div className="rounded-[24px] border border-slate-700/80 bg-slate-950/45 px-4 py-6 text-sm text-slate-400">
              No trades match your filters.
            </div>
          ) : (
            <>
              <div className="grid gap-2.5 lg:hidden">
                {pagedTrades.map((trade) => (
                  <TradeFeedCard key={trade.id} trade={trade} />
                ))}
              </div>

              <div className="hidden lg:flex lg:min-h-0 lg:flex-1">
                <div className="flex min-h-0 flex-1 overflow-hidden rounded-[22px] border border-slate-800/80 bg-slate-950/45">
                  <div className="min-h-0 flex-1 overflow-auto">
                    <table className="w-full min-w-[1260px] table-fixed border-collapse text-[11px] leading-tight">
                      <thead className="sticky top-0 z-10 bg-slate-950/95 text-xs uppercase tracking-[0.16em] text-slate-400 backdrop-blur">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium">Entry Time (ET)</th>
                          <th className="px-3 py-2 text-left font-medium">Exit Time (ET)</th>
                          <th className="px-3 py-2 text-right font-medium">Duration</th>
                          <th className="px-3 py-2 text-left font-medium">Symbol</th>
                          <th className="w-[96px] px-3 py-2 text-center font-medium">Direction</th>
                          <th className="w-[72px] px-3 py-2 text-center font-medium">Size</th>
                          <th className="px-3 py-2 text-right font-medium">Entry Price</th>
                          <th className="px-3 py-2 text-right font-medium">Exit Price</th>
                          <th className="px-3 py-2 text-right font-medium">Fees</th>
                          <th className="px-3 py-2 text-right font-medium">PnL</th>
                          <th className="px-3 py-2 text-right font-medium">Trade ID</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {pagedTrades.map((trade) => {
                          const pnlValue = trade.pnl ?? 0;
                          const direction = formatTradeDirection(trade.side);
                          const entryTime = trade.entry_time;
                          const exitTime = trade.exit_time ?? trade.timestamp;
                          const entryPrice = trade.entry_price;
                          const exitPrice = trade.exit_price ?? trade.price;

                          return (
                            <tr key={trade.id} className="transition hover:bg-slate-900/60">
                              <td className="px-3 py-2 text-left text-slate-300">{entryTime ? formatTimestamp(entryTime) : "-"}</td>
                              <td className="px-3 py-2 text-left text-slate-300">{formatTimestamp(exitTime)}</td>
                              <td className="px-3 py-2 text-right text-slate-300">{formatDurationCompact(trade.duration_minutes)}</td>
                              <td className="px-3 py-2 text-left font-semibold text-slate-100">
                                <div className="flex items-center gap-2">
                                  <span>{getDisplayTradeSymbol(trade.symbol, trade.contract_id)}</span>
                                  <span className="truncate text-[10px] font-normal text-slate-500">
                                    {trade.contract_id || trade.order_id}
                                  </span>
                                </div>
                              </td>
                              <td className="px-3 py-2 text-center">
                                <Badge variant={tradeDirectionBadgeVariant(trade.side)}>{direction}</Badge>
                              </td>
                              <td className="px-3 py-2 text-center text-slate-200">{formatInteger(trade.size)}</td>
                              <td className="px-3 py-2 text-right font-mono text-slate-200">{formatTradePrice(entryPrice)}</td>
                              <td className="px-3 py-2 text-right font-mono text-slate-200">{formatTradePrice(exitPrice)}</td>
                              <td className="px-3 py-2 text-right text-slate-300">{formatFee(trade.fees)}</td>
                              <td className={cn("px-3 py-2 text-right font-semibold", pnlClass(pnlValue))}>{formatPnl(pnlValue)}</td>
                              <td className="px-3 py-2 text-right font-mono text-[11px] text-slate-400">
                                {trade.source_trade_id ?? trade.order_id}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-800/70 pt-3 text-xs text-slate-400 lg:shrink-0">
            <p>
              {pagedTrades.length === 0
                ? "Page 1 of 1"
                : `Page ${formatInteger(currentPage)} of ${formatInteger(totalPages)} (${formatInteger(pageStart)}-${formatInteger(pageEnd)})`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPage <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Prev
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={currentPage >= totalPages}
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

interface HeroStatProps {
  label: string;
  value: string;
  detail: string;
  valueClassName?: string;
}

function HeroStat({ label, value, detail, valueClassName }: HeroStatProps) {
  return (
    <div className="rounded-[18px] border border-white/10 bg-slate-950/35 p-2.5 backdrop-blur-sm">
      <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">{label}</p>
      <p className={cn("mt-1.5 text-lg font-semibold tracking-tight text-slate-100 sm:text-xl", valueClassName)}>{value}</p>
      <p className="mt-1 text-xs text-slate-400">{detail}</p>
    </div>
  );
}

interface MetricTileProps {
  label: string;
  value: string;
  hint?: string;
  valueClassName?: string;
  className?: string;
}

function MetricTile({ label, value, hint, valueClassName, className }: MetricTileProps) {
  return (
    <div className={cn("rounded-[14px] border border-white/8 bg-slate-950/35 p-1.5", className)}>
      <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">{label}</p>
      <p className={cn("mt-0.5 text-sm font-semibold text-slate-100 sm:text-[15px]", valueClassName)}>{value}</p>
      {hint ? <p className="mt-0.5 text-[10px] text-slate-400">{hint}</p> : null}
    </div>
  );
}

interface FilterFieldProps {
  label: string;
  children: ReactNode;
}

function FilterField({ label, children }: FilterFieldProps) {
  return (
    <div>
      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.18em] text-slate-500">{label}</label>
      {children}
    </div>
  );
}

interface InlinePillProps {
  label: string;
  value: string;
}

function InlinePill({ label, value }: InlinePillProps) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-slate-950/35 px-2 py-1">
      <span className="uppercase tracking-[0.16em] text-slate-500">{label}</span>
      <span className="font-medium text-slate-200">{value}</span>
    </span>
  );
}

interface OutcomeRowProps {
  label: string;
  count: number;
  percentage: number;
  barClassName: string;
  countClassName?: string;
}

function OutcomeRow({ label, count, percentage, barClassName, countClassName }: OutcomeRowProps) {
  return (
    <div className="rounded-[14px] border border-white/8 bg-slate-950/35 p-1.5">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-slate-300">{label}</span>
        <span className={cn("font-semibold text-slate-200", countClassName)}>{formatInteger(count)}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full border border-slate-700/80 bg-slate-950/60">
        <div className={cn("h-full transition-all duration-300", barClassName)} style={{ width: `${percentage}%` }} />
      </div>
      <p className="mt-1 text-[10px] text-slate-500">{formatNumber(percentage, 0)}%</p>
    </div>
  );
}

interface TradeFeedCardProps {
  trade: AccountTrade;
}

function TradeFeedCard({ trade }: TradeFeedCardProps) {
  const pnlValue = trade.pnl ?? 0;
  const direction = formatTradeDirection(trade.side);
  const entryTime = trade.entry_time;
  const exitTime = trade.exit_time ?? trade.timestamp;
  const entryPrice = trade.entry_price;
  const exitPrice = trade.exit_price ?? trade.price;

  return (
    <article className="rounded-[18px] border border-slate-800/80 bg-slate-950/45 p-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-base font-semibold tracking-tight text-slate-100">
              {getDisplayTradeSymbol(trade.symbol, trade.contract_id)}
            </p>
            <Badge variant={tradeDirectionBadgeVariant(trade.side)}>{direction}</Badge>
          </div>
          <p className="mt-0.5 truncate text-[10px] text-slate-500">{trade.source_trade_id ?? trade.order_id}</p>
        </div>

        <div className="text-right">
          <p className={cn("text-base font-semibold tracking-tight", pnlClass(pnlValue))}>{formatPnl(pnlValue)}</p>
          <p className="mt-0.5 text-[11px] text-slate-400">{formatFee(trade.fees)} fees</p>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[11px] text-slate-300">
        <CompactFeedPill label="In" value={entryTime ? formatTimestamp(entryTime) : "-"} />
        <CompactFeedPill label="Out" value={formatTimestamp(exitTime)} />
        <CompactFeedPill label="Dur" value={formatDurationCompact(trade.duration_minutes)} />
        <CompactFeedPill label="Qty" value={formatInteger(trade.size)} />
        <CompactFeedPill label="Entry Px" value={formatTradePrice(entryPrice)} />
        <CompactFeedPill label="Exit Px" value={formatTradePrice(exitPrice)} />
      </div>
    </article>
  );
}

interface CompactFeedPillProps {
  label: string;
  value: string;
}

function CompactFeedPill({ label, value }: CompactFeedPillProps) {
  return (
    <div className="rounded-[13px] border border-slate-800/75 bg-slate-950/55 px-2 py-1.5">
      <span className="block text-[9px] uppercase tracking-[0.16em] text-slate-500">{label}</span>
      <span className="mt-0.5 block truncate font-medium text-slate-200">{value}</span>
    </div>
  );
}
