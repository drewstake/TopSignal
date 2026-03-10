import type {
  AccountInfo,
  JournalMergeConflictStrategy,
  JournalMergeResult,
} from "../../lib/types";

export interface MergeJournalFormState {
  fromAccountId: string;
  toAccountId: string;
  onConflict: JournalMergeConflictStrategy;
  includeImages: boolean;
}

export function reconcileMergeJournalForm(
  current: MergeJournalFormState,
  accounts: readonly Pick<AccountInfo, "id">[],
  preferredDestinationAccountId: number | null,
): MergeJournalFormState {
  const availableIds = new Set(accounts.map((account) => String(account.id)));
  const preferredDestinationId =
    preferredDestinationAccountId !== null ? String(preferredDestinationAccountId) : "";

  let toAccountId =
    current.toAccountId && availableIds.has(current.toAccountId)
      ? current.toAccountId
      : preferredDestinationId && availableIds.has(preferredDestinationId)
        ? preferredDestinationId
        : accounts[0]
          ? String(accounts[0].id)
          : "";

  let fromAccountId =
    current.fromAccountId && availableIds.has(current.fromAccountId) ? current.fromAccountId : "";
  if (!fromAccountId || fromAccountId === toAccountId) {
    fromAccountId = accounts.find((account) => String(account.id) !== toAccountId)?.id?.toString() ?? "";
  }

  if (!toAccountId && fromAccountId) {
    toAccountId = accounts.find((account) => String(account.id) !== fromAccountId)?.id?.toString() ?? "";
  }

  return {
    ...current,
    fromAccountId,
    toAccountId,
  };
}

export function validateMergeJournalForm(form: MergeJournalFormState): string | null {
  if (!form.fromAccountId || !form.toAccountId) {
    return "Select both an old account and a new account.";
  }
  if (form.fromAccountId === form.toAccountId) {
    return "Old and new account must be different.";
  }
  return null;
}

export function buildMergeJournalSuccessMessage(
  result: JournalMergeResult,
  accountNamesById?: ReadonlyMap<number, string>,
): string {
  const fromName = accountNamesById?.get(result.from_account_id) ?? `Account ${result.from_account_id}`;
  const toName = accountNamesById?.get(result.to_account_id) ?? `Account ${result.to_account_id}`;
  const transferredLabel = result.transferred_count === 1 ? "1 entry" : `${result.transferred_count} entries`;
  const parts = [`Merged ${transferredLabel} from ${fromName} into ${toName}.`];

  if (result.skipped_count > 0) {
    const skippedLabel = result.skipped_count === 1 ? "1 conflict was skipped" : `${result.skipped_count} conflicts were skipped`;
    parts.push(skippedLabel + ".");
  }
  if (result.overwritten_count > 0) {
    const overwrittenLabel =
      result.overwritten_count === 1 ? "1 destination entry was overwritten" : `${result.overwritten_count} destination entries were overwritten`;
    parts.push(overwrittenLabel + ".");
  }
  if (result.image_count > 0) {
    const imageLabel = result.image_count === 1 ? "1 image copied" : `${result.image_count} images copied`;
    parts.push(imageLabel + ".");
  }

  return parts.join(" ");
}

export function buildMergeJournalSummaryLine(result: JournalMergeResult): string {
  return `Transferred ${result.transferred_count}, skipped ${result.skipped_count}, overwritten ${result.overwritten_count}, images ${result.image_count}.`;
}
