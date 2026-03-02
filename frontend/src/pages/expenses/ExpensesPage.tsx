import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Drawer } from "../../components/ui/Drawer";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/Table";
import {
  accountsApi,
  createExpense,
  deleteExpense,
  getExpenseTotals,
  isApiError,
  listExpenses,
} from "../../lib/api";
import {
  decrementStandardActivationCount,
  incrementStandardActivationCount,
  markEvaluationExpensesSynced,
  readCombineSpendSnapshot,
  syncCombineSpendTracker,
} from "../../lib/combineTracker";
import {
  EXPENSE_ACCOUNT_TYPES,
  EXPENSE_PLAN_SIZES,
  getExpensePresetAmountCents,
  type ExpenseStage,
} from "../../lib/expensePresets";
import type { ExpenseCategory, ExpenseRecord, ExpenseTotals } from "../../lib/types";

const TOTAL_RANGE = "all_time";
const CATEGORY_OPTIONS: ExpenseCategory[] = ["evaluation_fee", "activation_fee", "reset_fee", "data_fee", "other"];

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const trackerDateFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

function formatCategoryLabel(category: string) {
  return category
    .split("_")
    .map((value) => `${value.charAt(0).toUpperCase()}${value.slice(1)}`)
    .join(" ");
}

function getTodayLocalIsoDate() {
  const now = new Date();
  const year = now.getFullYear();
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  const day = `${now.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parsePositiveInt(value: string) {
  if (!value.trim()) {
    return undefined;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return null;
  }
  return parsed;
}

function splitTags(input: string) {
  return input
    .split(",")
    .map((value) => value.trim())
    .filter((value, index, array) => value.length > 0 && array.indexOf(value) === index);
}

function isAutoTrackedCombineExpense(expense: ExpenseRecord): boolean {
  return expense.tags.includes("combine_tracker");
}

interface AddExpenseState {
  accountType: "no_activation" | "standard" | "practice";
  planSize: "50k" | "100k" | "150k";
  stage: ExpenseStage;
  expenseDate: string;
  amount: string;
  accountId: string;
  description: string;
  tags: string;
}

function buildInitialAddExpenseState(accountId: string): AddExpenseState {
  return {
    accountType: "no_activation",
    planSize: "50k",
    stage: "evaluation_fee",
    expenseDate: getTodayLocalIsoDate(),
    amount: "115.00",
    accountId,
    description: "",
    tags: "",
  };
}

export function ExpensesPage() {
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [category, setCategory] = useState<ExpenseCategory | "">("");
  const [accountFilter, setAccountFilter] = useState("");

  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const [items, setItems] = useState<ExpenseRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [totals, setTotals] = useState<ExpenseTotals | null>(null);
  const [totalsLoading, setTotalsLoading] = useState(false);
  const [totalsError, setTotalsError] = useState<string | null>(null);
  const [combineSpendSnapshot, setCombineSpendSnapshot] = useState(readCombineSpendSnapshot);
  const [combineTrackerLoading, setCombineTrackerLoading] = useState(false);
  const [combineTrackerError, setCombineTrackerError] = useState<string | null>(null);

  const [addOpen, setAddOpen] = useState(false);
  const [addState, setAddState] = useState<AddExpenseState>(buildInitialAddExpenseState(accountFilter));
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const didInitialCombineSyncRef = useRef(false);

  const parsedAccountFilter = useMemo(() => parsePositiveInt(accountFilter), [accountFilter]);

  const loadExpenses = useCallback(async () => {
    if (parsedAccountFilter === null) {
      setError("Account ID filter must be a positive integer.");
      setItems([]);
      setTotal(0);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const payload = await listExpenses({
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        category: category || undefined,
        account_id: parsedAccountFilter,
        limit,
        offset,
      });
      setItems(payload.items);
      setTotal(payload.total);
    } catch (err) {
      setItems([]);
      setTotal(0);
      setError(err instanceof Error ? err.message : "Failed to load expenses");
    } finally {
      setLoading(false);
    }
  }, [category, endDate, limit, offset, parsedAccountFilter, startDate]);

  const loadTotals = useCallback(async () => {
    if (parsedAccountFilter === null) {
      setTotalsError("Account ID filter must be a positive integer.");
      setTotals(null);
      return;
    }

    setTotalsLoading(true);
    setTotalsError(null);
    try {
      const response = await getExpenseTotals(TOTAL_RANGE, parsedAccountFilter);
      setTotals(response);
    } catch (err) {
      setTotals(null);
      setTotalsError(err instanceof Error ? err.message : "Failed to load totals");
    } finally {
      setTotalsLoading(false);
    }
  }, [parsedAccountFilter]);

  const syncCombineTracker = useCallback(async () => {
    setCombineTrackerLoading(true);
    setCombineTrackerError(null);
    try {
      const payload = await accountsApi.getAccounts(false);
      const syncResult = syncCombineSpendTracker(payload);
      let nextSnapshot = syncResult.snapshot;
      const syncedAccountIds: number[] = [];
      let failedCount = 0;

      for (const purchase of syncResult.unsyncedEvaluationPurchases) {
        try {
          await createExpense({
            expense_date: purchase.purchasedOn,
            amount_cents: purchase.amountCents,
            category: "evaluation_fee",
            plan_size: purchase.planSize,
            account_id: purchase.accountId,
            description: `Auto tracked combine purchase (${purchase.planSize.toUpperCase()})`,
            tags: ["combine_tracker", "auto"],
          });
          syncedAccountIds.push(purchase.accountId);
        } catch (err) {
          if (isApiError(err) && err.status === 409 && err.detail === "duplicate_expense") {
            syncedAccountIds.push(purchase.accountId);
          } else {
            failedCount += 1;
          }
        }
      }

      if (syncedAccountIds.length > 0) {
        nextSnapshot = markEvaluationExpensesSynced(syncedAccountIds);
        await Promise.all([loadExpenses(), loadTotals()]);
      }

      if (failedCount > 0) {
        setCombineTrackerError(`Failed to save ${failedCount} combine expense${failedCount === 1 ? "" : "s"}.`);
      }
      setCombineSpendSnapshot(nextSnapshot);
    } catch (err) {
      setCombineTrackerError(err instanceof Error ? err.message : "Failed to sync combine spend tracker");
    } finally {
      setCombineTrackerLoading(false);
    }
  }, [loadExpenses, loadTotals]);

  useEffect(() => {
    void loadExpenses();
  }, [loadExpenses]);

  useEffect(() => {
    void loadTotals();
  }, [loadTotals]);

  useEffect(() => {
    if (didInitialCombineSyncRef.current) {
      return;
    }
    didInitialCombineSyncRef.current = true;
    void syncCombineTracker();
  }, [syncCombineTracker]);

  useEffect(() => {
    setOffset(0);
  }, [startDate, endDate, category, accountFilter, limit]);

  useEffect(() => {
    if (addState.accountType !== "standard" && addState.stage !== "evaluation_fee") {
      setAddState((current) => ({ ...current, stage: "evaluation_fee" }));
    }
  }, [addState.accountType, addState.stage]);

  useEffect(() => {
    const preset = getExpensePresetAmountCents(addState.accountType, addState.planSize, addState.stage);
    if (preset !== null) {
      setAddState((current) => ({
        ...current,
        amount: (preset / 100).toFixed(2),
      }));
    }
  }, [addState.accountType, addState.planSize, addState.stage]);

  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.floor(offset / limit) + 1;
  const practiceBlocked = addState.accountType === "practice";
  const combineTrackerTotalAmount = combineSpendSnapshot.totalCostCents / 100;
  const trackerStartedOnLabel = trackerDateFormatter.format(
    new Date(`${combineSpendSnapshot.startedOn}T00:00:00.000Z`),
  );

  function resetAddForm() {
    setAddState(buildInitialAddExpenseState(accountFilter));
    setAddError(null);
  }

  function handleOpenAdd() {
    resetAddForm();
    setAddOpen(true);
  }

  async function handleDeleteExpense(expense: ExpenseRecord) {
    const confirmed = window.confirm(`Delete expense #${expense.id} for ${currencyFormatter.format(expense.amount)}?`);
    if (!confirmed) {
      return;
    }

    try {
      await deleteExpense(expense.id);
      await Promise.all([loadExpenses(), loadTotals()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete expense");
    }
  }

  function handleAddStandardActivation() {
    setCombineSpendSnapshot(incrementStandardActivationCount(1));
    setCombineTrackerError(null);
  }

  function handleRemoveStandardActivation() {
    setCombineSpendSnapshot(decrementStandardActivationCount(1));
    setCombineTrackerError(null);
  }

  async function handleSubmitNewExpense(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAddError(null);

    if (practiceBlocked) {
      setAddError("Practice accounts are free. Expenses are disabled.");
      return;
    }

    const parsedModalAccountId = parsePositiveInt(addState.accountId);
    if (parsedModalAccountId === null) {
      setAddError("Account ID must be a positive integer.");
      return;
    }

    const amount = Number.parseFloat(addState.amount);
    if (!Number.isFinite(amount) || amount < 0) {
      setAddError("Amount must be a non-negative number.");
      return;
    }

    setAdding(true);
    try {
      await createExpense({
        expense_date: addState.expenseDate,
        amount,
        category: addState.stage,
        account_type: addState.accountType,
        plan_size: addState.planSize,
        account_id: parsedModalAccountId,
        description: addState.description.trim() || undefined,
        tags: splitTags(addState.tags),
        is_practice: addState.accountType === "practice",
      });

      setAddOpen(false);
      await Promise.all([loadExpenses(), loadTotals()]);
    } catch (err) {
      if (isApiError(err) && err.status === 400 && err.detail === "practice_accounts_are_free") {
        setAddError("Practice accounts are free. Expenses are disabled.");
      } else {
        setAddError(err instanceof Error ? err.message : "Failed to create expense");
      }
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="space-y-6 pb-10">
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="xl:col-span-2">
          <CardHeader className="mb-2">
            <CardDescription>All-time spend</CardDescription>
            <CardTitle className="text-2xl">
              {totalsLoading ? "..." : totals ? currencyFormatter.format(totals.total_amount) : "$0.00"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-slate-400">
              {totals ? `${totals.count} expense${totals.count === 1 ? "" : "s"}` : "No data"}
            </p>
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader className="mb-2">
            <CardDescription>Combine spend tracker</CardDescription>
            <CardTitle className="text-2xl">
              {combineTrackerLoading ? "..." : currencyFormatter.format(combineTrackerTotalAmount)}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {combineTrackerError ? (
              <p className="text-xs text-rose-300">{combineTrackerError}</p>
            ) : (
              <p className="text-xs text-slate-400">
                {combineSpendSnapshot.totalTrackedCombines} combine purchase
                {combineSpendSnapshot.totalTrackedCombines === 1 ? "" : "s"} since {trackerStartedOnLabel} (
                {`50k: ${combineSpendSnapshot.countsByPlan["50k"]} | 100k: ${combineSpendSnapshot.countsByPlan["100k"]} | 150k: ${combineSpendSnapshot.countsByPlan["150k"]}`})
              </p>
            )}
            <p className="text-xs text-slate-500">
              Standard activations: {combineSpendSnapshot.standardActivationCount} (
              {currencyFormatter.format(combineSpendSnapshot.standardActivationCostCents / 100)}). Prefixes: 50KTC /
              100KTC / 150KTC. Rates: $115 / $168 / $221.
            </p>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={handleAddStandardActivation}>
                Add Standard Activation (+$150)
              </Button>
              <Button variant="ghost" size="sm" onClick={handleRemoveStandardActivation}>
                Remove Standard Activation (-$150)
              </Button>
              <Button variant="ghost" size="sm" onClick={() => void syncCombineTracker()} disabled={combineTrackerLoading}>
                {combineTrackerLoading ? "Syncing..." : "Sync Combine Expenses"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {totalsError ? <p className="text-sm text-rose-300">{totalsError}</p> : null}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Expenses</CardTitle>
              <CardDescription>Track paid account fees and operational costs.</CardDescription>
            </div>
            <Button onClick={handleOpenAdd}>Add Expense</Button>
          </div>
        </CardHeader>

        <CardContent>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Start Date</label>
              <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">End Date</label>
              <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Category</label>
              <Select
                value={category}
                onChange={(event) => setCategory((event.target.value as ExpenseCategory | "") || "")}
              >
                <option value="">All categories</option>
                {CATEGORY_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {formatCategoryLabel(value)}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Account ID</label>
              <Input
                value={accountFilter}
                onChange={(event) => setAccountFilter(event.target.value)}
                placeholder="Any"
                inputMode="numeric"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Page Size</label>
              <Select value={String(limit)} onChange={(event) => setLimit(Number(event.target.value))}>
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={200}>200</option>
              </Select>
            </div>
          </div>

          <div className="mt-4 overflow-x-auto rounded-xl border border-slate-800/80">
            <Table className="min-w-[980px]">
              <TableHeader>
                <tr>
                  <TableHead>Date</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Plan Size</TableHead>
                  <TableHead>Account Type</TableHead>
                  <TableHead className="text-right">Account ID</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Tags</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </tr>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={9} className="text-center text-slate-400">
                      Loading expenses...
                    </TableCell>
                  </TableRow>
                ) : error ? (
                  <TableRow>
                    <TableCell colSpan={9} className="text-center text-rose-300">
                      {error}
                    </TableCell>
                  </TableRow>
                ) : items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={9} className="text-center text-slate-400">
                      No expenses found.
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((expense) => (
                    <TableRow key={expense.id}>
                      <TableCell>{dateFormatter.format(new Date(`${expense.expense_date}T00:00:00.000Z`))}</TableCell>
                      <TableCell>{formatCategoryLabel(expense.category)}</TableCell>
                      <TableCell className="text-right font-mono">{currencyFormatter.format(expense.amount)}</TableCell>
                      <TableCell>{expense.plan_size ?? "-"}</TableCell>
                      <TableCell>{expense.account_type ?? "-"}</TableCell>
                      <TableCell className="text-right">{expense.account_id ?? "-"}</TableCell>
                      <TableCell className="max-w-[240px] truncate" title={expense.description ?? undefined}>
                        {expense.description ?? "-"}
                      </TableCell>
                      <TableCell className="max-w-[220px] truncate" title={expense.tags.join(", ")}>
                        {expense.tags.length > 0 ? expense.tags.join(", ") : "-"}
                      </TableCell>
                      <TableCell className="text-right">
                        {isAutoTrackedCombineExpense(expense) ? (
                          <span className="text-xs text-slate-500">Auto-tracked</span>
                        ) : (
                          <Button variant="danger" size="sm" onClick={() => void handleDeleteExpense(expense)}>
                            Delete
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          <div className="mt-4 flex items-center justify-between gap-3">
            <p className="text-xs text-slate-400">
              Page {Math.min(currentPage, totalPages)} of {totalPages} ({total} total)
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0 || loading}
                onClick={() => setOffset((current) => Math.max(0, current - limit))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={offset + limit >= total || loading}
                onClick={() => setOffset((current) => current + limit)}
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Drawer
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add Expense"
        description="Use paid-account presets for Topstep evaluation and activation fees."
      >
        <form className="space-y-3" onSubmit={(event) => void handleSubmitNewExpense(event)}>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Account Type</label>
            <Select
              value={addState.accountType}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  accountType: event.target.value as AddExpenseState["accountType"],
                }))
              }
            >
              {EXPENSE_ACCOUNT_TYPES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Plan Size</label>
            <Select
              value={addState.planSize}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  planSize: event.target.value as AddExpenseState["planSize"],
                }))
              }
            >
              {EXPENSE_PLAN_SIZES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </div>

          {addState.accountType === "standard" ? (
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Stage</label>
              <Select
                value={addState.stage}
                onChange={(event) =>
                  setAddState((current) => ({
                    ...current,
                    stage: event.target.value as ExpenseStage,
                  }))
                }
              >
                <option value="evaluation_fee">Evaluation Fee</option>
                <option value="activation_fee">Activation Fee</option>
              </Select>
            </div>
          ) : (
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Stage</label>
              <Input value="evaluation_fee" disabled readOnly />
            </div>
          )}

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Date</label>
            <Input
              type="date"
              value={addState.expenseDate}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  expenseDate: event.target.value,
                }))
              }
            />
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Amount (USD)</label>
            <Input
              value={addState.amount}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  amount: event.target.value,
                }))
              }
              inputMode="decimal"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Account ID (Optional)</label>
            <Input
              value={addState.accountId}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  accountId: event.target.value,
                }))
              }
              inputMode="numeric"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Description (Optional)</label>
            <Input
              value={addState.description}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  description: event.target.value,
                }))
              }
              placeholder="Topstep 50k evaluation"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Tags (Optional)</label>
            <Input
              value={addState.tags}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  tags: event.target.value,
                }))
              }
              placeholder="topstep, february"
            />
          </div>

          {practiceBlocked ? (
            <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              Practice accounts are free. Expenses are disabled.
            </p>
          ) : null}

          {addError ? <p className="text-sm text-rose-300">{addError}</p> : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={adding || practiceBlocked}>
              {adding ? "Saving..." : "Save Expense"}
            </Button>
          </div>
        </form>
      </Drawer>
    </div>
  );
}
