import { useEffect, useEffectEvent, useId, useRef, useState, type FormEvent } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Input } from "../../../components/ui/Input";
import { accountsApi, isApiError } from "../../../lib/api";
import { getDemoAccountName } from "../../../lib/demoMode";
import { TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT } from "../../../lib/tradeImportEvents";
import type {
  AccountInfo,
  AccountTradeDataSource,
  TradeImportConfirmResult,
  TradeImportPreview,
  TradeImportPreviewTrade,
} from "../../../lib/types";
import { getTradeImportErrorMessage } from "./tradeImportErrors";
import { openTradeImportFilePicker } from "./tradeImportPicker";
import {
  clearPendingTradeImport,
  readPendingTradeImport,
  rememberPendingTradeImport,
} from "./tradeImportRecovery";

const acceptedFileTypes = ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const supportedFileNamePattern = /\.(csv|xlsx)$/i;
const REVIEW_PAGE_SIZE = 100;
const IMPORT_STATUS_POLL_ATTEMPTS = 4;
const IMPORT_STATUS_POLL_DELAY_MS = 600;

type ActiveImportRequestKind = "preview" | "confirm" | "status";

interface ActiveImportRequest {
  kind: ActiveImportRequestKind;
  controller: AbortController;
  generation: number;
}

function isUnknownConfirmationOutcome(error: unknown): boolean {
  if (!isApiError(error)) {
    return true;
  }
  const detail = error.detail;
  const code = detail && typeof detail === "object" ? (detail as Record<string, unknown>).code : null;
  return code === "confirmation_in_progress" || error.status === 408 || error.status >= 500;
}

function waitForImportStatusPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(resolve, IMPORT_STATUS_POLL_DELAY_MS);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeoutId);
        const abortError = new Error("Import status check cancelled");
        abortError.name = "AbortError";
        reject(abortError);
      },
      { once: true },
    );
  });
}

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 5,
});

const timestampFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
  timeZone: "America/New_York",
});

function formatCurrency(value: number) {
  return currencyFormatter.format(Number.isFinite(value) ? value : 0);
}

function formatSignedCurrency(value: number) {
  const safeValue = Number.isFinite(value) ? value : 0;
  return `${safeValue > 0 ? "+" : ""}${currencyFormatter.format(safeValue)}`;
}

function formatPrice(value: number) {
  return Number.isFinite(value) ? priceFormatter.format(value) : "—";
}

function formatTimestamp(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value || "—" : timestampFormatter.format(parsed);
}

function ImportStat({
  label,
  value,
  valueClassName = "text-app-text",
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-xl border border-app-border/75 bg-app-bg/45 px-3 py-2">
      <dt className="text-[10px] uppercase tracking-[0.12em] text-app-muted">{label}</dt>
      <dd className={`mt-1 text-sm font-semibold ${valueClassName}`}>{value}</dd>
    </div>
  );
}

function tradeStatusVariant(status: TradeImportPreviewTrade["status"]) {
  if (status === "new") {
    return "positive" as const;
  }
  return status === "conflict" ? ("negative" as const) : ("neutral" as const);
}

function formatConflictValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "Not set";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

export interface TradeImportReviewProps {
  preview: TradeImportPreview;
  confirming: boolean;
  onConfirm: () => void;
  onCheckOutcome?: () => void;
  onChooseAnother: () => void;
  onClose: () => void;
}

