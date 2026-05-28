import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/Table";
import { Toggle } from "../../../components/ui/Toggle";
import { formatCurrency, formatInteger, formatNumber, formatPnl } from "../../../utils/formatters";
import type { CopyTradeAccountRow, CopyTradeDriftSummary, CopyTradeStatus, CopyTradeTotals } from "../copyTrade";

interface CopyTradePanelProps {
  rows: CopyTradeAccountRow[];
  totals: CopyTradeTotals;
  driftSummary: CopyTradeDriftSummary;
  loading: boolean;
  onFollowerCopyEnabledChange: (accountId: number, copyEnabled: boolean) => void;
}

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

export function CopyTradePanel({
  rows,
  totals,
  driftSummary,
  loading,
  onFollowerCopyEnabledChange,
}: CopyTradePanelProps) {
  const cappedWarnings = totals.warnings.slice(0, 3);
  const remainingWarningCount = Math.max(0, totals.warnings.length - cappedWarnings.length);
  const driftByAccountId = new Map(driftSummary.accounts.map((account) => [account.accountId, account]));

  return (
    <Card className="border-app-accent/30 bg-[radial-gradient(120%_130%_at_0%_0%,rgb(var(--theme-accent)/0.16),rgb(var(--theme-surface)/0.64)_48%,rgb(var(--theme-surface)/0.92)_100%)]">
      <CardHeader className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <CardTitle>Copy Trade Mode</CardTitle>
          <CardDescription>Combined leader and follower performance across up to five Topstep accounts.</CardDescription>
        </div>
        <Badge variant={totals.canCalculate ? "accent" : "warning"}>
          {loading ? "Syncing" : totals.canCalculate ? "Copy Adjusted" : "Needs Leader"}
        </Badge>
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
            detail={`${formatInteger(driftSummary.followerOnlyTradeCount)} follower-only trades`}
            valueClassName={driftSummary.likelyUncopyEventCount > 0 ? "text-app-warning" : "text-app-text"}
          />
          <CopyTradeStat
            label="Follower-Only P&L"
            value={formatPnl(driftSummary.followerOnlyNetPnl)}
            detail={`${formatInteger(driftSummary.affectedAccountCount)} affected accounts`}
            valueClassName={contributionClass(driftSummary.followerOnlyNetPnl)}
          />
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
          <Table className="min-w-[980px] table-fixed border-collapse whitespace-nowrap text-xs">
            <TableHeader className="sticky top-0 z-10 bg-app-surface/95">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[16%]">Account</TableHead>
                <TableHead className="w-[8%] text-center">Role</TableHead>
                <TableHead className="w-[9%] text-center">Status</TableHead>
                <TableHead className="w-[10%] text-center">Copy</TableHead>
                <TableHead className="w-[12%] text-right">Daily P&L</TableHead>
                <TableHead className="w-[12%] text-right">Net P&L</TableHead>
                <TableHead className="w-[12%] text-right">Contribution</TableHead>
                <TableHead className="w-[12%] text-right">Uncopy</TableHead>
                <TableHead className="w-[9%] text-right">Open</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, index) => {
                const drift = row.accountId === null ? null : driftByAccountId.get(row.accountId) ?? null;
                return (
                  <TableRow key={row.accountId ?? `slot-${index}`} className={row.includedInTotals ? "bg-app-accent/5" : undefined}>
                    <TableCell>
                      <div className="min-w-0">
                        <p className={cn("truncate font-semibold", row.accountId === null ? "text-app-muted" : "text-app-text")}>
                          {row.accountName}
                        </p>
                        <p className="text-[10px] text-app-muted">{row.accountId === null ? "Unassigned" : `ID ${row.accountId}`}</p>
                      </div>
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant={roleBadgeVariant(row.role)}>{row.role}</Badge>
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant={statusBadgeVariant(row.status)}>{row.status}</Badge>
                    </TableCell>
                    <TableCell className="text-center">
                      {row.role === "Leader" ? (
                        <span className="text-[11px] font-medium text-app-accent">Required</span>
                      ) : row.accountId === null ? (
                        <span className="text-[11px] text-app-muted">Disabled</span>
                      ) : (
                        <Toggle
                          checked={row.copyEnabled}
                          onChange={(checked) => onFollowerCopyEnabledChange(row.accountId!, checked)}
                          label={row.copyEnabled ? "On" : "Off"}
                          aria-label={`Toggle copy trading for ${row.accountName}`}
                          className="h-7 rounded-lg px-2 py-1"
                        />
                      )}
                    </TableCell>
                    <TableCell className={cn("text-right font-mono", contributionClass(row.dailyPnl))}>{formatPnl(row.dailyPnl)}</TableCell>
                    <TableCell className={cn("text-right font-mono", contributionClass(row.netPnl))}>{formatPnl(row.netPnl)}</TableCell>
                    <TableCell className={cn("text-right font-mono font-semibold", contributionClass(row.contributionNetPnl))}>
                      {formatPnl(row.contributionNetPnl)}
                      {row.exclusionReason ? <p className="text-[10px] font-normal text-app-muted">{row.exclusionReason}</p> : null}
                    </TableCell>
                    <TableCell className={cn("text-right font-mono", drift && drift.followerOnlyTradeCount > 0 ? "text-app-warning" : "text-app-muted")}>
                      {drift ? (
                        <>
                          {formatInteger(drift.followerOnlyTradeCount)}
                          <p className={cn("text-[10px]", contributionClass(drift.netPnl))}>{formatPnl(drift.netPnl)}</p>
                        </>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono">{formatNumber(row.openPositions, 0)}</TableCell>
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
