import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/Table";
import { formatCurrency, formatInteger, formatNumber, formatPnl } from "../../../utils/formatters";
import type { CopyTradeAccountRow, CopyTradeDriftSummary, CopyTradeStatus, CopyTradeTotals } from "../copyTrade";

interface CopyTradePanelProps {
  rows: CopyTradeAccountRow[];
  totals: CopyTradeTotals;
  driftSummary: CopyTradeDriftSummary;
  driftResetAt: string | null;
  loading: boolean;
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
  loading,
  onResetUncopyEvents,
}: CopyTradePanelProps) {
  const cappedWarnings = totals.warnings.slice(0, 3);
  const remainingWarningCount = Math.max(0, totals.warnings.length - cappedWarnings.length);
  const driftResetLabel = formatResetTime(driftResetAt);

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
                        <p className="text-[10px] text-app-muted">{row.accountId === null ? "Unassigned" : `ID ${row.accountId}`}</p>
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