export function TradeImportReview({
  preview,
  confirming,
  onConfirm,
  onCheckOutcome,
  onChooseAnother,
  onClose,
}: TradeImportReviewProps) {
  const { summary } = preview;
  const canConfirm = preview.new_rows > 0 && preview.conflict_rows === 0 && !confirming;
  const [pageIndex, setPageIndex] = useState(0);
  const pageCount = Math.max(1, Math.ceil(preview.trades.length / REVIEW_PAGE_SIZE));
  const safePageIndex = Math.min(pageIndex, pageCount - 1);
  const firstVisibleRow = safePageIndex * REVIEW_PAGE_SIZE;
  const lastVisibleRow = Math.min(firstVisibleRow + REVIEW_PAGE_SIZE, preview.trades.length);
  const visibleTrades = preview.trades.slice(firstVisibleRow, firstVisibleRow + REVIEW_PAGE_SIZE);

  if (preview.new_rows === 0 && preview.duplicate_rows > 0 && preview.conflict_rows === 0) {
    const duplicateLabel = `${preview.duplicate_rows.toLocaleString("en-US")} duplicate${
      preview.duplicate_rows === 1 ? "" : "s"
    }`;

    return (
      <div
        className="rounded-xl border border-app-warning/35 bg-app-warning/10 p-4"
        role="status"
        aria-live="polite"
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-app-text">Duplicate file</p>
            <p className="mt-0.5 truncate text-xs text-app-muted" title={preview.source_file_name}>
              {preview.source_file_name}
            </p>
          </div>
          <Badge variant="neutral">{duplicateLabel}</Badge>
        </div>
        <p className="mt-3 text-xs text-app-text-soft">
          {preview.duplicate_rows === 1
            ? "That trade is already imported. Nothing new was added."
            : `All ${preview.duplicate_rows.toLocaleString("en-US")} trades are already imported. Nothing new was added.`}
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button type="button" size="sm" onClick={onChooseAnother}>
            Choose Another File
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-app-text">Review parsed trades</p>
          <p className="mt-0.5 truncate text-xs text-app-muted" title={preview.source_file_name}>
            {preview.source_file_name}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="positive">{preview.new_rows} new</Badge>
          <Badge variant="neutral">{preview.duplicate_rows} duplicate</Badge>
          {preview.conflict_rows > 0 ? <Badge variant="negative">{preview.conflict_rows} conflict</Badge> : null}
        </div>
      </div>

      <p className="text-[10px] text-app-muted-strong">
        P&amp;L totals include new rows only; duplicate rows remain visible below for review.
      </p>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
        <ImportStat label="Rows" value={preview.total_rows.toLocaleString("en-US")} />
        <ImportStat label="Wins" value={summary.wins.toLocaleString("en-US")} valueClassName="text-app-positive" />
        <ImportStat label="Losses" value={summary.losses.toLocaleString("en-US")} valueClassName="text-app-negative" />
        <ImportStat label="Breakeven" value={summary.breakeven.toLocaleString("en-US")} />
        <ImportStat label="Gross P&L" value={formatSignedCurrency(summary.gross_pnl)} />
        <ImportStat label="Fees" value={formatCurrency(-Math.abs(summary.fees))} />
        <ImportStat label="Commissions" value={formatCurrency(-Math.abs(summary.commissions))} />
        <ImportStat
          label="Net P&L"
          value={formatSignedCurrency(summary.net_pnl)}
          valueClassName={summary.net_pnl >= 0 ? "text-app-positive" : "text-app-negative"}
        />
      </dl>

      <div className="max-h-[360px] overflow-auto rounded-xl border border-app-border/80 bg-app-bg/45">
        <table className="w-full min-w-[1280px] table-fixed border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-app-surface/95 uppercase tracking-wide text-app-muted">
            <tr>
              <th className="w-[84px] px-2 py-2 text-left font-medium">Status</th>
              <th className="w-[56px] px-2 py-2 text-right font-medium">Row</th>
              <th className="w-[130px] px-2 py-2 text-left font-medium">Trade ID</th>
              <th className="w-[92px] px-2 py-2 text-left font-medium">Trade Day</th>
              <th className="w-[90px] px-2 py-2 text-left font-medium">Contract</th>
              <th className="w-[82px] px-2 py-2 text-center font-medium">Direction</th>
              <th className="w-[54px] px-2 py-2 text-center font-medium">Size</th>
              <th className="w-[150px] px-2 py-2 text-left font-medium">Entered (ET)</th>
              <th className="w-[150px] px-2 py-2 text-left font-medium">Exited (ET)</th>
              <th className="w-[88px] px-2 py-2 text-right font-medium">Entry</th>
              <th className="w-[88px] px-2 py-2 text-right font-medium">Exit</th>
              <th className="w-[88px] px-2 py-2 text-right font-medium">Gross</th>
              <th className="w-[76px] px-2 py-2 text-right font-medium">Fees</th>
              <th className="w-[92px] px-2 py-2 text-right font-medium">Comm.</th>
              <th className="w-[88px] px-2 py-2 text-right font-medium">Net</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/70">
            {visibleTrades.map((trade) => (
              <tr
                key={`${trade.row_number}-${trade.source_trade_id}`}
                className={trade.status === "duplicate" ? "bg-app-surface/25 text-app-muted" : "text-app-text-soft"}
              >
                <td className="px-2 py-2">
                  <Badge variant={tradeStatusVariant(trade.status)}>
                    {trade.status === "new" ? "New" : trade.status === "conflict" ? "Conflict" : "Duplicate"}
                  </Badge>
                </td>
                <td className="px-2 py-2 text-right font-mono">{trade.row_number}</td>
                <td className="truncate px-2 py-2 font-mono" title={trade.source_trade_id}>
                  {trade.source_trade_id}
                </td>
                <td className="px-2 py-2">{trade.trade_day}</td>
                <td className="px-2 py-2 font-medium text-app-text" title={trade.contract_name}>
                  {trade.symbol || trade.contract_name}
                </td>
                <td className="px-2 py-2 text-center uppercase">{trade.direction}</td>
                <td className="px-2 py-2 text-center">{trade.size}</td>
                <td className="px-2 py-2">{formatTimestamp(trade.entered_at)}</td>
                <td className="px-2 py-2">{formatTimestamp(trade.exited_at)}</td>
                <td className="px-2 py-2 text-right font-mono">{formatPrice(trade.entry_price)}</td>
                <td className="px-2 py-2 text-right font-mono">{formatPrice(trade.exit_price)}</td>
                <td className="px-2 py-2 text-right font-medium">{formatSignedCurrency(trade.gross_pnl)}</td>
                <td className="px-2 py-2 text-right">{formatCurrency(-Math.abs(trade.fees))}</td>
                <td className="px-2 py-2 text-right">{formatCurrency(-Math.abs(trade.commissions))}</td>
                <td
                  className={`px-2 py-2 text-right font-semibold ${
                    trade.net_pnl >= 0 ? "text-app-positive" : "text-app-negative"
                  }`}
                >
                  {formatSignedCurrency(trade.net_pnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {preview.conflict_rows > 0 ? (
        <div className="space-y-2 rounded-xl border border-app-negative/40 bg-app-negative/10 p-3" role="alert">
          <p className="text-sm font-semibold text-app-negative">
            Resolve {preview.conflict_rows.toLocaleString("en-US")} conflicting trade
            {preview.conflict_rows === 1 ? "" : "s"} before importing
          </p>
          <p className="text-xs text-app-text-soft">
            A stored trade has the same identity but different financial details. Nothing will be overwritten.
          </p>
          <div className="space-y-2">
            {preview.trades
              .filter((trade) => trade.status === "conflict")
              .map((trade) => (
                <div key={`conflict-${trade.row_number}-${trade.source_trade_id}`} className="rounded-lg border border-app-negative/30 bg-app-bg/50 p-2">
                  <p className="text-xs font-medium text-app-text">
                    Row {trade.row_number} · {trade.source_trade_id || trade.contract_name}
                  </p>
                  {trade.conflict?.differences.length ? (
                    <dl className="mt-2 grid gap-1 text-[11px] text-app-text-soft">
                      {trade.conflict.differences.map((difference) => (
                        <div key={difference.field} className="grid gap-1 sm:grid-cols-[minmax(100px,0.6fr)_1fr_1fr]">
                          <dt className="font-medium text-app-muted">{difference.field.replaceAll("_", " ")}</dt>
                          <dd>Stored: {formatConflictValue(difference.stored)}</dd>
                          <dd>Incoming: {formatConflictValue(difference.incoming)}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="mt-1 text-[11px] text-app-muted">
                      Multiple stored rows share this identity; review the source export before retrying.
                    </p>
                  )}
                </div>
              ))}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-app-muted-strong">
        <p>{`Rows ${preview.trades.length === 0 ? 0 : firstVisibleRow + 1}–${lastVisibleRow} of ${preview.trades.length}`}</p>
        {pageCount > 1 ? (
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              disabled={safePageIndex === 0 || confirming}
              onClick={() => setPageIndex((current) => Math.max(0, current - 1))}
            >
              Previous Rows
            </Button>
            <span>
              Page {safePageIndex + 1} of {pageCount}
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={safePageIndex >= pageCount - 1 || confirming}
              onClick={() => setPageIndex((current) => Math.min(pageCount - 1, current + 1))}
            >
              Next Rows
            </Button>
          </div>
        ) : null}
      </div>

      {preview.conflict_rows > 0 ? (
        <p className="rounded-xl border border-app-negative/40 bg-app-negative/10 px-3 py-2 text-xs text-app-negative" role="alert">
          Confirmation is blocked while identity conflicts remain.
        </p>
      ) : preview.new_rows === 0 ? (
        <p className="rounded-xl border border-app-border/80 bg-app-surface/40 px-3 py-2 text-xs text-app-muted" role="status">
          Every parsed trade is already stored. There is nothing new to import.
        </p>
      ) : (
        <p className="text-xs text-app-muted">
          Confirming will store only the {preview.new_rows.toLocaleString("en-US")} new trade
          {preview.new_rows === 1 ? "" : "s"}; duplicates will be skipped.
        </p>
      )}

      <div className="flex flex-wrap justify-end gap-2">
        {confirming && onCheckOutcome ? (
          <Button type="button" variant="secondary" size="sm" onClick={onCheckOutcome}>
            Cancel &amp; Check Outcome
          </Button>
        ) : null}
        <Button type="button" variant="ghost" size="sm" disabled={confirming} onClick={onClose}>
          Close
        </Button>
        <Button variant="ghost" size="sm" disabled={confirming} onClick={onChooseAnother}>
          Choose Another File
        </Button>
        {preview.conflict_rows === 0 ? (
          <Button size="sm" disabled={!canConfirm} onClick={onConfirm}>
            {confirming ? "Importing..." : `Confirm Import (${preview.new_rows})`}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export interface TradeImportPanelProps {
  accountId: number | null;
  tradeDataSource?: AccountTradeDataSource | null;
  liveAccounts?: readonly AccountInfo[];
  accountsLoading?: boolean;
  accountSetupRequest?: number;
  onImportComplete?: (result: TradeImportConfirmResult) => void | Promise<void>;
  onAccountCreated?: (account: AccountInfo) => void | Promise<void>;
  onAccountSelected?: (account: AccountInfo) => void | Promise<void>;
}

export function TradeImportPanel({
  accountId,
  tradeDataSource = null,
  liveAccounts = [],
  accountsLoading = false,
  accountSetupRequest = 0,
  onImportComplete,
  onAccountCreated,
  onAccountSelected,
}: TradeImportPanelProps) {
  const inputId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const noticeFocusRef = useRef<HTMLDivElement | null>(null);
  const activeRequestRef = useRef<ActiveImportRequest | null>(null);
  const confirmInFlightRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const lastAccountSetupRequestRef = useRef(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<TradeImportPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [checkingOutcome, setCheckingOutcome] = useState(false);
  const [outcomeStatusMessage, setOutcomeStatusMessage] = useState<string | null>(null);
  const [recoveryToken, setRecoveryToken] = useState<string | null>(null);
  const [creatingAccount, setCreatingAccount] = useState(false);
  const [selectingAccountId, setSelectingAccountId] = useState<number | null>(null);
  const [showAccountForm, setShowAccountForm] = useState(!accountsLoading && accountId === null);
  const [liveAccountName, setLiveAccountName] = useState("");
  const [liveStartingBalance, setLiveStartingBalance] = useState("10000");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TradeImportConfirmResult | null>(null);
  const canImport = accountId !== null && tradeDataSource === "csv_import";
  const hasLiveAccount = liveAccounts.length > 0;

  function cancelActiveRequest() {
    requestGenerationRef.current += 1;
    const activeRequest = activeRequestRef.current;
    // A confirmation can already be committed server-side. Do not abort that
    // transport and pretend the import did not happen; make its UI generation
    // stale and recover the durable outcome by token instead.
    if (activeRequest && activeRequest.kind !== "confirm") {
      activeRequest.controller.abort();
    }
    activeRequestRef.current = null;
    confirmInFlightRef.current = false;
  }

  function resetSelection() {
    cancelActiveRequest();
    setSelectedFile(null);
    setPreview(null);
    setPreviewing(false);
    setConfirming(false);
    setCheckingOutcome(false);
    setOutcomeStatusMessage(null);
    setRecoveryToken(null);
    setCreatingAccount(false);
    setShowAccountForm(!accountsLoading && accountId === null);
    setLiveAccountName("");
    setError(null);
    setResult(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  useEffect(() => {
    cancelActiveRequest();
    setSelectedFile(null);
    setPreview(null);
    setPreviewing(false);
    setConfirming(false);
    setCheckingOutcome(false);
    setOutcomeStatusMessage(null);
    setRecoveryToken(null);
    setError(null);
    setResult(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }

    if (accountsLoading) {
      setShowAccountForm(false);
    } else if (accountId !== null && tradeDataSource === "csv_import") {
      setShowAccountForm(false);
      setLiveAccountName("");
    } else if (accountId === null && !hasLiveAccount) {
      setShowAccountForm(true);
    }
  }, [accountId, accountsLoading, hasLiveAccount, tradeDataSource]);

  useEffect(() => {
    if (!error && !result && !preview && !checkingOutcome) {
      return;
    }
    const focusTimer = window.setTimeout(() => noticeFocusRef.current?.focus(), 0);
    return () => window.clearTimeout(focusTimer);
  }, [checkingOutcome, error, preview, result]);

  useEffect(() => {
    return () => {
      cancelActiveRequest();
    };
  }, []);

  useEffect(() => {
    if (
      accountSetupRequest <= 0 ||
      accountSetupRequest <= lastAccountSetupRequestRef.current
    ) {
      return;
    }

    if (accountsLoading) {
      return;
    }

    lastAccountSetupRequestRef.current = accountSetupRequest;
    if (canImport) {
      return;
    }

    setShowAccountForm(true);
    setError(null);
    const scrollTimer = window.setTimeout(() => {
      document.getElementById("live-trade-import-panel")?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 0);
    return () => window.clearTimeout(scrollTimer);
  }, [accountSetupRequest, accountsLoading, canImport]);

  async function previewFile(file: File) {
    if (!accountId) {
      setError("Select an account before importing trades.");
      return;
    }
    if (!canImport) {
      setError("Select or add a separate Live CSV account before importing trades.");
      return;
    }

    cancelActiveRequest();
    const requestGeneration = requestGenerationRef.current;
    const controller = new AbortController();
    activeRequestRef.current = { kind: "preview", controller, generation: requestGeneration };
    setSelectedFile(file);
    setPreview(null);
    setResult(null);
    setError(null);
    setPreviewing(true);

    try {
      const nextPreview = await accountsApi.previewTradeImport(accountId, file, { signal: controller.signal });
      if (requestGeneration === requestGenerationRef.current) {
        setPreview(nextPreview);
      }
    } catch (nextError) {
      if (controller.signal.aborted) {
        return;
      }
      if (requestGeneration === requestGenerationRef.current) {
        setError(getTradeImportErrorMessage(nextError));
      }
    } finally {
      if (requestGeneration === requestGenerationRef.current) {
        activeRequestRef.current = null;
        setPreviewing(false);
      }
    }
  }

  function handleFileChange(file: File | null) {
    setError(null);
    setResult(null);
    if (!file) {
      resetSelection();
      return;
    }

    if (!supportedFileNamePattern.test(file.name)) {
      resetSelection();
      setError("Choose a Topstep CSV or Excel file (.csv or .xlsx).");
      return;
    }

    void previewFile(file);
  }

  function handleChooseFile() {
    setError(null);
    setResult(null);

    if (!canImport) {
      setShowAccountForm(true);
      setError(
        accountId === null
          ? "Add or select a Live CSV account before choosing a trade file."
          : "Select or add a separate Live CSV account before choosing a trade file.",
      );
      return;
    }

    if (!openTradeImportFilePicker(fileInputRef.current)) {
      setError("The file picker is unavailable. Refresh the page and try again.");
    }
  }

  function handleChooseAnotherFile() {
    resetSelection();
    openTradeImportFilePicker(fileInputRef.current);
  }

  const chooseFileFromHeader = useEffectEvent(() => {
    handleChooseFile();
  });

  useEffect(() => {
    const handleFilePickerRequest = () => chooseFileFromHeader();
    window.addEventListener(TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT, handleFilePickerRequest);
    return () => window.removeEventListener(TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT, handleFilePickerRequest);
  }, []);

  async function applyCommittedImport(
    requestAccountId: number,
    previewToken: string,
    importResult: TradeImportConfirmResult,
    requestGeneration: number,
  ) {
    if (requestGeneration !== requestGenerationRef.current) {
      return;
    }
    setResult(importResult);
    setSelectedFile(null);
    setPreview(null);
    setRecoveryToken(null);
    setOutcomeStatusMessage(null);
    setError(null);
    clearPendingTradeImport(requestAccountId, previewToken);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }

    try {
      await onImportComplete?.(importResult);
    } catch {
      // The import is already committed. A later dashboard refresh can recover.
    }
  }

  async function checkImportOutcome(requestAccountId: number, previewToken: string) {
    cancelActiveRequest();
    const requestGeneration = requestGenerationRef.current;
    const controller = new AbortController();
    activeRequestRef.current = { kind: "status", controller, generation: requestGeneration };
    setRecoveryToken(previewToken);
    setCheckingOutcome(true);
    setOutcomeStatusMessage("Import outcome unknown—checking status...");
    setError(null);

    try {
      let confirmationRetryAttempted = false;
      for (let attempt = 0; attempt < IMPORT_STATUS_POLL_ATTEMPTS; attempt += 1) {
        const status = await accountsApi.getTradeImportStatus(requestAccountId, previewToken, {
          signal: controller.signal,
        });
        if (requestGeneration !== requestGenerationRef.current) {
          return;
        }

        if (status.status === "committed" && status.result) {
          await applyCommittedImport(requestAccountId, previewToken, status.result, requestGeneration);
          return;
        }
        if (status.status === "pending" && status.confirmation_retryable && !confirmationRetryAttempted) {
          confirmationRetryAttempted = true;
          activeRequestRef.current = { kind: "confirm", controller, generation: requestGeneration };
          try {
            const retriedResult = await accountsApi.confirmTradeImport(requestAccountId, previewToken, {
              signal: controller.signal,
            });
            if (requestGeneration !== requestGenerationRef.current) {
              return;
            }
            await applyCommittedImport(requestAccountId, previewToken, retriedResult, requestGeneration);
            return;
          } catch (retryError) {
            if (controller.signal.aborted || requestGeneration !== requestGenerationRef.current) {
              return;
            }
            if (!isUnknownConfirmationOutcome(retryError)) {
              clearPendingTradeImport(requestAccountId, previewToken);
              setError(getTradeImportErrorMessage(retryError));
              return;
            }
            activeRequestRef.current = { kind: "status", controller, generation: requestGeneration };
          }
        }
        if (status.status === "pending" || status.status === "confirming") {
          if (attempt < IMPORT_STATUS_POLL_ATTEMPTS - 1) {
            await waitForImportStatusPoll(controller.signal);
            continue;
          }
          setError("The import is still processing. Retry the status check in a moment.");
          return;
        }
        if (status.status === "stale") {
          setError("This preview is stale because account data changed. Choose the file again for a fresh review.");
        } else if (status.status === "expired") {
          setError("This preview expired. Choose the file again to create a fresh preview.");
        } else if (status.status === "conflict") {
          setError("This preview contains unresolved trade conflicts and was not imported.");
        } else {
          setError("The import did not complete. Review the file and try again.");
        }
        clearPendingTradeImport(requestAccountId, previewToken);
        return;
      }
    } catch (statusError) {
      if (!controller.signal.aborted && requestGeneration === requestGenerationRef.current) {
        setError(
          `Import outcome is still unknown. ${getTradeImportErrorMessage(statusError)} Retry the status check.`,
        );
      }
    } finally {
      if (requestGeneration === requestGenerationRef.current) {
        activeRequestRef.current = null;
        setCheckingOutcome(false);
        setOutcomeStatusMessage(null);
      }
    }
  }

  async function handleConfirm() {
    if (
      !accountId ||
      !canImport ||
      !preview ||
      preview.new_rows <= 0 ||
      preview.conflict_rows > 0 ||
      confirmInFlightRef.current
    ) {
      return;
    }

    cancelActiveRequest();
    const requestGeneration = requestGenerationRef.current;
    const controller = new AbortController();
    activeRequestRef.current = { kind: "confirm", controller, generation: requestGeneration };
    confirmInFlightRef.current = true;
    rememberPendingTradeImport(accountId, preview.preview_token);
    setConfirming(true);
    setError(null);

    try {
      const importResult = await accountsApi.confirmTradeImport(
        accountId,
        preview.preview_token,
        { signal: controller.signal },
      );
      if (requestGeneration !== requestGenerationRef.current) {
        return;
      }

      await applyCommittedImport(accountId, preview.preview_token, importResult, requestGeneration);
    } catch (nextError) {
      if (requestGeneration !== requestGenerationRef.current) {
        return;
      }
      if (isUnknownConfirmationOutcome(nextError)) {
        activeRequestRef.current = null;
        confirmInFlightRef.current = false;
        setConfirming(false);
        await checkImportOutcome(accountId, preview.preview_token);
      } else {
        clearPendingTradeImport(accountId, preview.preview_token);
        setError(getTradeImportErrorMessage(nextError));
      }
    } finally {
      if (requestGeneration === requestGenerationRef.current) {
        activeRequestRef.current = null;
        confirmInFlightRef.current = false;
        setConfirming(false);
      }
    }
  }

  async function handleCreateLiveAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedName = liveAccountName.trim();
    if (!normalizedName) {
      setError("Enter the Live account name.");
      return;
    }
    if (normalizedName.length > 120) {
      setError("Live account name must be 120 characters or fewer.");
      return;
    }
    const startingBalance = Number(liveStartingBalance);
    if (!Number.isFinite(startingBalance) || startingBalance <= 0 || startingBalance > 1_000_000_000) {
      setError("Enter a starting balance between $0.01 and $1,000,000,000.");
      return;
    }

    setCreatingAccount(true);
    setError(null);
    try {
      const account = await accountsApi.createLiveImportAccount({
        name: normalizedName,
        starting_balance: startingBalance,
      });
      await onAccountCreated?.(account);
      setLiveAccountName("");
      setLiveStartingBalance("10000");
      setShowAccountForm(false);
    } catch (nextError) {
      setError(getTradeImportErrorMessage(nextError));
    } finally {
      setCreatingAccount(false);
    }
  }

  async function handleSelectLiveAccount(account: AccountInfo) {
    setSelectingAccountId(account.id);
    setError(null);
    try {
      await onAccountSelected?.(account);
      setShowAccountForm(false);
    } catch {
      setError("Failed to select the Live CSV account.");
    } finally {
      setSelectingAccountId(null);
    }
  }

  function handleCheckConfirmationOutcome() {
    if (!accountId || !preview) {
      return;
    }
    setConfirming(false);
    confirmInFlightRef.current = false;
    void checkImportOutcome(accountId, preview.preview_token);
  }

  function handleCancelOutcomeCheck() {
    if (!recoveryToken) {
      return;
    }
    cancelActiveRequest();
    setCheckingOutcome(false);
    setOutcomeStatusMessage(null);
    setError("Import outcome is still unknown. Retry the status check when you are ready.");
  }

  function handleCloseError() {
    setError(null);
    setRecoveryToken(null);
    setOutcomeStatusMessage(null);
  }

  function handleRetryError() {
    if (recoveryToken && accountId) {
      void checkImportOutcome(accountId, recoveryToken);
      return;
    }
    if (selectedFile && !preview) {
      void previewFile(selectedFile);
    }
  }

  const recoverPendingOutcome = useEffectEvent((requestAccountId: number, previewToken: string) => {
    void checkImportOutcome(requestAccountId, previewToken);
  });

  useEffect(() => {
    if (!canImport || accountId === null) {
      return;
    }
    const pendingToken = readPendingTradeImport(accountId);
    if (pendingToken) {
      recoverPendingOutcome(accountId, pendingToken);
    }
  }, [accountId, canImport]);

  const retryAvailable = Boolean((recoveryToken && accountId) || (selectedFile && !preview));
  const busy = previewing || confirming || checkingOutcome || creatingAccount || selectingAccountId !== null;

  return (
    <section
      id="live-trade-import-panel"
      className={canImport ? "contents" : "rounded-xl border border-app-border/80 bg-app-bg/45 p-3"}
      aria-label={canImport ? "Trade import" : undefined}
      aria-labelledby={canImport ? undefined : `${inputId}-title`}
      aria-busy={busy}
    >
      {!canImport ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p id={`${inputId}-title`} className="text-sm font-semibold text-app-text">
            Import trades
          </p>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {!accountsLoading ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                aria-expanded={showAccountForm}
                aria-controls={`${inputId}-account-setup`}
                onClick={() => {
                  setShowAccountForm((current) => !current);
                  setError(null);
                }}
              >
                {showAccountForm
                  ? hasLiveAccount
                    ? "Cancel Live Account Selection"
                    : "Cancel Live Account Setup"
                  : hasLiveAccount
                    ? "Select Live Account"
                    : "Add Live Account"}
              </Button>
            ) : null}
            {accountsLoading ? (
              <span className="text-xs text-app-muted" role="status">
                Loading account...
              </span>
            ) : (
              <Button type="button" size="sm" disabled>
                Upload trade file
              </Button>
            )}
          </div>
        </div>
      ) : null}
      <input
        ref={fileInputRef}
        id={inputId}
        type="file"
        accept={acceptedFileTypes}
        disabled={!canImport || busy}
        tabIndex={-1}
        className="sr-only"
        aria-label="Trade export file (.csv or .xlsx)"
        onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
      />

      {!accountsLoading && showAccountForm ? (
        <div
          id={`${inputId}-account-setup`}
          className="mt-3 space-y-3 rounded-xl border border-app-border/80 bg-app-surface/35 p-3"
        >
          {liveAccounts.length > 0 ? (
            <div className="space-y-2">
              <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-app-muted">
                Existing Live CSV accounts
              </p>
              <div className="flex flex-wrap gap-2">
                {liveAccounts.map((account) => {
                  const selected = account.id === accountId;
                  return (
                    <Button
                      key={account.id}
                      type="button"
                      variant={selected ? "secondary" : "ghost"}
                      size="sm"
                      disabled={busy || selected}
                      onClick={() => void handleSelectLiveAccount(account)}
                    >
                      {selectingAccountId === account.id
                        ? "Selecting..."
                        : selected
                          ? `${getDemoAccountName(account)} · Selected`
                          : getDemoAccountName(account)}
                    </Button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {!hasLiveAccount ? (
            <form
              className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,0.65fr)_auto] md:items-end"
              onSubmit={(event) => void handleCreateLiveAccount(event)}
            >
              <label className="space-y-1">
                <span className="block text-[10px] font-medium uppercase tracking-[0.12em] text-app-muted">
                  Account name
                </span>
                <Input
                  value={liveAccountName}
                  maxLength={120}
                  autoComplete="off"
                  placeholder="TOPX8141"
                  disabled={creatingAccount}
                  onChange={(event) => setLiveAccountName(event.target.value)}
                />
              </label>
              <label className="space-y-1">
                <span className="block text-[10px] font-medium uppercase tracking-[0.12em] text-app-muted">
                  Starting balance
                </span>
                <Input
                  type="number"
                  min="0.01"
                  max="1000000000"
                  step="0.01"
                  value={liveStartingBalance}
                  inputMode="decimal"
                  disabled={creatingAccount}
                  onChange={(event) => setLiveStartingBalance(event.target.value)}
                />
              </label>
              <Button type="submit" size="sm" disabled={creatingAccount || !liveAccountName.trim()}>
                {creatingAccount ? "Adding..." : "Add Import Account"}
              </Button>
              <p className="text-[10px] text-app-muted-strong md:col-span-3">
                Enter the account name and opening balance shown by Topstep. TopSignal adds imported net P&amp;L to that balance automatically.
              </p>
            </form>
          ) : null}
        </div>
      ) : null}

      {!accountsLoading ? (
        !accountId ? (
          <p className="mt-3 rounded-xl border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning">
            Add or select the Live account before choosing a trade file.
          </p>
        ) : !canImport ? (
          <p className="mt-3 rounded-xl border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning">
            Select or add a separate Live CSV account before choosing a trade file. Your Express account will remain unchanged.
          </p>
        ) : null
      ) : null}

      {previewing ? (
        <div
          ref={noticeFocusRef}
          tabIndex={-1}
          className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-app-accent/30 bg-app-accent/10 px-3 py-2 text-xs text-app-text focus:outline-none focus-visible:ring-2 focus-visible:ring-app-accent"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <p>Validating and parsing {selectedFile?.name ?? "trade file"}...</p>
          <Button type="button" variant="ghost" size="sm" onClick={resetSelection}>
            Cancel
          </Button>
        </div>
      ) : null}

      {checkingOutcome && outcomeStatusMessage ? (
        <div
          ref={noticeFocusRef}
          tabIndex={-1}
          className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning focus:outline-none focus-visible:ring-2 focus-visible:ring-app-warning"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <span>{outcomeStatusMessage}</span>
          <Button type="button" variant="ghost" size="sm" onClick={handleCancelOutcomeCheck}>
            Cancel
          </Button>
        </div>
      ) : null}

      {error ? (
        <div
          ref={noticeFocusRef}
          tabIndex={-1}
          className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-app-negative/35 bg-app-negative/10 px-3 py-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-app-negative"
          role="alert"
          aria-live="assertive"
          aria-atomic="true"
        >
          <p className="text-xs text-app-negative">{error}</p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={handleCloseError}>
              Close
            </Button>
            {retryAvailable ? (
              <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={handleRetryError}>
              Retry
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {result ? (
        <div
          ref={noticeFocusRef}
          tabIndex={-1}
          className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-app-positive/35 bg-app-positive/10 px-3 py-2 text-xs text-app-positive focus:outline-none focus-visible:ring-2 focus-visible:ring-app-positive"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <p>
            Imported {result.inserted_rows.toLocaleString("en-US")} trade{result.inserted_rows === 1 ? "" : "s"} from{" "}
            {result.source_file_name}; skipped {result.duplicate_rows.toLocaleString("en-US")} duplicate
            {result.duplicate_rows === 1 ? "" : "s"}.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={resetSelection}>
              Close
            </Button>
            <Button type="button" size="sm" onClick={handleChooseAnotherFile}>
              Import Another File
            </Button>
          </div>
        </div>
      ) : null}

      {preview ? (
        <div
          ref={!error && !checkingOutcome ? noticeFocusRef : undefined}
          tabIndex={-1}
          className="mt-4 border-t border-app-border/70 pt-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-app-accent"
        >
          <TradeImportReview
            key={preview.file_sha256}
            preview={preview}
            confirming={confirming || checkingOutcome}
            onConfirm={() => void handleConfirm()}
            onCheckOutcome={confirming && !checkingOutcome ? handleCheckConfirmationOutcome : undefined}
            onChooseAnother={handleChooseAnotherFile}
            onClose={resetSelection}
          />
        </div>
      ) : null}
    </section>
  );
}
