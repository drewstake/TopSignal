import { useMemo, useState } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/Table";
import { formatCurrency, formatInteger, formatNumber, formatPnl } from "../../../utils/formatters";
import { getDemoAccountId, getDemoAccountName } from "../../../lib/demoMode";
import type { AccountInfo } from "../../../lib/types";
import type { CopyTradeAccountRow, CopyTradeDriftSummary, CopyTradeStatus, CopyTradeTotals } from "../copyTrade";

interface CopyTradePanelProps {
  rows: CopyTradeAccountRow[];
  totals: CopyTradeTotals;
  driftSummary: CopyTradeDriftSummary;
  driftResetAt: string | null;
  accounts: AccountInfo[];
  leaderAccountId: number | null;
  selectedFollowerAccountIds: number[];
  maxFollowers: number;
  loading: boolean;
  onFollowerSelectionChange: (accountId: number, selected: boolean) => void;
  onResetUncopyEvents: () => void;
}

const resetTimeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/New_York",
});

function statusBadgeVariant(status: CopyTradeStatus) {
  if (status === "Active") {
    return "positive" as const;
  }
  if (status === "Syncing") {
    return "accent" as const;
  }
  if (status === "Error") {
    return "negative" as const;
  }
  return "warning" as const;
}

function roleBadgeVariant(role: CopyTradeAccountRow["role"]) {
  return role === "Leader" ? "accent" as const : "neutral" as const;
}

function contributionClass(value: number) {
  if (value > 0) {
    return "text-app-positive";
  }
  if (value < 0) {
    return "text-app-negative";
  }
  return "text-app-muted";
}

function formatResetTime(value: string | null) {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return resetTimeFormatter.format(date);
}

