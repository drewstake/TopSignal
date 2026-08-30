import { useId, useMemo, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import type { ExpenseMonthlySummary, PayoutMonthlySummary } from "../../lib/types";
import { formatCurrency } from "../../utils/formatters";

export interface ExpenseCalendarCardProps {
  months: readonly ExpenseMonthlySummary[];
  payoutMonths: readonly PayoutMonthlySummary[];
  loading: boolean;
  error: string | null;
  asOfDate: string;
  selectedMonth?: string | null;
  onMonthSelect?: (month: string | null) => void;
}

const monthNameFormatter = new Intl.DateTimeFormat("en-US", {
  month: "long",
  timeZone: "UTC",
});

function getIsoYear(value: string) {
  const year = Number.parseInt(value.slice(0, 4), 10);
  return Number.isFinite(year) ? year : new Date().getUTCFullYear();
}

function getMonthKey(value: string) {
  return value.slice(0, 7);
}

function formatMonthKey(year: number, monthIndex: number) {
  return `${year}-${String(monthIndex + 1).padStart(2, "0")}`;
}

function formatMonthName(monthIndex: number) {
  return monthNameFormatter.format(new Date(Date.UTC(2000, monthIndex, 1)));
}

function netClass(amountCents: number) {
  if (amountCents > 0) {
    return "text-app-positive-text";
  }
  if (amountCents < 0) {
    return "text-app-negative-text";
  }
  return "text-app-text-soft";
}

function tileBackground(amountCents: number, maxAbsAmountCents: number) {
  if (amountCents === 0) {
    return "var(--dashboard-calendar-empty)";
  }

  const intensity = Math.min(1, Math.abs(amountCents) / Math.max(maxAbsAmountCents, 1));
  const alpha = 0.1 + intensity * 0.3;
  const color = amountCents > 0 ? "--dashboard-positive-rgb" : "--dashboard-negative-rgb";
  return `rgb(var(${color}) / ${alpha.toFixed(3)})`;
}

export function ExpenseCalendarCard({
  months,
  payoutMonths,
  loading,
  error,
  asOfDate,
  selectedMonth = null,
  onMonthSelect,
}: ExpenseCalendarCardProps) {
  const titleId = `${useId().replace(/:/g, "")}-expense-calendar-title`;
  const asOfYear = getIsoYear(asOfDate);
  const dataYears = useMemo(
    () => [...months, ...payoutMonths]
      .map((month) => getIsoYear(month.month))
      .sort((left, right) => left - right),
    [months, payoutMonths],
  );
  const earliestYear = dataYears[0] ?? asOfYear;
  const latestDataYear = dataYears[dataYears.length - 1] ?? asOfYear;
  const latestYear = Math.max(latestDataYear, asOfYear);
  const [requestedYear, setRequestedYear] = useState<number | null>(null);
  const visibleYear = Math.min(latestYear, Math.max(earliestYear, requestedYear ?? latestDataYear));

  const expenseMonthMap = useMemo(
    () => new Map(months.map((month) => [getMonthKey(month.month), month])),
    [months],
  );
  const payoutMonthMap = useMemo(
    () => new Map(payoutMonths.map((month) => [getMonthKey(month.month), month])),
    [payoutMonths],
  );
  const visibleMonths = useMemo(
    () => Array.from({ length: 12 }, (_, monthIndex) => {
      const key = formatMonthKey(visibleYear, monthIndex);
      return {
        key,
        monthIndex,
        expenseSummary: expenseMonthMap.get(key) ?? null,
        payoutSummary: payoutMonthMap.get(key) ?? null,
      };
    }),
    [expenseMonthMap, payoutMonthMap, visibleYear],
  );
  const yearSummary = useMemo(
    () => visibleMonths.reduce(
      (summary, month) => ({
        payoutAmountCents: summary.payoutAmountCents + (month.payoutSummary?.total_amount_cents ?? 0),
        expenseAmountCents: summary.expenseAmountCents + (month.expenseSummary?.total_amount_cents ?? 0),
      }),
      { payoutAmountCents: 0, expenseAmountCents: 0 },
    ),
    [visibleMonths],
  );
  const yearNetAmountCents = yearSummary.payoutAmountCents - yearSummary.expenseAmountCents;
  const maxAbsMonthNetCents = useMemo(
    () => visibleMonths.reduce(
      (maxAmount, month) => Math.max(
        maxAmount,
        Math.abs(
          (month.payoutSummary?.total_amount_cents ?? 0)
          - (month.expenseSummary?.total_amount_cents ?? 0),
        ),
      ),
      0,
    ),
    [visibleMonths],
  );

  const canGoPrevious = visibleYear > earliestYear;
  const canGoNext = visibleYear < latestYear;

  return (
    <Card aria-labelledby={titleId} aria-busy={loading}>
      <CardHeader className="mb-3 space-y-0">
        <div className="grid gap-2 sm:grid-cols-[auto_1fr_auto] sm:items-center">
          <CardTitle id={titleId} className="sm:justify-self-start">Monthly P&amp;L Calendar</CardTitle>
          <p className="text-sm font-medium text-app-text-soft sm:text-center md:text-base" aria-live="polite">
            {loading
              ? "Loading monthly cash flow..."
              : error
                ? "Monthly cash flow unavailable"
                : `${formatCurrency(yearNetAmountCents / 100)} net`}
          </p>
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              disabled={!canGoPrevious}
              onClick={() => setRequestedYear(visibleYear - 1)}
              aria-label="Previous year"
            >
              Prev
            </Button>
            <p className="min-w-12 text-center text-xs font-medium text-app-muted" aria-live="polite">
              {visibleYear}
            </p>
            <Button
              variant="ghost"
              size="sm"
              disabled={!canGoNext}
              onClick={() => setRequestedYear(visibleYear + 1)}
              aria-label="Next year"
            >
              Next
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {loading ? (
          <div role="status" aria-live="polite">
            <span className="sr-only">Loading payout and expense calendar</span>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" aria-hidden="true">
              {Array.from({ length: 12 }, (_, index) => (
                <Skeleton key={index} className="h-24" />
              ))}
            </div>
          </div>
        ) : error ? (
          <p className="rounded-xl border border-app-negative/30 bg-app-negative/10 px-3 py-2 text-sm text-app-negative" role="alert">
            {error}
          </p>
        ) : (
          <div
            role="grid"
            aria-label={`${visibleYear} monthly payouts and expenses`}
            className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4"
          >
            {visibleMonths.map(({ key, monthIndex, expenseSummary, payoutSummary }) => {
              const monthName = formatMonthName(monthIndex);
              const expenseAmountCents = expenseSummary?.total_amount_cents ?? 0;
              const payoutAmountCents = payoutSummary?.total_amount_cents ?? 0;
              const netAmountCents = payoutAmountCents - expenseAmountCents;
              const isSelected = selectedMonth === key;
              const accessibleLabel = `${monthName} ${visibleYear}, ${formatCurrency(netAmountCents / 100)} net`;

              return (
                <div key={key} role="gridcell" aria-selected={isSelected} className="min-w-0">
                  <button
                    type="button"
                    aria-label={accessibleLabel}
                    aria-pressed={isSelected}
                    title={accessibleLabel}
                    onClick={() => onMonthSelect?.(isSelected ? null : key)}
                    className={`flex h-full min-h-24 w-full min-w-0 flex-col rounded-lg border p-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-focus ${
                      isSelected
                        ? "border-app-accent/90 ring-1 ring-app-accent/70"
                        : netAmountCents > 0
                          ? "border-app-positive/35 hover:border-app-positive/60"
                          : netAmountCents < 0
                            ? "border-app-negative/35 hover:border-app-negative/60"
                            : "border-app-border/60 hover:border-app-border-strong"
                    } ${onMonthSelect ? "cursor-pointer" : "cursor-default"}`}
                    style={{ backgroundColor: tileBackground(netAmountCents, maxAbsMonthNetCents) }}
                  >
                    <span className="text-xs font-semibold uppercase tracking-wide text-app-muted-text">
                      {monthName}
                    </span>
                    <span className={`mt-auto pt-3 text-base font-semibold tabular-nums ${netClass(netAmountCents)}`}>
                      {formatCurrency(netAmountCents / 100)} net
                    </span>
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
