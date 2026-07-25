import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Input } from "../../../components/ui/Input";
import { accountsApi } from "../../../lib/api";
import { getDemoAccountId, getDemoAccountName } from "../../../lib/demoMode";
import type {
  AccountInfo,
  AccountTradeDataSource,
  TradeImportConfirmResult,
  TradeImportPreview,
  TradeImportPreviewTrade,
} from "../../../lib/types";
import { getTradeImportErrorMessage } from "./tradeImportErrors";

const acceptedFileTypes = ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const supportedFileNamePattern = /\.(csv|xlsx)$/i;
const REVIEW_PAGE_SIZE = 100;

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
  return status === "new" ? ("positive" as const) : ("neutral" as const);
}

export interface TradeImportReviewProps {
  preview: TradeImportPreview;
  confirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function TradeImportReview({ preview, confirming, onConfirm, onCancel }: TradeImportReviewProps) {
  const { summary } = preview;
  const canConfirm = preview.new_rows > 0 && !confirming;
  const [pageIndex, setPageIndex] = useState(0);
  const pageCount = Math.max(1, Math.ceil(preview.trades.length / REVIEW_PAGE_SIZE));
  const safePageIndex = Math.min(pageIndex, pageCount - 1);
  const firstVisibleRow = safePageIndex * REVIEW_PAGE_SIZE;
  const lastVisibleRow = Math.min(firstVisibleRow + REVIEW_PAGE_SIZE, preview.trades.length);
  const visibleTrades = preview.trades.slice(firstVisibleRow, firstVisibleRow + REVIEW_PAGE_SIZE);

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
                  <Badge variant={tradeStatusVariant(trade.status)}>{trade.status === "new" ? "New" : "Duplicate"}</Badge>
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

      {preview.new_rows === 0 ? (
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
        <Button variant="ghost" size="sm" disabled={confirming} onClick={onCancel}>
          Choose Another File
        </Button>
        <Button size="sm" disabled={!canConfirm} onClick={onConfirm}>
          {confirming ? "Importing..." : `Confirm Import (${preview.new_rows})`}
        </Button>
      </div>
    </div>
  );
}

export interface TradeImportPanelProps {
  accountId: number | null;
  tradeDataSource?: AccountTradeDataSource | null;
  liveAccounts?: readonly AccountInfo[];
  accountSetupRequest?: number;
  onImportComplete?: (result: TradeImportConfirmResult) => void | Promise<void>;
  onAccountCreated?: (account: AccountInfo) => void | Promise<void>;
  onAccountSelected?: (account: AccountInfo) => void | Promise<void>;
}

export function TradeImportPanel({
  accountId,
  tradeDataSource = null,
  liveAccounts = [],
  accountSetupRequest = 0,
  onImportComplete,
  onAccountCreated,
  onAccountSelected,
}: TradeImportPanelProps) {
  const inputId = useId();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const activeControllerRef = useRef<AbortController | null>(null);
  const requestGenerationRef = useRef(0);
  const lastAccountSetupRequestRef = useRef(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<TradeImportPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [creatingAccount, setCreatingAccount] = useState(false);
  const [selectingAccountId, setSelectingAccountId] = useState<number | null>(null);
  const [showAccountForm, setShowAccountForm] = useState(accountId === null);
  const [liveAccountId, setLiveAccountId] = useState("");
  const [liveAccountName, setLiveAccountName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TradeImportConfirmResult | null>(null);
  const canImport = accountId !== null && tradeDataSource === "csv_import";

  function cancelActiveRequest() {
    requestGenerationRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
  }

  function resetSelection() {
    cancelActiveRequest();
    setSelectedFile(null);
    setPreview(null);
    setPreviewing(false);
    setConfirming(false);
    setCreatingAccount(false);
    setShowAccountForm(accountId === null);
    setLiveAccountId("");
    setLiveAccountName("");
    setError(null);
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
    setError(null);
    setResult(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, [accountId, tradeDataSource]);

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

    lastAccountSetupRequestRef.current = accountSetupRequest;
    setShowAccountForm(true);
    setError(null);
    const scrollTimer = window.setTimeout(() => {
      document.getElementById("live-trade-import-panel")?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 0);
    return () => window.clearTimeout(scrollTimer);
  }, [accountSetupRequest]);

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
    activeControllerRef.current = controller;
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
        activeControllerRef.current = null;
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

  async function handleConfirm() {
    if (!accountId || !canImport || !selectedFile || !preview || preview.new_rows <= 0) {
      return;
    }

    cancelActiveRequest();
    const requestGeneration = requestGenerationRef.current;
    const controller = new AbortController();
    activeControllerRef.current = controller;
    setConfirming(true);
    setError(null);

    try {
      const importResult = await accountsApi.confirmTradeImport(
        accountId,
        selectedFile,
        preview.file_sha256,
        { signal: controller.signal },
      );
      if (requestGeneration !== requestGenerationRef.current) {
        return;
      }

      setResult(importResult);
      setSelectedFile(null);
      setPreview(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }

      try {
        await onImportComplete?.(importResult);
      } catch {
        // The import is already committed. A later dashboard refresh can recover.
      }
    } catch (nextError) {
      if (!controller.signal.aborted && requestGeneration === requestGenerationRef.current) {
        setError(getTradeImportErrorMessage(nextError));
      }
    } finally {
      if (requestGeneration === requestGenerationRef.current) {
        activeControllerRef.current = null;
        setConfirming(false);
      }
    }
  }

  async function handleCreateLiveAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedId = liveAccountId.trim();
    const accountIdValue = Number(normalizedId);
    if (!/^\d+$/.test(normalizedId) || !Number.isSafeInteger(accountIdValue) || accountIdValue <= 0) {
      setError("Enter the positive numeric account ID shown by Topstep.");
      return;
    }
    const normalizedName = liveAccountName.trim();
    if (normalizedName.length > 120) {
      setError("Live account name must be 120 characters or fewer.");
      return;
    }

    setCreatingAccount(true);
    setError(null);
    try {
      const account = await accountsApi.createLiveImportAccount({
        account_id: accountIdValue,
        ...(normalizedName ? { name: normalizedName } : {}),
      });
      await onAccountCreated?.(account);
      setLiveAccountId("");
      setLiveAccountName("");
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

  const busy = previewing || confirming || creatingAccount || selectingAccountId !== null;

  return (
    <section
      id="live-trade-import-panel"
      className="rounded-xl border border-app-border/80 bg-app-bg/45 p-3"
      aria-labelledby={`${inputId}-title`}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-2xl">
          <p id={`${inputId}-title`} className="text-sm font-semibold text-app-text">
            Import Topstep Live Trades
          </p>
          <p className="mt-1 text-xs text-app-muted">
            Upload a CSV or Excel trade export for the selected Live CSV account. You can review parsed rows and duplicates before anything is stored.
          </p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-2"
            disabled={busy}
            onClick={() => {
              setShowAccountForm((current) => !current);
              setError(null);
            }}
          >
            {showAccountForm ? "Cancel Live Account Setup" : "Add Live Account"}
          </Button>
        </div>
        <label className="block w-full max-w-md space-y-1" htmlFor={inputId}>
          <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-app-muted">Trade export file</span>
          <Input
            ref={fileInputRef}
            id={inputId}
            type="file"
            accept={acceptedFileTypes}
            disabled={!canImport || busy}
            aria-describedby={`${inputId}-help`}
            onChange={(event) => handleFileChange(event.target.files?.[0] ?? null)}
            className="h-auto min-h-10 py-1.5 file:mr-3 file:rounded-lg file:border-0 file:bg-app-accent/15 file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-app-text"
          />
          <span id={`${inputId}-help`} className="block text-[10px] text-app-muted-strong">
            Accepted: .csv and .xlsx
          </span>
        </label>
      </div>

      {showAccountForm ? (
        <div className="mt-3 space-y-3 rounded-xl border border-app-border/80 bg-app-surface/35 p-3">
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
                          : `${getDemoAccountName(account)} · ID ${getDemoAccountId(account.id)}`}
                    </Button>
                  );
                })}
              </div>
            </div>
          ) : null}

          <form
            className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] md:items-end"
            onSubmit={(event) => void handleCreateLiveAccount(event)}
          >
            <label className="space-y-1">
              <span className="block text-[10px] font-medium uppercase tracking-[0.12em] text-app-muted">
                Topstep Live account ID
              </span>
              <Input
                value={liveAccountId}
                inputMode="numeric"
                autoComplete="off"
                placeholder="e.g. 88001"
                disabled={creatingAccount}
                onChange={(event) => setLiveAccountId(event.target.value)}
              />
            </label>
            <label className="space-y-1">
              <span className="block text-[10px] font-medium uppercase tracking-[0.12em] text-app-muted">
                Account name (optional)
              </span>
              <Input
                value={liveAccountName}
                maxLength={120}
                autoComplete="off"
                placeholder="Topstep Live Funded"
                disabled={creatingAccount}
                onChange={(event) => setLiveAccountName(event.target.value)}
              />
            </label>
            <Button type="submit" size="sm" disabled={creatingAccount || !liveAccountId.trim()}>
              {creatingAccount ? "Adding..." : "Add Import Account"}
            </Button>
            <p className="text-[10px] text-app-muted-strong md:col-span-3">
              Topstep exports do not include an account ID. Enter the Live account number so imported trades stay separate
              from your Express account.
            </p>
          </form>
        </div>
      ) : null}

      {!accountId ? (
        <p className="mt-3 rounded-xl border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning">
          Add or select the Live account before choosing a trade file.
        </p>
      ) : !canImport ? (
        <p className="mt-3 rounded-xl border border-app-warning/35 bg-app-warning/10 px-3 py-2 text-xs text-app-warning">
          Select or add a separate Live CSV account before choosing a trade file. Your Express account will remain unchanged.
        </p>
      ) : null}

      {previewing ? (
        <p className="mt-3 rounded-xl border border-app-accent/30 bg-app-accent/10 px-3 py-2 text-xs text-app-text" role="status">
          Validating and parsing {selectedFile?.name ?? "trade file"}...
        </p>
      ) : null}

      {error ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-app-negative/35 bg-app-negative/10 px-3 py-2">
          <p className="text-xs text-app-negative" role="alert">
            {error}
          </p>
          {selectedFile && !preview ? (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void previewFile(selectedFile)}>
              Retry
            </Button>
          ) : null}
        </div>
      ) : null}

      {result ? (
        <p className="mt-3 rounded-xl border border-app-positive/35 bg-app-positive/10 px-3 py-2 text-xs text-app-positive" role="status">
          Imported {result.inserted_rows.toLocaleString("en-US")} trade{result.inserted_rows === 1 ? "" : "s"} from{" "}
          {result.source_file_name}; skipped {result.duplicate_rows.toLocaleString("en-US")} duplicate
          {result.duplicate_rows === 1 ? "" : "s"}.
        </p>
      ) : null}

      {preview ? (
        <div className="mt-4 border-t border-app-border/70 pt-4">
          <TradeImportReview
            key={preview.file_sha256}
            preview={preview}
            confirming={confirming}
            onConfirm={() => void handleConfirm()}
            onCancel={resetSelection}
          />
        </div>
      ) : null}
    </section>
  );
}
