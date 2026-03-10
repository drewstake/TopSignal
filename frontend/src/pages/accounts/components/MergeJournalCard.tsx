import type { ReactNode } from "react";

import { Button } from "../../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Select } from "../../../components/ui/Select";
import { Toggle } from "../../../components/ui/Toggle";
import type {
  AccountInfo,
  JournalMergeConflictStrategy,
  JournalMergeResult,
} from "../../../lib/types";
import type { MergeJournalFormState } from "../mergeJournal";
import { buildMergeJournalSummaryLine } from "../mergeJournal";

interface MergeJournalCardProps {
  accounts: readonly AccountInfo[];
  form: MergeJournalFormState;
  loading: boolean;
  submitDisabled: boolean;
  validationMessage: string | null;
  errorMessage: string | null;
  successMessage: string | null;
  successResult: JournalMergeResult | null;
  onFromAccountChange: (value: string) => void;
  onToAccountChange: (value: string) => void;
  onConflictChange: (value: JournalMergeConflictStrategy) => void;
  onIncludeImagesChange: (value: boolean) => void;
  onSubmit: () => void;
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

function formatMergeAccountLabel(account: AccountInfo) {
  const stateLabel = formatAccountStateLabel(account.account_state);
  return account.account_state === "ACTIVE"
    ? `${account.name} (#${account.id})`
    : `${account.name} (#${account.id}) - ${stateLabel}`;
}

function MergeJournalMessage({
  tone,
  children,
}: {
  tone: "error" | "success" | "info";
  children: ReactNode;
}) {
  const classes =
    tone === "error"
      ? "border-rose-500/35 bg-rose-500/10 text-rose-200"
      : tone === "success"
        ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-200"
        : "border-amber-500/35 bg-amber-500/10 text-amber-200";

  return (
    <div
      className={`rounded-2xl border px-4 py-3 text-sm ${classes}`}
      role={tone === "error" ? "alert" : "status"}
      aria-live={tone === "error" ? "assertive" : "polite"}
    >
      {children}
    </div>
  );
}

export function MergeJournalCard({
  accounts,
  form,
  loading,
  submitDisabled,
  validationMessage,
  errorMessage,
  successMessage,
  successResult,
  onFromAccountChange,
  onToAccountChange,
  onConflictChange,
  onIncludeImagesChange,
  onSubmit,
}: MergeJournalCardProps) {
  const statusTone = errorMessage ? "error" : successMessage ? "success" : validationMessage ? "info" : null;
  const statusMessage = errorMessage ?? successMessage ?? validationMessage;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Merge Journal</CardTitle>
        <CardDescription>
          Copy journal history from an old account into a new account. The old account stays unchanged, and matching
          dates stay on the new account unless you choose overwrite.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs uppercase tracking-[0.12em] text-slate-500">Old account</span>
            <Select
              value={form.fromAccountId}
              onChange={(event) => onFromAccountChange(event.target.value)}
              aria-label="Old account"
            >
              <option value="">Select old account</option>
              {accounts.map((account) => (
                <option key={`from-${account.id}`} value={String(account.id)}>
                  {formatMergeAccountLabel(account)}
                </option>
              ))}
            </Select>
          </label>
          <label className="space-y-1">
            <span className="text-xs uppercase tracking-[0.12em] text-slate-500">New account</span>
            <Select
              value={form.toAccountId}
              onChange={(event) => onToAccountChange(event.target.value)}
              aria-label="New account"
            >
              <option value="">Select new account</option>
              {accounts.map((account) => (
                <option key={`to-${account.id}`} value={String(account.id)}>
                  {formatMergeAccountLabel(account)}
                </option>
              ))}
            </Select>
          </label>
        </div>

        <div className="grid gap-3 md:grid-cols-[minmax(0,220px)_auto] md:items-end">
          <label className="space-y-1">
            <span className="text-xs uppercase tracking-[0.12em] text-slate-500">Conflict behavior</span>
            <Select
              value={form.onConflict}
              onChange={(event) => onConflictChange(event.target.value as JournalMergeConflictStrategy)}
              aria-label="Conflict behavior"
            >
              <option value="skip">Skip existing destination dates</option>
              <option value="overwrite">Overwrite existing destination dates</option>
            </Select>
          </label>
          <div className="pb-1">
            <Toggle
              checked={form.includeImages}
              onChange={onIncludeImagesChange}
              label="Copy linked images"
              aria-label="Copy linked images"
            />
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800/80 bg-slate-950/35 px-4 py-3 text-sm text-slate-400">
          Use <span className="font-medium text-slate-200">Skip</span> to fill in missing dates on the new account
          without touching entries that are already there. Use <span className="font-medium text-slate-200">Overwrite</span>{" "}
          only when the old account entry should replace the new account entry for the same trading date.
        </div>

        {statusTone && statusMessage ? (
          <MergeJournalMessage tone={statusTone}>
            <p>{statusMessage}</p>
            {successResult ? <p className="mt-1 text-xs text-current/80">{buildMergeJournalSummaryLine(successResult)}</p> : null}
          </MergeJournalMessage>
        ) : null}

        <div className="flex justify-end">
          <Button onClick={onSubmit} disabled={submitDisabled}>
            {loading ? "Merging..." : "Merge Journal"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
