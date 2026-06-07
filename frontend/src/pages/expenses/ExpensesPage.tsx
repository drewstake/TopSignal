import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Drawer } from "../../components/ui/Drawer";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/Table";
import { Textarea } from "../../components/ui/Textarea";
import {
  accountsApi,
  createPayout,
  createExpense,
  deletePayout,
  deleteExpense,
  getExpenseTotals,
  getPayoutTotals,
  isApiError,
  listPayouts,
  listExpenses,
} from "../../lib/api";
import {
  getCombinePriceCentsFromAccountName,
  getCombinePlanSizeFromAccountName,
  isDailyLossLimitCombineAccountName,
  isTrackedCombinePurchaseExpense,
  markEvaluationExpensesSynced,
  readCombineSpendSnapshot,
  suppressEvaluationExpenseSync,
  syncCombineSpendTracker,
  syncCombineSpendTrackerFromExpenses,
} from "../../lib/combineTracker";
import {
  EXPENSE_ACCOUNT_TYPES,
  EXPENSE_PLAN_SIZES,
  getExpenseAccountTypeLabel,
  getExpensePresetAmountCents,
  type ExpenseAccountPresetType,
  type ExpenseStage,
} from "../../lib/expensePresets";
import type { ExpenseCategory, ExpenseRecord, ExpenseTotals, PayoutRecord, PayoutTotals } from "../../lib/types";

const TOTAL_RANGE = "all_time";
const CATEGORY_OPTIONS: ExpenseCategory[] = ["evaluation_fee", "activation_fee", "reset_fee", "data_fee", "other"];
const COMBINE_EXPENSE_PAGE_SIZE = 200;
const PAYOUT_PAGE_SIZE = 50;
const NET_RANGE_OPTIONS = [
  { key: "one_month", label: "1 Month", months: 1 },
  { key: "three_months", label: "3 Months", months: 3 },
  { key: "six_months", label: "6 Months", months: 6 },
  { key: "year_to_date", label: "YTD", months: null },
  { key: "one_year", label: "1 Year", months: 12 },
  { key: "all_time", label: "All Time", months: null },
] as const;

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

function formatCategoryLabel(category: string) {
  return category
    .split("_")
    .map((value) => `${value.charAt(0).toUpperCase()}${value.slice(1)}`)
    .join(" ");
}

function getTodayLocalIsoDate() {
  return formatLocalIsoDate(new Date());
}

