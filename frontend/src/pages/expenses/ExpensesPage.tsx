import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";

import { DemoModeNotice } from "../../components/demo/DemoModeNotice";
import { useDemoInteractionPolicy } from "../../components/demo/useDemoInteractionPolicy";
import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { Drawer } from "../../components/ui/Drawer";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/Table";
import { Textarea } from "../../components/ui/Textarea";
import {
  accountsApi,
  clearFinancialReadCache,
  createPayout,
  createExpense,
  deletePayout,
  deleteExpense,
  getFinancialSummary,
  getCombineTrackerExpenseSuppressions,
  isApiError,
  listPayouts,
  listExpenses,
} from "../../lib/api";
import {
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
import type {
  ExpenseCategory,
  ExpenseMonthlySummary,
  ExpenseRecord,
  ExpenseTotals,
  PayoutMonthlySummary,
  PayoutRecord,
  PayoutTotals,
} from "../../lib/types";
import { isDemoModeEnabled } from "../../lib/demoMode";
import { useLatestRequestGuard } from "../../lib/latestRequest";
import { parseStrictFiniteNumber, parseStrictInteger } from "../../lib/strictNumber";
import { formatCurrency } from "../../utils/formatters";
import { ExpenseCalendarCard } from "./ExpenseCalendarCard";
import "./ExpensesPage.css";
import { loadFreshAccountsForExpenseReconciliation } from "./expenseAccountLoading";
import { buildNetRangeOptions, formatLocalIsoDate, type NetRangeOption } from "./expenseNetRanges";
import {
  decideExpenseReconciliation,
  reconcileCombineExpenses as runCombineExpenseReconciliation,
} from "./expenseReconciliation";

const CATEGORY_OPTIONS: ExpenseCategory[] = ["evaluation_fee", "activation_fee", "reset_fee", "data_fee", "other", "refund"];
const COMBINE_EXPENSE_PAGE_SIZE = 200;
const PAYOUT_PAGE_SIZE = 50;

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});
const monthFormatter = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

function formatRecordDate(isoDate: string) {
  return dateFormatter.format(new Date(`${isoDate}T00:00:00.000Z`));
}

function formatExpenseCount(count: number) {
  return `${count.toLocaleString("en-US")} expense${count === 1 ? "" : "s"}`;
}

function formatCategoryLabel(category: string) {
  return category
    .split("_")
    .map((value) => `${value.charAt(0).toUpperCase()}${value.slice(1)}`)
    .join(" ");
}

function getTodayLocalIsoDate() {
  return formatLocalIsoDate(new Date());
}

function parsePositiveInt(value: string) {
  if (!value.trim()) {
    return undefined;
  }
  const parsed = parseStrictInteger(value);
  if (parsed === null || parsed <= 0) {
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
    return "text-2xl text-app-positive-text";
  }
  if (amount < 0) {
    return "text-2xl text-app-negative-text";
  }
  return "text-2xl";
}

function getNetProfitAmountClassName(amount: number) {
  if (amount > 0) {
    return "text-app-positive-text";
  }
  if (amount < 0) {
    return "text-app-negative-text";
  }
  return "text-app-text";
}

function getNetProfitPositionLabel(amount: number) {
  if (amount > 0) {
    return `Positive by ${formatCurrency(amount)}`;
  }
  if (amount < 0) {
    return `Negative by ${formatCurrency(Math.abs(amount))}`;
  }
  return "Break-even";
}

function isAutoTrackedCombineExpense(expense: ExpenseRecord): boolean {
  return expense.category === "evaluation_fee" && expense.tags.includes("combine_tracker");
}