export function CopyTradePanel({
  rows,
  totals,
  driftSummary,
  driftResetAt,
  accounts,
  leaderAccountId,
  selectedFollowerAccountIds,
  maxFollowers,
  loading,
  onFollowerSelectionChange,
  onResetUncopyEvents,
}: CopyTradePanelProps) {
  const selectedFollowerCount = selectedFollowerAccountIds.length;
  const [followerSelectorState, setFollowerSelectorState] = useState(() => ({
    leaderAccountId,
    expanded: selectedFollowerCount === 0,
  }));
  const visibleWarnings = loading ? [] : totals.warnings;
  const cappedWarnings = visibleWarnings.slice(0, 3);
  const remainingWarningCount = Math.max(0, visibleWarnings.length - cappedWarnings.length);
  const driftResetLabel = formatResetTime(driftResetAt);
  const selectedFollowerIds = useMemo(() => new Set(selectedFollowerAccountIds), [selectedFollowerAccountIds]);
  const followerCandidates = accounts.filter(
    (account) => account.id !== leaderAccountId && (account.account_state === "ACTIVE" || account.account_state === "LOCKED_OUT"),
  );
  const selectedFollowerAccounts = followerCandidates.filter((account) => selectedFollowerIds.has(account.id));
  const followerSelectionFull = selectedFollowerCount >= maxFollowers;
  const followerSelectorExpanded =
    followerSelectorState.leaderAccountId === leaderAccountId ? followerSelectorState.expanded : selectedFollowerCount === 0;
  const showFollowerEditor = selectedFollowerCount === 0 || followerSelectorExpanded;

  return (
    <Card className="border-app-accent/30 bg-[radial-gradient(120%_130%_at_0%_0%,rgb(var(--theme-accent)/0.16),rgb(var(--theme-surface)/0.64)_48%,rgb(var(--theme-surface)/0.92)_100%)]">
      <CardHeader className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <CardTitle>Copy Trade Mode</CardTitle>
          <CardDescription>Combined leader and follower performance across up to five Topstep accounts.</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onResetUncopyEvents}
            disabled={!totals.hasLeader || loading}
            className="rounded-lg px-2.5"
          >
            Reset Uncopy Events
          </Button>
          <Badge variant={totals.canCalculate ? "accent" : "warning"}>
            {loading ? "Syncing" : totals.canCalculate ? "Copy Adjusted" : "Needs Leader"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
          <div className="rounded-lg border border-app-accent/35 bg-app-bg/40 p-3 md:col-span-2 xl:col-span-2">
            <p className="text-[10px] uppercase tracking-[0.14em] text-app-muted">Copy Trade Net</p>
            <p className={cn("mt-1 text-2xl font-semibold tracking-tight", contributionClass(totals.combinedNetPnl))}>
              {formatPnl(totals.combinedNetPnl)}
            </p>
          </div>
          <CopyTradeStat label="Combined Daily P&L" value={formatPnl(totals.combinedDailyPnl)} valueClassName={contributionClass(totals.combinedDailyPnl)} />
          <CopyTradeStat label="Combined Balance" value={formatCurrency(totals.combinedBalance)} />
          <CopyTradeStat label="Leader P&L" value={formatPnl(totals.leaderNetPnl)} valueClassName={contributionClass(totals.leaderNetPnl)} />
          <CopyTradeStat
            label="Follower P&L"
            value={formatPnl(totals.followerContributionNetPnl)}
            valueClassName={contributionClass(totals.followerContributionNetPnl)}
          />
          <CopyTradeStat
            label="Accounts Copying"
            value={`${formatInteger(totals.activeCopiedAccountCount)} / 5`}
            detail={`${formatInteger(totals.followersCopyingCount)} followers`}
          />
          <CopyTradeStat
            label="Uncopy Events"
            value={formatInteger(driftSummary.likelyUncopyEventCount)}
            detail={`${formatInteger(driftSummary.followerOnlyTradeCount)} follower-only trades${driftResetLabel ? ` since ${driftResetLabel}` : ""}`}
            valueClassName={driftSummary.likelyUncopyEventCount > 0 ? "text-app-warning" : "text-app-text"}
          />
          <CopyTradeStat
            label="Follower-Only P&L"
            value={formatPnl(driftSummary.followerOnlyNetPnl)}
            detail={`${formatInteger(driftSummary.affectedAccountCount)} affected accounts`}
            valueClassName={contributionClass(driftSummary.followerOnlyNetPnl)}
          />
        </div>

        <div className="rounded-lg border border-app-border/75 bg-app-bg/35 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[10px] uppercase tracking-[0.14em] text-app-muted">Followers</p>
            <div className="flex items-center gap-2">
              <Badge variant={selectedFollowerCount > 0 ? "accent" : "warning"}>{`${formatInteger(selectedFollowerCount)} / ${formatInteger(maxFollowers)}`}</Badge>
              {selectedFollowerCount > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setFollowerSelectorState((current) => {
                      const currentExpanded =
                        current.leaderAccountId === leaderAccountId ? current.expanded : selectedFollowerCount === 0;
                      return {
                        leaderAccountId,
                        expanded: !currentExpanded,
                      };
                    })
                  }
                  className="h-7 rounded-lg px-2 text-[10px]"
                >
                  {showFollowerEditor ? "Done" : "Edit"}
                </Button>
              ) : null}
            </div>
          </div>

          {selectedFollowerCount > 0 && !showFollowerEditor ? (
            <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {selectedFollowerAccounts.map((account) => {
                const status = getAccountStatusLabel(account);
                return (
                  <div key={account.id} className="min-w-0 rounded-lg border border-app-accent/45 bg-app-accent/10 px-2.5 py-2 text-xs">
                    <p className="truncate font-semibold text-app-text">{getAccountDisplayName(account)}</p>
                    <div className="mt-1 flex items-center gap-1.5 text-[10px] text-app-muted">
                      <span>{`ID ${getDemoAccountId(account.id)}`}</span>
                      <Badge variant={status === "Active" ? "positive" : "warning"} className="px-1.5 py-0.5 text-[9px]">
                        {status}
                      </Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : followerCandidates.length > 0 ? (
            <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
              {followerCandidates.map((account) => {
                const selected = selectedFollowerIds.has(account.id);
                const disabled = loading || (!selected && followerSelectionFull);
                const status = getAccountStatusLabel(account);
                return (
                  <label
                    key={account.id}
                    className={cn(
                      "flex min-w-0 items-start gap-2 rounded-lg border px-2.5 py-2 text-xs transition",
                      selected
                        ? "border-app-accent/55 bg-app-accent/15 text-app-text"
                        : "border-app-border/75 bg-app-surface/45 text-app-text-soft",
                      disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:border-app-accent/45 hover:bg-app-accent/10",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      disabled={disabled}
                      onChange={(event) => onFollowerSelectionChange(account.id, event.currentTarget.checked)}
                      className="mt-0.5 h-4 w-4 rounded border-app-border bg-app-bg accent-[rgb(var(--theme-accent))]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-semibold">{getAccountDisplayName(account)}</span>
                      <span className="mt-1 flex items-center gap-1.5 text-[10px] text-app-muted">
                        <span>{`ID ${getDemoAccountId(account.id)}`}</span>
                        <Badge variant={status === "Active" ? "positive" : "warning"} className="px-1.5 py-0.5 text-[9px]">
                          {status}
                        </Badge>
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          ) : (
            <p className="mt-2 text-xs text-app-muted">No follower accounts available.</p>
          )}
        </div>

        {driftSummary.likelyUncopyEventCount > 0 ? (
          <div className="rounded-lg border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning">
            Detected follower trades without a matching leader trade within {formatNumber(driftSummary.matchWindowMinutes, 0)} minutes.
          </div>
        ) : null}

        {cappedWarnings.length > 0 ? (
          <div className="rounded-lg border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning">
            {cappedWarnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
            {remainingWarningCount > 0 ? <p>{`${remainingWarningCount} more copy-trade warning${remainingWarningCount === 1 ? "" : "s"}.`}</p> : null}
          </div>
        ) : null}

        <div className="overflow-auto rounded-xl border border-app-border/80">
          <Table className="min-w-[640px] table-fixed border-collapse whitespace-nowrap text-xs">
            <TableHeader className="sticky top-0 z-10 bg-app-surface/95">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[38%]">Account</TableHead>
                <TableHead className="w-[13%] text-center">Role</TableHead>
                <TableHead className="w-[15%] text-center">Status</TableHead>
                <TableHead className="w-[17%] text-right">Daily P&L</TableHead>
                <TableHead className="w-[17%] text-right">Net P&L</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => {
                return (
                  <TableRow key={row.accountId ?? `slot-${index}`} className={row.includedInTotals ? "bg-app-accent/5" : undefined}>
                    <TableCell>
                      <div className="min-w-0">
                        <p className={cn("truncate font-semibold", row.accountId === null ? "text-app-muted" : "text-app-text")}>
                          {row.accountName}
                        </p>
                        <p className="text-[10px] text-app-muted">{row.accountId === null ? "Unassigned" : `ID ${getDemoAccountId(row.accountId)}`}</p>
                      </div>
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant={roleBadgeVariant(row.role)}>{row.role}</Badge>
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant={statusBadgeVariant(row.status)}>{row.status}</Badge>
                    </TableCell>
                    <TableCell className={cn("text-right font-mono", contributionClass(row.dailyPnl))}>{formatPnl(row.dailyPnl)}</TableCell>
                    <TableCell className={cn("text-right font-mono", contributionClass(row.netPnl))}>{formatPnl(row.netPnl)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

function getAccountDisplayName(account: AccountInfo) {
  return getDemoAccountName({
    id: account.id,
    name: account.name || account.provider_name || `Account ${account.id}`,
  });
}

function getAccountStatusLabel(account: AccountInfo) {
  return account.account_state === "ACTIVE" ? "Active" : "Locked Out";
}

function CopyTradeStat({
  label,
  value,
  detail,
  valueClassName,
}: {
  label: string;
  value: string;
  detail?: string;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-lg border border-app-border/75 bg-app-bg/35 p-3">
      <p className="text-[10px] uppercase tracking-[0.14em] text-app-muted">{label}</p>
      <p className={cn("mt-1 text-sm font-semibold text-app-text", valueClassName)}>{value}</p>
      {detail ? <p className="mt-1 text-[10px] text-app-muted">{detail}</p> : null}
    </div>
  );
}