function formatLocalIsoDate(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function subtractLocalMonths(date: Date, months: number) {
  const target = new Date(date.getFullYear(), date.getMonth() - months, 1);
  const lastDayOfTargetMonth = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  target.setDate(Math.min(date.getDate(), lastDayOfTargetMonth));
  return target;
}

function getTrailingDateRange(months: number) {
  const end = new Date();
  return {
    startDate: formatLocalIsoDate(subtractLocalMonths(end, months)),
    endDate: formatLocalIsoDate(end),
  };
}

function getYearToDateRange() {
  const end = new Date();
  return {
    startDate: `${end.getFullYear()}-01-01`,
    endDate: formatLocalIsoDate(end),
  };
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

function getNetProfitTitleClassName(amount: number, loading: boolean) {
  if (loading) {
    return "text-2xl";
  }
  if (amount > 0) {
    return "text-2xl text-emerald-300";
  }
  if (amount < 0) {
    return "text-2xl text-rose-300";
  }
  return "text-2xl";
}

function getNetProfitAmountClassName(amount: number) {
  if (amount > 0) {
    return "text-emerald-300";
  }
  if (amount < 0) {
    return "text-rose-300";
  }
  return "text-slate-100";
}

function getNetProfitPositionLabel(amount: number) {
  if (amount > 0) {
    return `Positive by ${currencyFormatter.format(amount)}`;
  }
  if (amount < 0) {
    return `Negative by ${currencyFormatter.format(Math.abs(amount))}`;
  }
  return "Break-even";
}

function isAutoTrackedCombineExpense(expense: ExpenseRecord): boolean {
  return expense.tags.includes("combine_tracker");
}

function isSpreadsheetImportedTopstepExpense(expense: ExpenseRecord): boolean {
  return expense.tags.includes("topstep_import");
}

function getLatestSpreadsheetImportedTopstepExpenseDate(expenses: ExpenseRecord[]): string | null {
  let latestDate: string | null = null;
  for (const expense of expenses) {
    if (!isSpreadsheetImportedTopstepExpense(expense)) {
      continue;
    }
    if (latestDate === null || expense.expense_date > latestDate) {
      latestDate = expense.expense_date;
    }
  }
  return latestDate;
}

interface ActiveCombineAccount {
  accountId: number;
  planSize: "50k" | "100k" | "150k";
  amountCents: number;
  isDailyLossLimit: boolean;
}

function collectActiveCombineAccounts(
  accounts: Array<{ id: number; name: string; provider_name?: string; status?: string; account_state?: string }>,
): ActiveCombineAccount[] {
  const output: ActiveCombineAccount[] = [];
  for (const account of accounts) {
    const rawState = (account.account_state ?? account.status ?? "").trim().toUpperCase();
    if (rawState !== "ACTIVE" && rawState !== "LOCKED_OUT") {
      continue;
    }
    const providerBackedName = account.provider_name ?? account.name;
    const planSize = getCombinePlanSizeFromAccountName(providerBackedName);
    if (planSize === null) {
      continue;
    }
    output.push({
      accountId: account.id,
      planSize,
      amountCents: getCombinePriceCentsFromAccountName(providerBackedName) ?? 0,
      isDailyLossLimit: isDailyLossLimitCombineAccountName(providerBackedName),
    });
  }
  return output;
}

interface AddExpenseState {
  accountType: ExpenseAccountPresetType;
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
    accountType: "standard",
    planSize: "50k",
    stage: "evaluation_fee",
    expenseDate: getTodayLocalIsoDate(),
    amount: "49.00",
    accountId,
    description: "",
    tags: "",
  };
}

interface AddPayoutState {
  payoutDate: string;
  amount: string;
  notes: string;
}

interface NetRangeSummary {
  key: string;
  label: string;
  netAmount: number;
  expenseAmount: number;
  payoutAmount: number;
  expenseCount: number;
  payoutCount: number;
}

function buildInitialAddPayoutState(): AddPayoutState {
  return {
    payoutDate: getTodayLocalIsoDate(),
    amount: "",
    notes: "",
  };
}

export function ExpensesPage() {
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [category, setCategory] = useState<ExpenseCategory | "">("");

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
  const [addState, setAddState] = useState<AddExpenseState>(buildInitialAddExpenseState(""));
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [payoutItems, setPayoutItems] = useState<PayoutRecord[]>([]);
  const [payoutTotal, setPayoutTotal] = useState(0);
  const [payoutOffset, setPayoutOffset] = useState(0);
  const [payoutLoading, setPayoutLoading] = useState(false);
  const [payoutError, setPayoutError] = useState<string | null>(null);
  const [payoutTotals, setPayoutTotals] = useState<PayoutTotals | null>(null);
  const [payoutTotalsLoading, setPayoutTotalsLoading] = useState(false);
  const [payoutTotalsError, setPayoutTotalsError] = useState<string | null>(null);
  const [netRanges, setNetRanges] = useState<NetRangeSummary[]>([]);
  const [netRangesLoading, setNetRangesLoading] = useState(false);
  const [netRangesError, setNetRangesError] = useState<string | null>(null);
  const [addPayoutOpen, setAddPayoutOpen] = useState(false);
  const [addPayoutState, setAddPayoutState] = useState<AddPayoutState>(buildInitialAddPayoutState());
  const [addPayoutError, setAddPayoutError] = useState<string | null>(null);
  const [addingPayout, setAddingPayout] = useState(false);
  const didInitialCombineSyncRef = useRef(false);

  const loadExpenses = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await listExpenses({
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        category: category || undefined,
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
  }, [category, endDate, limit, offset, startDate]);

  const loadTotals = useCallback(async () => {
    setTotalsLoading(true);
    setTotalsError(null);
    try {
      const response = await getExpenseTotals(TOTAL_RANGE);
      setTotals(response);
    } catch (err) {
      setTotals(null);
      setTotalsError(err instanceof Error ? err.message : "Failed to load totals");
    } finally {
      setTotalsLoading(false);
    }
  }, []);

  const loadPayouts = useCallback(async () => {
    setPayoutLoading(true);
    setPayoutError(null);
    try {
      const payload = await listPayouts({
        limit: PAYOUT_PAGE_SIZE,
        offset: payoutOffset,
      });
      setPayoutItems(payload.items);
      setPayoutTotal(payload.total);
    } catch (err) {
      setPayoutItems([]);
      setPayoutTotal(0);
      setPayoutError(err instanceof Error ? err.message : "Failed to load payouts");
    } finally {
      setPayoutLoading(false);
    }
  }, [payoutOffset]);

  const loadPayoutTotals = useCallback(async () => {
    setPayoutTotalsLoading(true);
    setPayoutTotalsError(null);
    try {
      const response = await getPayoutTotals();
      setPayoutTotals(response);
    } catch (err) {
      setPayoutTotals(null);
      setPayoutTotalsError(err instanceof Error ? err.message : "Failed to load payout totals");
    } finally {
      setPayoutTotalsLoading(false);
    }
  }, []);

  const loadNetRanges = useCallback(async () => {
    setNetRangesLoading(true);
    setNetRangesError(null);
    try {
      const summaries = await Promise.all(
        NET_RANGE_OPTIONS.map(async (option) => {
          const dateRange =
            option.months !== null ? getTrailingDateRange(option.months) : option.key === "year_to_date" ? getYearToDateRange() : {};
          const [expenseTotal, payoutTotal] = await Promise.all([
            getExpenseTotals(TOTAL_RANGE, dateRange),
            getPayoutTotals(dateRange),
          ]);

          return {
            key: option.key,
            label: option.label,
            netAmount: payoutTotal.total_amount - expenseTotal.total_amount,
            expenseAmount: expenseTotal.total_amount,
            payoutAmount: payoutTotal.total_amount,
            expenseCount: expenseTotal.count,
            payoutCount: payoutTotal.count,
          };
        }),
      );
      setNetRanges(summaries);
    } catch (err) {
      setNetRanges([]);
      setNetRangesError(err instanceof Error ? err.message : "Failed to load net ranges");
    } finally {
      setNetRangesLoading(false);
    }
  }, []);

  const listAllExpensesByCategory = useCallback(async (expenseCategory: Extract<ExpenseCategory, "evaluation_fee" | "activation_fee">) => {
    const rows: ExpenseRecord[] = [];
    let nextOffset = 0;

    while (true) {
      const payload = await listExpenses({
        category: expenseCategory,
        limit: COMBINE_EXPENSE_PAGE_SIZE,
        offset: nextOffset,
      });

      rows.push(...payload.items);
      if (payload.items.length < COMBINE_EXPENSE_PAGE_SIZE) {
        break;
      }
      nextOffset += payload.items.length;
    }

    return rows;
  }, []);

  const listAllCombineRelevantExpenses = useCallback(async () => {
    const [evaluationExpenses, activationExpenses] = await Promise.all([
      listAllExpensesByCategory("evaluation_fee"),
      listAllExpensesByCategory("activation_fee"),
    ]);
    return [...evaluationExpenses, ...activationExpenses];
  }, [listAllExpensesByCategory]);

  const syncCombineTracker = useCallback(async () => {
    setCombineTrackerLoading(true);
    setCombineTrackerError(null);

    let nextSnapshot = readCombineSpendSnapshot();
    try {
      const combineRelevantExpenses = await listAllCombineRelevantExpenses();
      nextSnapshot = syncCombineSpendTrackerFromExpenses(combineRelevantExpenses);
      const latestSpreadsheetImportExpenseDate = getLatestSpreadsheetImportedTopstepExpenseDate(combineRelevantExpenses);
      const combinePurchaseExpenses = combineRelevantExpenses.filter(isTrackedCombinePurchaseExpense);
      const trackedCombineExpensesByAccountId = new Map<number, ExpenseRecord[]>();
      const expenseIdsToDelete = new Set<number>();

      for (const expense of combinePurchaseExpenses) {
        const accountId = expense.account_id;
        if (accountId === null) {
          if (isAutoTrackedCombineExpense(expense)) {
            expenseIdsToDelete.add(expense.id);
          }
          continue;
        }
        const rows = trackedCombineExpensesByAccountId.get(accountId);
        if (rows) {
          rows.push(expense);
        } else {
          trackedCombineExpensesByAccountId.set(accountId, [expense]);
        }
      }

      // Prefer a manually logged combine expense over any generated row for the same account.
      for (const rows of trackedCombineExpensesByAccountId.values()) {
        const manualRows = rows.filter((row) => !isAutoTrackedCombineExpense(row));
        const autoRows = rows.filter(isAutoTrackedCombineExpense);
        if (manualRows.length > 0) {
          for (const autoTrackedRow of autoRows) {
            expenseIdsToDelete.add(autoTrackedRow.id);
          }
          continue;
        }

        if (autoRows.length <= 1) {
          continue;
        }

        const sorted = [...autoRows].sort((left, right) => {
          const createdAtDiff = Date.parse(right.created_at) - Date.parse(left.created_at);
          if (createdAtDiff !== 0) {
            return createdAtDiff;
          }
          return right.id - left.id;
        });
        for (const duplicate of sorted.slice(1)) {
          expenseIdsToDelete.add(duplicate.id);
        }
      }

      try {
        const payload = await accountsApi.getAccounts({ showInactive: true, showMissing: false, bypassCache: true });
        const activeCombineAccounts = collectActiveCombineAccounts(payload);
        const activeCombineByAccountId = new Map<number, ActiveCombineAccount>();
        for (const account of activeCombineAccounts) {
          activeCombineByAccountId.set(account.accountId, account);
        }

        const syncResult = syncCombineSpendTracker(payload);
        nextSnapshot = syncResult.snapshot;
        const syncedAccountIds: number[] = [];
        const unsyncedByAccountId = new Map(
          syncResult.unsyncedEvaluationPurchases.map((purchase) => [purchase.accountId, purchase]),
        );
        let failedCreateCount = 0;
        let failedDeleteCount = 0;
        let didMutateExpenses = false;

        for (const expenseId of expenseIdsToDelete) {
          try {
            await deleteExpense(expenseId);
            didMutateExpenses = true;
          } catch {
            failedDeleteCount += 1;
          }
        }

        const existingActiveAccountIds = new Set<number>();
        for (const [accountId, rows] of trackedCombineExpensesByAccountId.entries()) {
          if (!activeCombineByAccountId.has(accountId)) {
            continue;
          }
          const rowsNotDeleted = rows.filter((row) => !expenseIdsToDelete.has(row.id));
          if (rowsNotDeleted.length > 0) {
            existingActiveAccountIds.add(accountId);
            syncedAccountIds.push(accountId);
          }
        }

        for (const activeCombine of activeCombineAccounts) {
          if (existingActiveAccountIds.has(activeCombine.accountId)) {
            continue;
          }
          const unsyncedPurchase = unsyncedByAccountId.get(activeCombine.accountId);
          if (
            latestSpreadsheetImportExpenseDate !== null &&
            (unsyncedPurchase === undefined || unsyncedPurchase.purchasedOn <= latestSpreadsheetImportExpenseDate)
          ) {
            if (unsyncedPurchase !== undefined) {
              syncedAccountIds.push(activeCombine.accountId);
            }
            continue;
          }
          const amountCents = unsyncedPurchase?.amountCents ?? activeCombine.amountCents;
          const purchasedOn = unsyncedPurchase?.purchasedOn ?? getTodayLocalIsoDate();
          try {
            await createExpense({
              expense_date: purchasedOn,
              amount_cents: amountCents,
              category: "evaluation_fee",
              plan_size: activeCombine.planSize,
              account_id: activeCombine.accountId,
              account_type: activeCombine.isDailyLossLimit ? "no_activation" : "standard",
              description: `Auto tracked combine purchase (${activeCombine.planSize.toUpperCase()}${
                activeCombine.isDailyLossLimit ? " DLL" : ""
              })`,
              tags: activeCombine.isDailyLossLimit ? ["combine_tracker", "auto", "dll"] : ["combine_tracker", "auto"],
            });
            syncedAccountIds.push(activeCombine.accountId);
            didMutateExpenses = true;
          } catch (err) {
            if (isApiError(err) && err.status === 409 && err.detail === "duplicate_expense") {
              syncedAccountIds.push(activeCombine.accountId);
            } else {
              failedCreateCount += 1;
            }
          }
        }

        if (syncedAccountIds.length > 0) {
          nextSnapshot = markEvaluationExpensesSynced(syncedAccountIds);
        }

        if (didMutateExpenses) {
          await Promise.all([loadExpenses(), loadTotals(), loadNetRanges()]);
        }

        const failedCount = failedCreateCount + failedDeleteCount;
        if (failedCount > 0) {
          setCombineTrackerError(
            `Failed to update ${failedCount} combine expense record${failedCount === 1 ? "" : "s"}.`,
          );
        }
      } catch (err) {
        setCombineTrackerError(err instanceof Error ? err.message : "Failed to sync combine spend tracker");
      }
    } catch (err) {
      setCombineTrackerError(err instanceof Error ? err.message : "Failed to sync combine spend tracker");
    } finally {
      setCombineSpendSnapshot(nextSnapshot);
      setCombineTrackerLoading(false);
    }
  }, [listAllCombineRelevantExpenses, loadExpenses, loadNetRanges, loadTotals]);

  useEffect(() => {
    void loadExpenses();
  }, [loadExpenses]);

  useEffect(() => {
    void loadTotals();
  }, [loadTotals]);

  useEffect(() => {
    void loadPayouts();
  }, [loadPayouts]);

  useEffect(() => {
    void loadPayoutTotals();
  }, [loadPayoutTotals]);

  useEffect(() => {
    void loadNetRanges();
  }, [loadNetRanges]);

  useEffect(() => {
    if (didInitialCombineSyncRef.current) {
      return;
    }
    didInitialCombineSyncRef.current = true;
    void syncCombineTracker();
  }, [syncCombineTracker]);

  useEffect(() => {
    setOffset(0);
  }, [startDate, endDate, category, limit]);

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
  const payoutTotalPages = Math.max(1, Math.ceil(payoutTotal / PAYOUT_PAGE_SIZE));
  const payoutCurrentPage = Math.floor(payoutOffset / PAYOUT_PAGE_SIZE) + 1;
  const practiceBlocked = addState.accountType === "practice";
  const recordedSpendAmount = totals?.total_amount ?? 0;
  const netPayoutTotalAmount = payoutTotals?.total_amount ?? 0;
  const netProfitAmount = netPayoutTotalAmount - recordedSpendAmount;
  const netProfitLoading = totalsLoading || payoutTotalsLoading;
  const netProfitTitleClassName = getNetProfitTitleClassName(netProfitAmount, netProfitLoading);
  const netProfitPositionLabel = getNetProfitPositionLabel(netProfitAmount);

  function resetAddForm() {
    setAddState(buildInitialAddExpenseState(""));
    setAddError(null);
  }

  function resetAddPayoutForm() {
    setAddPayoutState(buildInitialAddPayoutState());
    setAddPayoutError(null);
  }

  function handleOpenAdd() {
    resetAddForm();
    setAddOpen(true);
  }

  function handleOpenAddPayout() {
    resetAddPayoutForm();
    setAddPayoutOpen(true);
  }

  async function handleDeleteExpense(expense: ExpenseRecord) {
    const confirmed = window.confirm(`Delete expense #${expense.id} for ${currencyFormatter.format(expense.amount)}?`);
    if (!confirmed) {
      return;
    }

    try {
      await deleteExpense(expense.id);
      if (isAutoTrackedCombineExpense(expense) && expense.account_id !== null) {
        setCombineSpendSnapshot(suppressEvaluationExpenseSync([expense.account_id]));
      }
      await Promise.all([loadExpenses(), loadTotals(), loadNetRanges()]);
      if (
        !isAutoTrackedCombineExpense(expense) &&
        (expense.category === "evaluation_fee" || expense.category === "activation_fee")
      ) {
        await syncCombineTracker();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete expense");
    }
  }

  async function handleDeletePayout(payout: PayoutRecord) {
    const confirmed = window.confirm(`Delete payout #${payout.id} for ${currencyFormatter.format(payout.amount)}?`);
    if (!confirmed) {
      return;
    }

    try {
      await deletePayout(payout.id);
      await Promise.all([loadPayouts(), loadPayoutTotals(), loadNetRanges()]);
    } catch (err) {
      setPayoutError(err instanceof Error ? err.message : "Failed to delete payout");
    }
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
        account_type: addState.accountType === "no_activation_dll" ? "no_activation" : addState.accountType,
        plan_size: addState.planSize,
        account_id: parsedModalAccountId,
        description: addState.description.trim() || undefined,
        tags:
          addState.accountType === "no_activation_dll"
            ? Array.from(new Set([...splitTags(addState.tags), "dll"]))
            : splitTags(addState.tags),
        is_practice: addState.accountType === "practice",
      });

      setAddOpen(false);
      await Promise.all([loadExpenses(), loadTotals(), loadNetRanges()]);
      if (addState.stage === "evaluation_fee" || addState.stage === "activation_fee") {
        await syncCombineTracker();
      }
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

  async function handleSubmitNewPayout(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAddPayoutError(null);

    if (!addPayoutState.payoutDate) {
      setAddPayoutError("Payout date is required.");
      return;
    }

    const amount = Number.parseFloat(addPayoutState.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      setAddPayoutError("Amount must be greater than 0.");
      return;
    }

    setAddingPayout(true);
    try {
      await createPayout({
        payout_date: addPayoutState.payoutDate,
        amount,
        notes: addPayoutState.notes.trim() || undefined,
      });

      setAddPayoutOpen(false);
      if (payoutOffset !== 0) {
        setPayoutOffset(0);
        await Promise.all([loadPayoutTotals(), loadNetRanges()]);
      } else {
        await Promise.all([loadPayouts(), loadPayoutTotals(), loadNetRanges()]);
      }
    } catch (err) {
      setAddPayoutError(err instanceof Error ? err.message : "Failed to create payout");
    } finally {
      setAddingPayout(false);
    }
  }

  return (
    <div className="space-y-6 pb-10">
      <section className="grid gap-3">
        <Card>
          <CardContent className="space-y-5">
            <div className="grid gap-5 lg:grid-cols-[minmax(0,0.75fr)_minmax(0,1.25fr)]">
              <div className="space-y-3">
                <div>
                  <CardDescription>Recorded spend</CardDescription>
                  <CardTitle className="text-2xl">
                    {totalsLoading ? "..." : totals ? currencyFormatter.format(totals.total_amount) : "$0.00"}
                  </CardTitle>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                  <p className="text-xs text-slate-400">
                    {totals ? `${totals.count} expense${totals.count === 1 ? "" : "s"}` : "No data"}
                  </p>
                  <Button variant="ghost" size="sm" onClick={() => void syncCombineTracker()} disabled={combineTrackerLoading}>
                    {combineTrackerLoading ? "Syncing..." : "Sync Combine Expenses"}
                  </Button>
                </div>
                {combineTrackerError ? <p className="text-xs text-rose-300">{combineTrackerError}</p> : null}
              </div>

              <div className="space-y-3 border-t border-slate-800/80 pt-5 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
                <div>
                  <CardDescription>Net after payouts</CardDescription>
                  <CardTitle className={netProfitTitleClassName}>
                    {netProfitLoading ? "..." : currencyFormatter.format(netProfitAmount)}
                  </CardTitle>
                </div>
                {totalsError || payoutTotalsError ? (
                  <p className="text-xs text-rose-300">{totalsError ?? payoutTotalsError}</p>
                ) : (
                  <p className="text-xs text-slate-400">
                    {netProfitLoading
                      ? "Calculating net..."
                      : `${netProfitPositionLabel}. ${currencyFormatter.format(netPayoutTotalAmount)} payouts - ${currencyFormatter.format(
                          recordedSpendAmount,
                        )} recorded spend.`}
                  </p>
                )}
              </div>
            </div>

            {netRangesError ? <p className="text-xs text-rose-300">{netRangesError}</p> : null}
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
              {NET_RANGE_OPTIONS.map((option) => {
                const summary = netRanges.find((item) => item.key === option.key);
                return (
                  <div key={option.key} className="rounded-md border border-slate-800/80 bg-slate-950/40 p-3">
                    <p className="text-xs uppercase tracking-wide text-slate-500">{option.label}</p>
                    <p
                      className={`mt-1 text-lg font-semibold ${
                        summary ? getNetProfitAmountClassName(summary.netAmount) : "text-slate-100"
                      }`}
                    >
                      {netRangesLoading || !summary ? "..." : currencyFormatter.format(summary.netAmount)}
                    </p>
                    <p className="mt-1 text-[11px] text-slate-500">
                      {netRangesLoading || !summary
                        ? "Loading..."
                        : `${currencyFormatter.format(summary.payoutAmount)} payouts - ${currencyFormatter.format(
                            summary.expenseAmount,
                          )} spend`}
                    </p>
                  </div>
                );
              })}
            </div>
            <p className="text-xs text-slate-500">
              Expenses counted: {totalsLoading ? "..." : (totals?.count ?? 0)}. Payouts counted:{" "}
              {payoutTotalsLoading ? "..." : (payoutTotals?.count ?? 0)}. Standard activations:{" "}
              {combineSpendSnapshot.standardActivationCount} (
              {currencyFormatter.format(combineSpendSnapshot.standardActivationCostCents / 100)}).
            </p>
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
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
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
            <Table className="min-w-[760px]">
              <TableHeader>
                <tr>
                  <TableHead>Date</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Tags</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </tr>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-slate-400">
                      Loading expenses...
                    </TableCell>
                  </TableRow>
                ) : error ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-rose-300">
                      {error}
                    </TableCell>
                  </TableRow>
                ) : items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-slate-400">
                      No expenses found.
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((expense) => (
                    <TableRow key={expense.id}>
                      <TableCell>{dateFormatter.format(new Date(`${expense.expense_date}T00:00:00.000Z`))}</TableCell>
                      <TableCell>{formatCategoryLabel(expense.category)}</TableCell>
                      <TableCell className="text-right font-mono">{currencyFormatter.format(expense.amount)}</TableCell>
                      <TableCell className="max-w-[240px] truncate" title={expense.description ?? undefined}>
                        {expense.description ?? "-"}
                      </TableCell>
                      <TableCell className="max-w-[220px] truncate" title={expense.tags.join(", ")}>
                        {expense.tags.length > 0 ? expense.tags.join(", ") : "-"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="danger" size="sm" onClick={() => void handleDeleteExpense(expense)}>
                          Delete
                        </Button>
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

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Payouts</CardTitle>
              <CardDescription>Log the final payouts you receive after the profit split.</CardDescription>
            </div>
            <Button onClick={handleOpenAddPayout}>Add Payout</Button>
          </div>
        </CardHeader>

        <CardContent>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-slate-800/80 bg-slate-950/40 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Total Payouts</p>
              <p className="mt-2 text-2xl font-semibold text-slate-100">
                {payoutTotalsLoading ? "..." : currencyFormatter.format(payoutTotals?.total_amount ?? 0)}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800/80 bg-slate-950/40 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Number of Payouts</p>
              <p className="mt-2 text-2xl font-semibold text-slate-100">
                {payoutTotalsLoading ? "..." : (payoutTotals?.count ?? 0).toLocaleString("en-US")}
              </p>
            </div>
            <div className="rounded-xl border border-slate-800/80 bg-slate-950/40 p-4">
              <p className="text-xs uppercase tracking-wide text-slate-500">Average Payout</p>
              <p className="mt-2 text-2xl font-semibold text-slate-100">
                {payoutTotalsLoading ? "..." : currencyFormatter.format(payoutTotals?.average_amount ?? 0)}
              </p>
            </div>
          </div>

          {payoutTotalsError ? <p className="mt-4 text-sm text-rose-300">{payoutTotalsError}</p> : null}

          <div className="mt-4 overflow-x-auto rounded-xl border border-slate-800/80">
            <Table className="min-w-[720px]">
              <TableHeader>
                <tr>
                  <TableHead>Date</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </tr>
              </TableHeader>
              <TableBody>
                {payoutLoading ? (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-slate-400">
                      Loading payouts...
                    </TableCell>
                  </TableRow>
                ) : payoutError ? (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-rose-300">
                      {payoutError}
                    </TableCell>
                  </TableRow>
                ) : payoutItems.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-slate-400">
                      No payouts found.
                    </TableCell>
                  </TableRow>
                ) : (
                  payoutItems.map((payout) => (
                    <TableRow key={payout.id}>
                      <TableCell>{dateFormatter.format(new Date(`${payout.payout_date}T00:00:00.000Z`))}</TableCell>
                      <TableCell className="text-right font-mono">{currencyFormatter.format(payout.amount)}</TableCell>
                      <TableCell className="max-w-[360px] truncate" title={payout.notes ?? undefined}>
                        {payout.notes ?? "-"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="danger" size="sm" onClick={() => void handleDeletePayout(payout)}>
                          Delete
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          <div className="mt-4 flex items-center justify-between gap-3">
            <p className="text-xs text-slate-400">
              Page {Math.min(payoutCurrentPage, payoutTotalPages)} of {payoutTotalPages} ({payoutTotal} total)
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={payoutOffset === 0 || payoutLoading}
                onClick={() => setPayoutOffset((current) => Math.max(0, current - PAYOUT_PAGE_SIZE))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={payoutOffset + PAYOUT_PAGE_SIZE >= payoutTotal || payoutLoading}
                onClick={() => setPayoutOffset((current) => current + PAYOUT_PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Drawer
        open={addPayoutOpen}
        onClose={() => setAddPayoutOpen(false)}
        title="Add Payout"
        description="Log the final payout amount you received after the profit split."
      >
        <form className="space-y-3" onSubmit={(event) => void handleSubmitNewPayout(event)}>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Payout Date</label>
            <Input
              type="date"
              value={addPayoutState.payoutDate}
              onChange={(event) =>
                setAddPayoutState((current) => ({
                  ...current,
                  payoutDate: event.target.value,
                }))
              }
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Amount (USD)</label>
            <Input
              value={addPayoutState.amount}
              onChange={(event) =>
                setAddPayoutState((current) => ({
                  ...current,
                  amount: event.target.value,
                }))
              }
              inputMode="decimal"
              placeholder="2500.00"
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Notes (Optional)</label>
            <Textarea
              value={addPayoutState.notes}
              onChange={(event) =>
                setAddPayoutState((current) => ({
                  ...current,
                  notes: event.target.value,
                }))
              }
              className="min-h-[120px]"
              placeholder="March payout after split"
            />
          </div>

          {addPayoutError ? <p className="text-sm text-rose-300">{addPayoutError}</p> : null}

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => setAddPayoutOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={addingPayout}>
              {addingPayout ? "Saving..." : "Save Payout"}
            </Button>
          </div>
        </form>
      </Drawer>

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
                  {getExpenseAccountTypeLabel(value)}
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