function formatCombineReconciliationError(error: unknown): string {
  if (isApiError(error) && error.detail && typeof error.detail === "object") {
    const detail = error.detail as { code?: unknown; message?: unknown };
    if (
      typeof detail.code === "string" &&
      detail.code.startsWith("projectx_") &&
      typeof detail.message === "string" &&
      detail.message.trim().length > 0
    ) {
      return `${detail.message.trim()} No expenses or local combine-tracker data were changed.`;
    }
  }
  return error instanceof Error ? error.message : "Failed to sync combine spend tracker";
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

interface SpendSinceLastPayoutSummary {
  lastPayoutDate: string | null;
  totalAmount: number;
  totalAmountCents: number;
  expenseCount: number;
}

function buildInitialAddPayoutState(): AddPayoutState {
  return {
    payoutDate: getTodayLocalIsoDate(),
    amount: "",
    notes: "",
  };
}

export function ExpensesPage() {
  const { demoModeEnabled, demoDisabledTitle } = useDemoInteractionPolicy();
  const [ledgerView, setLedgerView] = useState<"expenses" | "payouts">("expenses");
  const ledgerTabsRef = useRef<HTMLDivElement>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [category, setCategory] = useState<ExpenseCategory | "">("");

  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  const [items, setItems] = useState<ExpenseRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const expensePaginationRef = useRef<HTMLElement>(null);
  const expensePaginationTopRef = useRef<number | null>(null);
  const expenseTableScrollRef = useRef<HTMLDivElement>(null);
  const payoutTableScrollRef = useRef<HTMLDivElement>(null);

  const handleExpensePageChange = (nextOffset: number) => {
    expensePaginationTopRef.current = expensePaginationRef.current?.getBoundingClientRect().top ?? null;
    setOffset(nextOffset);
  };

  useLayoutEffect(() => {
    if (loading) {
      return;
    }
    if (expenseTableScrollRef.current) expenseTableScrollRef.current.scrollTop = 0;
    if (expensePaginationTopRef.current === null) return;
    const previousTop = expensePaginationTopRef.current;
    expensePaginationTopRef.current = null;
    const pagination = expensePaginationRef.current;
    if (pagination && !pagination.closest("[hidden]")) {
      // A shorter page must not leave the viewport down in the payouts table.
      window.scrollBy({ top: pagination.getBoundingClientRect().top - previousTop, behavior: "instant" });
    }
  }, [items, loading]);

  const [totals, setTotals] = useState<ExpenseTotals | null>(null);
  const [totalsLoading, setTotalsLoading] = useState(true);
  const [totalsError, setTotalsError] = useState<string | null>(null);
  const [expenseMonths, setExpenseMonths] = useState<ExpenseMonthlySummary[]>([]);
  const [payoutMonths, setPayoutMonths] = useState<PayoutMonthlySummary[]>([]);
  const [financialAsOfDate, setFinancialAsOfDate] = useState(getTodayLocalIsoDate);
  const [selectedCalendarMonth, setSelectedCalendarMonth] = useState<string | null>(null);
  const [combineSpendSnapshot, setCombineSpendSnapshot] = useState(readCombineSpendSnapshot);
  const [combineTrackerLoading, setCombineTrackerLoading] = useState(false);
  const [combineTrackerError, setCombineTrackerError] = useState<string | null>(null);
  const [combineTrackerNotice, setCombineTrackerNotice] = useState<string | null>(null);

  const [addOpen, setAddOpen] = useState(false);
  const [addState, setAddState] = useState<AddExpenseState>(buildInitialAddExpenseState(""));
  const [addError, setAddError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [payoutItems, setPayoutItems] = useState<PayoutRecord[]>([]);
  const [payoutTotal, setPayoutTotal] = useState(0);
  const [payoutOffset, setPayoutOffset] = useState(0);
  const [payoutLoading, setPayoutLoading] = useState(true);
  const [payoutError, setPayoutError] = useState<string | null>(null);
  const [payoutTotals, setPayoutTotals] = useState<PayoutTotals | null>(null);
  const [payoutTotalsLoading, setPayoutTotalsLoading] = useState(true);
  const [payoutTotalsError, setPayoutTotalsError] = useState<string | null>(null);
  const [spendSinceLastPayout, setSpendSinceLastPayout] = useState<SpendSinceLastPayoutSummary | null>(null);
  const [spendSinceLastPayoutLoading, setSpendSinceLastPayoutLoading] = useState(true);
  const [spendSinceLastPayoutError, setSpendSinceLastPayoutError] = useState<string | null>(null);
  const [netRangeOptions, setNetRangeOptions] = useState<NetRangeOption[]>(() => buildNetRangeOptions(null));
  const [netRanges, setNetRanges] = useState<NetRangeSummary[]>([]);
  const [netRangesLoading, setNetRangesLoading] = useState(true);
  const [netRangesError, setNetRangesError] = useState<string | null>(null);
  const [addPayoutOpen, setAddPayoutOpen] = useState(false);
  const [addPayoutState, setAddPayoutState] = useState<AddPayoutState>(buildInitialAddPayoutState());
  const [addPayoutError, setAddPayoutError] = useState<string | null>(null);
  const [addingPayout, setAddingPayout] = useState(false);
  const [dataRevision, setDataRevision] = useState(0);
  const beginExpensesRequest = useLatestRequestGuard();
  const beginPayoutsRequest = useLatestRequestGuard();
  const beginFinancialSummaryRequest = useLatestRequestGuard();

  useLayoutEffect(() => {
    if (!payoutLoading && payoutTableScrollRef.current) payoutTableScrollRef.current.scrollTop = 0;
  }, [payoutItems, payoutLoading]);

  const refreshFinancialData = useCallback(() => {
    setDataRevision((current) => current + 1);
  }, []);

  const loadExpenses = useCallback(async () => {
    const isCurrent = beginExpensesRequest();
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
      if (isCurrent()) {
        setItems(payload.items);
        setTotal(payload.total);
      }
    } catch (err) {
      if (!isCurrent()) {
        return;
      }
      setItems([]);
      setTotal(0);
      setError(err instanceof Error ? err.message : "Failed to load expenses");
    } finally {
      if (isCurrent()) {
        setLoading(false);
      }
    }
  }, [beginExpensesRequest, category, endDate, limit, offset, startDate]);

  const selectedPayoutStartDate = selectedCalendarMonth ? startDate : "";
  const selectedPayoutEndDate = selectedCalendarMonth ? endDate : "";

  const loadPayouts = useCallback(async () => {
    const isCurrent = beginPayoutsRequest();
    setPayoutLoading(true);
    setPayoutError(null);
    try {
      const payload = await listPayouts({
        start_date: selectedPayoutStartDate || undefined,
        end_date: selectedPayoutEndDate || undefined,
        limit: PAYOUT_PAGE_SIZE,
        offset: payoutOffset,
      });
      if (isCurrent()) {
        setPayoutItems(payload.items);
        setPayoutTotal(payload.total);
      }
    } catch (err) {
      if (!isCurrent()) {
        return;
      }
      setPayoutItems([]);
      setPayoutTotal(0);
      setPayoutError(err instanceof Error ? err.message : "Failed to load payouts");
    } finally {
      if (isCurrent()) {
        setPayoutLoading(false);
      }
    }
  }, [beginPayoutsRequest, payoutOffset, selectedPayoutEndDate, selectedPayoutStartDate]);

  const loadFinancialSummary = useCallback(async (signal?: AbortSignal) => {
    const isCurrent = beginFinancialSummaryRequest();
    setTotalsLoading(true);
    setPayoutTotalsLoading(true);
    setSpendSinceLastPayoutLoading(true);
    setNetRangesLoading(true);
    setTotalsError(null);
    setPayoutTotalsError(null);
    setSpendSinceLastPayoutError(null);
    setNetRangesError(null);

    try {
      const response = await getFinancialSummary(
        { asOfDate: getTodayLocalIsoDate() },
        { signal },
      );
      if (signal?.aborted || !isCurrent()) {
        return;
      }

      setTotals(response.expense_totals);
      setExpenseMonths(response.expense_months ?? []);
      setPayoutMonths(response.payout_months ?? []);
      setFinancialAsOfDate(response.as_of_date);
      setPayoutTotals(response.payout_totals);
      setSpendSinceLastPayout({
        lastPayoutDate: response.spend_since_last_payout.last_payout_date,
        totalAmount: response.spend_since_last_payout.total_amount,
        totalAmountCents: response.spend_since_last_payout.total_amount_cents,
        expenseCount: response.spend_since_last_payout.expense_count,
      });
      setNetRangeOptions(response.ranges.map((range) => ({
        key: range.key,
        label: range.label,
        dateRange: {
          ...(range.start_date ? { startDate: range.start_date } : {}),
          ...(range.end_date ? { endDate: range.end_date } : {}),
        },
      })));
      setNetRanges(response.ranges.map((range) => ({
        key: range.key,
        label: range.label,
        netAmount: range.payout_totals.total_amount - range.expense_totals.total_amount,
        expenseAmount: range.expense_totals.total_amount,
        payoutAmount: range.payout_totals.total_amount,
        expenseCount: range.expense_totals.count,
        payoutCount: range.payout_totals.count,
      })));
    } catch (err) {
      if (signal?.aborted || !isCurrent()) {
        return;
      }
      const message = err instanceof Error ? err.message : "Failed to load financial summary";
      setTotals(null);
      setExpenseMonths([]);
      setPayoutMonths([]);
      setPayoutTotals(null);
      setSpendSinceLastPayout(null);
      setNetRanges([]);
      setTotalsError(message);
      setPayoutTotalsError(message);
      setSpendSinceLastPayoutError(message);
      setNetRangesError(message);
    } finally {
      if (!signal?.aborted && isCurrent()) {
        setTotalsLoading(false);
        setPayoutTotalsLoading(false);
        setSpendSinceLastPayoutLoading(false);
        setNetRangesLoading(false);
      }
    }
  }, [beginFinancialSummaryRequest]);

  const listAllExpensesByCategory = useCallback(async (expenseCategory: Extract<ExpenseCategory, "evaluation_fee" | "activation_fee">) => {
    const rows: ExpenseRecord[] = [];
    let nextOffset = 0;

    while (true) {
      const payload = await listExpenses({
        category: expenseCategory,
        limit: COMBINE_EXPENSE_PAGE_SIZE,
        offset: nextOffset,
      }, { bypassCache: true });

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
    if (isDemoModeEnabled()) {
      setCombineTrackerError("Demo mode is read-only. Turn it off before reconciling combine expenses.");
      return;
    }

    setCombineTrackerLoading(true);
    setCombineTrackerError(null);
    setCombineTrackerNotice(null);
    try {
      const result = await runCombineExpenseReconciliation({
        loadAccounts: () => loadFreshAccountsForExpenseReconciliation(accountsApi.getAccounts),
        loadExpenses: listAllCombineRelevantExpenses,
        loadSuppressedAccountIds: async () =>
          (await getCombineTrackerExpenseSuppressions()).account_ids,
        createExpense,
        deleteExpense: (expenseId) =>
          deleteExpense(expenseId, { suppressAutoRecreation: false }),
        isDuplicateCreateError: (error) =>
          isApiError(error) && error.status === 409 && error.detail === "duplicate_expense",
        syncTrackerFromExpenses: syncCombineSpendTrackerFromExpenses,
        syncTrackerFromAccounts: syncCombineSpendTracker,
        markEvaluationExpensesSynced,
        suppressEvaluationExpenseSync,
      });

      setCombineSpendSnapshot(result.snapshot);
      if (result.didMutateExpenses) {
        refreshFinancialData();
      }

      const failedCount = result.failedCreateCount + result.failedDeleteCount;
      if (failedCount > 0) {
        setCombineTrackerError(
          `Failed to update ${failedCount} combine expense record${failedCount === 1 ? "" : "s"}. Retry reconciliation to finish safely.`,
        );
      }

      if (failedCount === 0) {
        if (result.eligibleCombineCount === 0 && result.createdCount === 0 && result.deletedCount === 0) {
          setCombineTrackerNotice(
            "ProjectX refreshed successfully, but no ACTIVE or LOCKED_OUT 50KTC, 100KTC, or 150KTC combine accounts were found. No changes were made.",
          );
        } else if (result.createdCount === 0 && result.deletedCount === 0) {
          setCombineTrackerNotice("Combine expenses are already reconciled. No changes were needed.");
        } else {
          const changes: string[] = [];
          if (result.createdCount > 0) {
            changes.push(`created ${result.createdCount} missing row${result.createdCount === 1 ? "" : "s"}`);
          }
          if (result.deletedCount > 0) {
            changes.push(`removed ${result.deletedCount} duplicate auto row${result.deletedCount === 1 ? "" : "s"}`);
          }
          setCombineTrackerNotice(`Reconciled combine expenses: ${changes.join(" and ")}.`);
        }
      }
    } catch (err) {
      setCombineTrackerError(formatCombineReconciliationError(err));
    } finally {
      setCombineTrackerLoading(false);
    }
  }, [listAllCombineRelevantExpenses, refreshFinancialData]);

  const handleReconcileCombineExpenses = useCallback(() => {
    const confirmed = demoModeEnabled
      ? false
      : window.confirm(
          "Reconcile combine expenses now? This refreshes ProjectX, creates missing auto-tracked evaluation fees, and removes duplicate auto-tracked rows.",
        );
    const decision = decideExpenseReconciliation(demoModeEnabled, confirmed);
    if (!decision.allowed && decision.reason === "demo_mode") {
      setCombineTrackerError("Demo mode is read-only. Turn it off before reconciling combine expenses.");
      return;
    }
    if (decision.allowed) {
      void syncCombineTracker();
    }
  }, [demoModeEnabled, syncCombineTracker]);

  useEffect(() => {
    void dataRevision;
    void loadExpenses();
  }, [dataRevision, loadExpenses]);

  useEffect(() => {
    void dataRevision;
    void loadPayouts();
  }, [dataRevision, loadPayouts]);

  useEffect(() => {
    void dataRevision;
    const controller = new AbortController();
    void loadFinancialSummary(controller.signal);
    return () => controller.abort();
  }, [dataRevision, loadFinancialSummary]);

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
    if (demoModeEnabled) {
      return;
    }
    resetAddForm();
    setAddOpen(true);
  }

  function handleOpenAddPayout() {
    if (demoModeEnabled) {
      return;
    }
    resetAddPayoutForm();
    setAddPayoutOpen(true);
  }

  async function handleDeleteExpense(expense: ExpenseRecord) {
    if (demoModeEnabled) {
      return;
    }
    const confirmed = window.confirm(`Delete expense #${expense.id} for ${formatCurrency(expense.amount)}?`);
    if (!confirmed) {
      return;
    }

    try {
      await deleteExpense(expense.id);
      if (isAutoTrackedCombineExpense(expense) && expense.account_id !== null) {
        setCombineSpendSnapshot(suppressEvaluationExpenseSync([expense.account_id]));
      }
      refreshFinancialData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete expense");
    }
  }

  async function handleDeletePayout(payout: PayoutRecord) {
    if (demoModeEnabled) {
      return;
    }
    const confirmed = window.confirm(`Delete payout #${payout.id} for ${formatCurrency(payout.amount)}?`);
    if (!confirmed) {
      return;
    }

    try {
      await deletePayout(payout.id);
      refreshFinancialData();
    } catch (err) {
      setPayoutError(err instanceof Error ? err.message : "Failed to delete payout");
    }
  }

  async function handleSubmitNewExpense(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAddError(null);

    if (demoModeEnabled) {
      setAddError("Demo Mode uses a read-only financial snapshot.");
      return;
    }

    if (practiceBlocked) {
      setAddError("Practice accounts are free. Expenses are disabled.");
      return;
    }

    const parsedModalAccountId = parsePositiveInt(addState.accountId);
    if (parsedModalAccountId === null) {
      setAddError("Account ID must be a positive integer.");
      return;
    }

    const amount = parseStrictFiniteNumber(addState.amount);
    if (amount === null || amount < 0) {
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
      setLedgerView("expenses");
      setOffset(0);
      refreshFinancialData();
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

    if (demoModeEnabled) {
      setAddPayoutError("Demo Mode uses a read-only financial snapshot.");
      return;
    }

    if (!addPayoutState.payoutDate) {
      setAddPayoutError("Payout date is required.");
      return;
    }

    const amount = parseStrictFiniteNumber(addPayoutState.amount);
    if (amount === null || amount <= 0) {
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
      setLedgerView("payouts");
      refreshFinancialData();
      if (payoutOffset !== 0) {
        setPayoutOffset(0);
      }
    } catch (err) {
      setAddPayoutError(err instanceof Error ? err.message : "Failed to create payout");
    } finally {
      setAddingPayout(false);
    }
  }

  function handleCalendarMonthSelect(month: string | null) {
    if (month === null) {
      setSelectedCalendarMonth(null);
      setStartDate("");
      setEndDate("");
      setPayoutOffset(0);
      return;
    }

    const [yearValue, monthValue] = month.split("-");
    const year = Number.parseInt(yearValue ?? "", 10);
    const monthNumber = Number.parseInt(monthValue ?? "", 10);
    if (!Number.isInteger(year) || !Number.isInteger(monthNumber) || monthNumber < 1 || monthNumber > 12) {
      return;
    }

    const lastDay = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
    setSelectedCalendarMonth(month);
    setStartDate(`${month}-01`);
    setEndDate(`${month}-${String(lastDay).padStart(2, "0")}`);
    setPayoutOffset(0);
  }

  function handleManualStartDateChange(value: string) {
    setSelectedCalendarMonth(null);
    setPayoutOffset(0);
    setStartDate(value);
  }

  function handleManualEndDateChange(value: string) {
    setSelectedCalendarMonth(null);
    setPayoutOffset(0);
    setEndDate(value);
  }

  return (
    <div className="expenses-workspace space-y-5 pb-10">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-app-accent">Financial overview</p>
          <h1 className="text-2xl font-semibold tracking-tight text-app-text sm:text-[28px]">Expenses and Payouts</h1>
          <p className="mt-1 text-sm text-app-muted">Your trading costs, payouts, and net cash flow.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" onClick={handleOpenAddPayout} disabled={demoModeEnabled} title={demoModeEnabled ? demoDisabledTitle : undefined}>Add Payout</Button>
          <Button onClick={handleOpenAdd} disabled={demoModeEnabled} title={demoModeEnabled ? demoDisabledTitle : undefined}><span aria-hidden="true">+</span> Add Expense</Button>
        </div>
      </header>
      <DemoModeNotice>
        <p>Expenses, payouts, and net ranges are a fixed sample ledger. Filtering and pagination are available; adding, deleting, and combine reconciliation are disabled.</p>
      </DemoModeNotice>

      <section aria-label="Financial overview" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
          <h2 className="font-medium text-app-text-soft">At a glance</h2>
          <span>All recorded activity · USD</span>
        </div>
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <Card className="expenses-stat expenses-net-stat">
            <CardDescription>Net after payouts</CardDescription>
            <p className={`expenses-stat-value ${netProfitTitleClassName}`}>
              {netProfitLoading ? "..." : totalsError || payoutTotalsError ? "Unavailable" : formatCurrency(netProfitAmount)}
            </p>
            <p className="text-xs leading-5 text-app-muted">
              {netProfitLoading ? "Calculating net..." : totalsError || payoutTotalsError ? "Summary could not be loaded." : `${netProfitPositionLabel}.`}
            </p>
          </Card>
          <Card className="expenses-stat">
            <div>
              <CardDescription>Recorded spend</CardDescription>
              <p className="expenses-stat-value">{totalsLoading ? "..." : totalsError ? "Unavailable" : formatCurrency(totals?.total_amount ?? 0)}</p>
            </div>
            <p className="text-xs leading-5 text-app-muted">{totalsLoading ? "Loading recorded spend..." : totals ? `${totals.count} expense${totals.count === 1 ? "" : "s"}` : "No data"}</p>
          </Card>
          <Card className="expenses-stat">
            <CardDescription>Total payouts</CardDescription>
            <p className="expenses-stat-value">{payoutTotalsLoading ? "..." : payoutTotalsError ? "Unavailable" : formatCurrency(payoutTotals?.total_amount ?? 0)}</p>
            <p className="text-xs leading-5 text-app-muted">{payoutTotalsLoading ? "Loading payout summary..." : `${(payoutTotals?.count ?? 0).toLocaleString("en-US")} payouts received`}</p>
          </Card>
          <Card className="expenses-stat">
            <CardDescription>Spend since last payout</CardDescription>
            <p className="expenses-stat-value">{spendSinceLastPayoutError ? "Unavailable" : spendSinceLastPayoutLoading || !spendSinceLastPayout ? "..." : formatCurrency(spendSinceLastPayout.totalAmount)}</p>
            {spendSinceLastPayoutError ? <p className="text-xs text-app-negative-text" role="alert">{spendSinceLastPayoutError}</p> : <p className="text-xs leading-5 text-app-muted">
              {spendSinceLastPayoutLoading || !spendSinceLastPayout ? "Calculating spend..." : spendSinceLastPayout.lastPayoutDate
                ? `${formatExpenseCount(spendSinceLastPayout.expenseCount)} from ${formatRecordDate(spendSinceLastPayout.lastPayoutDate)} forward.`
                : `No payouts recorded; showing all recorded spend (${formatExpenseCount(spendSinceLastPayout.expenseCount)}).`}
            </p>}
          </Card>
        </div>
        {totalsError || payoutTotalsError ? <p className="text-sm text-app-negative-text" role="alert">{totalsError ?? payoutTotalsError}</p> : null}
      </section>

      <div className="grid items-stretch gap-5 xl:grid-cols-[minmax(0,1.8fr)_minmax(320px,1fr)]">
        <ExpenseCalendarCard
          months={expenseMonths}
          payoutMonths={payoutMonths}
          loading={totalsLoading || payoutTotalsLoading}
          error={totalsError ?? payoutTotalsError}
          asOfDate={financialAsOfDate}
          selectedMonth={selectedCalendarMonth}
          onMonthSelect={handleCalendarMonthSelect}
        />
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Net by period</CardTitle>
            <CardDescription>Payouts less recorded spend · as of {formatRecordDate(financialAsOfDate)}</CardDescription>
          </CardHeader>
          {netRangesError ? <p className="text-xs text-app-negative-text" role="alert">{netRangesError}</p> : null}
          <div className="expenses-periods divide-y divide-app-border/60">
            {netRangeOptions.map((option) => {
              const summary = netRanges.find((item) => item.key === option.key);
              return (
                <div key={option.key} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-app-text-soft">{option.label}</p>
                    <p className="mt-1 text-[10px] leading-4 text-app-muted">{netRangesLoading || !summary ? "Loading..." : `${formatCurrency(summary.payoutAmount)} payouts - ${formatCurrency(summary.expenseAmount)} spend`}</p>
                  </div>
                  <p className={`shrink-0 text-sm font-semibold tabular-nums ${summary ? getNetProfitAmountClassName(summary.netAmount) : "text-app-text"}`}>
                    {netRangesError ? "—" : netRangesLoading || !summary ? "..." : formatCurrency(summary.netAmount)}
                  </p>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      <section className="space-y-3" aria-label="Transaction history">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-app-text">Transaction history</h2>
            <p className="mt-1 text-xs text-app-muted">Browse your records. Overview totals stay independent of ledger filters.</p>
          </div>
          <Button variant="ghost" size="sm" disabled={loading || payoutLoading || totalsLoading} onClick={() => {
            clearFinancialReadCache();
            refreshFinancialData();
          }}>Refresh</Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-app-border">
          <div ref={ledgerTabsRef} role="tablist" aria-label="Transaction type" className="flex gap-5">
            {(["expenses", "payouts"] as const).map((view) => <button
              key={view}
              type="button"
              role="tab"
              id={`${view}-tab`}
              aria-selected={ledgerView === view}
              aria-controls={`${view}-panel`}
              tabIndex={ledgerView === view ? 0 : -1}
              onClick={() => setLedgerView(view)}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const nextView = event.key === "Home" ? "expenses" : event.key === "End" ? "payouts" : view === "expenses" ? "payouts" : "expenses";
                setLedgerView(nextView);
                ledgerTabsRef.current?.querySelector<HTMLButtonElement>(`#${nextView}-tab`)?.focus();
              }}
              className={`inline-flex min-h-11 items-center gap-2 border-b-2 px-1 py-3 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent ${ledgerView === view ? "border-app-accent text-app-accent" : "border-transparent text-app-muted hover:text-app-text"}`}
            >
              {view === "expenses" ? "Expenses" : "Payouts"}
              <span className="rounded-md bg-app-raised/65 px-1.5 py-0.5 text-[10px] tabular-nums">{view === "expenses" ? loading ? "…" : total.toLocaleString("en-US") : payoutLoading ? "…" : payoutTotal.toLocaleString("en-US")}</span>
            </button>)}
          </div>
          {selectedCalendarMonth ? <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-app-muted">
            <span>Both ledgers: {monthFormatter.format(new Date(`${selectedCalendarMonth}-01T00:00:00.000Z`))}</span>
            <Button variant="ghost" size="sm" onClick={() => handleCalendarMonthSelect(null)}>Clear month</Button>
          </div> : null}
        </div>
      <Card id="expenses-panel" role="tabpanel" aria-labelledby="expenses-tab" tabIndex={0} hidden={ledgerView !== "expenses"} className="expenses-ledger">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="sr-only">Expenses</CardTitle>
              <CardDescription>Track paid account fees and operational costs. These filters apply to expenses.</CardDescription>
            </div>
          </div>
        </CardHeader>

        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1.2fr_100px]">
            <div>
              <label htmlFor="expenses-start-date" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Start Date</label>
              <Input
                id="expenses-start-date"
                type="date"
                value={startDate}
                onChange={(event) => handleManualStartDateChange(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="expenses-end-date" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">End Date</label>
              <Input
                id="expenses-end-date"
                type="date"
                value={endDate}
                onChange={(event) => handleManualEndDateChange(event.target.value)}
              />
            </div>
            <div>
              <label htmlFor="expenses-category" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Category</label>
              <Select
                id="expenses-category"
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
              <label htmlFor="expenses-page-size" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Page Size</label>
              <Select id="expenses-page-size" value={String(limit)} onChange={(event) => setLimit(Number(event.target.value))}>
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={200}>200</option>
              </Select>
            </div>
          </div>

          {startDate || endDate || category ? <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-app-muted">
            <p>{selectedCalendarMonth ? "Calendar month selected" : "Expense filters applied"}{category ? ` · ${formatCategoryLabel(category)}` : ""}</p>
            <Button variant="ghost" size="sm" onClick={() => { handleCalendarMonthSelect(null); setCategory(""); }}>Clear filters</Button>
          </div> : null}
          <div ref={expenseTableScrollRef} role="region" aria-label="Expense records" tabIndex={0} className="expenses-table-scroll relative mt-4 rounded-xl border border-app-border/80">
            {loading && items.length > 0 ? (
              <div className="absolute inset-x-0 top-0 z-10 bg-app-surface/95 py-3 text-center text-sm text-app-muted" role="status" aria-live="polite">
                Loading expenses...
              </div>
            ) : null}
            <Table className="min-w-[760px]" aria-label="Expenses" aria-busy={loading}>
              <TableHeader>
                <tr>
                  <TableHead>Date</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </tr>
              </TableHeader>
              <TableBody>
                {loading && items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-app-muted">
                      <span role="status" aria-live="polite">Loading expenses...</span>
                    </TableCell>
                  </TableRow>
                ) : error ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-app-negative-text">
                      <span role="alert">{error}</span>
                    </TableCell>
                  </TableRow>
                ) : items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-12 text-center text-app-muted">
                      No expenses found.
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((expense) => (
                    <TableRow key={expense.id}>
                      <TableCell className="whitespace-nowrap">{dateFormatter.format(new Date(`${expense.expense_date}T00:00:00.000Z`))}</TableCell>
                      <TableCell><span className="whitespace-nowrap rounded-md border border-app-border/60 bg-app-raised/40 px-2 py-1 text-[11px]">{formatCategoryLabel(expense.category)}</span></TableCell>
                      <TableCell className="text-right font-semibold tabular-nums">{formatCurrency(expense.amount)}</TableCell>
                      <TableCell className="max-w-[340px]" title={expense.description ?? undefined}>
                        <p className="truncate">{expense.description ?? "-"}</p>
                        {expense.tags.length > 0 ? <p className="mt-1 truncate text-[10px] text-app-muted" title={expense.tags.join(", ")}>{expense.tags.join(" · ")}</p> : null}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="sm" className="expenses-delete" disabled={demoModeEnabled || loading} title={demoModeEnabled ? demoDisabledTitle : undefined} onClick={() => void handleDeleteExpense(expense)}>
                          Delete
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          <p className="mt-2 text-xs text-slate-500 md:hidden">Swipe horizontally to review every expense column.</p>

          <nav ref={expensePaginationRef} className="mt-4 flex flex-wrap items-center justify-between gap-3" aria-label="Expense pagination">
            <p className="text-xs text-slate-400">
              {loading
                ? "Loading expense total..."
                : `Page ${Math.min(currentPage, totalPages)} of ${totalPages} (${total} total)`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={offset === 0 || loading}
                onClick={() => handleExpensePageChange(Math.max(0, offset - limit))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={offset + limit >= total || loading}
                onClick={() => handleExpensePageChange(offset + limit)}
              >
                Next
              </Button>
            </div>
          </nav>
        </CardContent>
      </Card>

      <Card id="payouts-panel" role="tabpanel" aria-labelledby="payouts-tab" tabIndex={0} hidden={ledgerView !== "payouts"} className="expenses-ledger">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="sr-only">Payouts</CardTitle>
              <CardDescription>Final payouts received after the profit split. {selectedCalendarMonth ? "Filtered to the selected calendar month." : "Showing all dates; select a calendar month to filter payouts."}</CardDescription>
            </div>
          </div>
        </CardHeader>

        <CardContent>
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3 rounded-lg bg-app-bg/35 px-4 py-3">
            <p className="text-[10px] font-medium uppercase tracking-wider text-app-muted">All recorded payouts</p>
            <dl className="flex flex-wrap gap-x-8 gap-y-3 text-xs">
              <div><dt className="text-app-muted">Total Payouts</dt><dd className="mt-1 font-semibold tabular-nums">{payoutTotalsLoading ? "..." : payoutTotalsError ? "—" : formatCurrency(payoutTotals?.total_amount ?? 0)}</dd></div>
              <div><dt className="text-app-muted">Number of Payouts</dt><dd className="mt-1 font-semibold tabular-nums">{payoutTotalsLoading ? "..." : payoutTotalsError ? "—" : (payoutTotals?.count ?? 0).toLocaleString("en-US")}</dd></div>
              <div><dt className="text-app-muted">Average Payout</dt><dd className="mt-1 font-semibold tabular-nums">{payoutTotalsLoading ? "..." : payoutTotalsError ? "—" : formatCurrency(payoutTotals?.average_amount ?? 0)}</dd></div>
            </dl>
          </div>

          {payoutTotalsError ? <p className="mt-4 text-sm text-rose-300" role="alert">{payoutTotalsError}</p> : null}

          <div ref={payoutTableScrollRef} role="region" aria-label="Payout records" tabIndex={0} className="expenses-table-scroll mt-4 rounded-xl border border-app-border/80">
            <Table className="min-w-[720px]" aria-label="Payouts" aria-busy={payoutLoading}>
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
                      <span role="status" aria-live="polite">Loading payouts...</span>
                    </TableCell>
                  </TableRow>
                ) : payoutError ? (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-rose-300">
                      <span role="alert">{payoutError}</span>
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
                      <TableCell className="text-right font-semibold tabular-nums">{formatCurrency(payout.amount)}</TableCell>
                      <TableCell className="max-w-[360px] truncate" title={payout.notes ?? undefined}>
                        {payout.notes ?? "-"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="sm" className="expenses-delete" disabled={demoModeEnabled} title={demoModeEnabled ? demoDisabledTitle : undefined} onClick={() => void handleDeletePayout(payout)}>
                          Delete
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          <p className="mt-2 text-xs text-slate-500 md:hidden">Swipe horizontally to review every payout column.</p>

          <nav className="mt-4 flex flex-wrap items-center justify-between gap-3" aria-label="Payout pagination">
            <p className="text-xs text-slate-400">
              {payoutLoading
                ? "Loading payout total..."
                : `Page ${Math.min(payoutCurrentPage, payoutTotalPages)} of ${payoutTotalPages} (${payoutTotal} total)`}
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
          </nav>
        </CardContent>
      </Card>

      </section>

      <footer className="space-y-2 border-t border-app-border/70 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs leading-5 text-app-muted">
            Expenses counted: {totalsLoading ? "..." : (totals?.count ?? 0)}. Payouts counted: {payoutTotalsLoading ? "..." : (payoutTotals?.count ?? 0)}.
            {demoModeEnabled ? <> Demo totals exclude the device's local combine tracker.</> : <> Standard activations: {combineSpendSnapshot.standardActivationCount} ({formatCurrency(combineSpendSnapshot.standardActivationCostCents / 100)}).</>}
          </p>
          {demoModeEnabled ? <p className="text-xs text-app-muted">Local combine tracking is excluded from this sample ledger.</p> : <Button variant="ghost" size="sm" onClick={handleReconcileCombineExpenses} disabled={combineTrackerLoading}>
            {combineTrackerLoading ? "Reconciling..." : "Reconcile Combine Expenses"}
          </Button>}
        </div>
        {combineTrackerError ? <p className="text-xs text-app-negative-text" role="alert">{combineTrackerError}</p> : null}
        {combineTrackerNotice ? <p className="text-xs text-app-positive-text" role="status">{combineTrackerNotice}</p> : null}
      </footer>

      <Drawer
        open={!demoModeEnabled && addPayoutOpen}
        onClose={() => setAddPayoutOpen(false)}
        title="Add Payout"
        description="Log the final payout amount you received after the profit split."
      >
        <form className="expenses-form space-y-4" onSubmit={(event) => void handleSubmitNewPayout(event)}>
          <div>
            <label htmlFor="payout-date" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Payout Date</label>
            <Input
              id="payout-date"
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
            <label htmlFor="payout-amount" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Amount (USD)</label>
            <Input
              id="payout-amount"
              type="number"
              value={addPayoutState.amount}
              onChange={(event) =>
                setAddPayoutState((current) => ({
                  ...current,
                  amount: event.target.value,
                }))
              }
              inputMode="decimal"
              min="0.01"
              step="0.01"
              placeholder="2500.00"
              required
            />
          </div>

          <div>
            <label htmlFor="payout-notes" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Notes (Optional)</label>
            <Textarea
              id="payout-notes"
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
            <Button type="submit" disabled={demoModeEnabled || addingPayout}>
              {addingPayout ? "Saving..." : "Save Payout"}
            </Button>
          </div>
        </form>
      </Drawer>

      <Drawer
        open={!demoModeEnabled && addOpen}
        onClose={() => setAddOpen(false)}
        title="Add Expense"
        description="Use paid-account presets for Topstep evaluation and activation fees."
      >
        <form className="expenses-form space-y-4" onSubmit={(event) => void handleSubmitNewExpense(event)}>
          <div>
            <label htmlFor="expense-account-type" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Account Type</label>
            <Select
              id="expense-account-type"
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
            <label htmlFor="expense-plan-size" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Plan Size</label>
            <Select
              id="expense-plan-size"
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
              <label htmlFor="expense-stage" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Stage</label>
              <Select
                id="expense-stage"
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
              <label htmlFor="expense-stage-readonly" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Stage</label>
              <Input id="expense-stage-readonly" value="evaluation_fee" disabled readOnly />
            </div>
          )}

          <div>
            <label htmlFor="expense-date" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Date</label>
            <Input
              id="expense-date"
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
            <label htmlFor="expense-amount" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Amount (USD)</label>
            <Input
              id="expense-amount"
              type="number"
              value={addState.amount}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  amount: event.target.value,
                }))
              }
              inputMode="decimal"
              min="0"
              step="0.01"
            />
          </div>

          <div>
            <label htmlFor="expense-account-id" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Account ID (Optional)</label>
            <Input
              id="expense-account-id"
              type="number"
              value={addState.accountId}
              onChange={(event) =>
                setAddState((current) => ({
                  ...current,
                  accountId: event.target.value,
                }))
              }
              inputMode="numeric"
              min="1"
              step="1"
            />
          </div>

          <div>
            <label htmlFor="expense-description" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Description (Optional)</label>
            <Input
              id="expense-description"
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
            <label htmlFor="expense-tags" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Tags (Optional)</label>
            <Input
              id="expense-tags"
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
            <Button type="submit" disabled={demoModeEnabled || adding || practiceBlocked}>
              {adding ? "Saving..." : "Save Expense"}
            </Button>
          </div>
        </form>
      </Drawer>
    </div>
  );
}
