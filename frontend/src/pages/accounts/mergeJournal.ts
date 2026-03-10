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

export function filterMergeSourceAccounts<
  T extends Pick<AccountInfo, "id" | "name"> & Partial<Pick<AccountInfo, "provider_name">>,
>(
  accounts: readonly T[],
  query: string,
): T[] {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) {
    return [...accounts];
  }

  return accounts.filter((account) => {
    const idText = String(account.id);
    const nameText = account.name.toLowerCase();
    const providerNameText = (account.provider_name ?? "").toLowerCase();
    return (
      idText.includes(normalizedQuery) ||
      nameText.includes(normalizedQuery) ||
      providerNameText.includes(normalizedQuery)
    );
  });
}

export function getMergeDestinationAccounts<T extends Pick<AccountInfo, "account_state">>(
  accounts: readonly T[],
): T[] {
  return accounts.filter(
    (account) => account.account_state === "ACTIVE" || account.account_state === "LOCKED_OUT",
  );
}

export function reconcileMergeJournalForm(
  current: MergeJournalFormState,
  sourceAccounts: readonly Pick<AccountInfo, "id">[],
  destinationAccounts: readonly Pick<AccountInfo, "id">[],
  preferredDestinationAccountId: number | null,
): MergeJournalFormState {
  const sourceIds = new Set(sourceAccounts.map((account) => String(account.id)));
  const destinationIds = new Set(destinationAccounts.map((account) => String(account.id)));
  const preferredDestinationId =
    preferredDestinationAccountId !== null ? String(preferredDestinationAccountId) : "";

  let toAccountId =
    current.toAccountId && destinationIds.has(current.toAccountId)
      ? current.toAccountId
      : preferredDestinationId && destinationIds.has(preferredDestinationId)
        ? preferredDestinationId
        : destinationAccounts[0]
          ? String(destinationAccounts[0].id)
          : "";

  let fromAccountId =
    current.fromAccountId && sourceIds.has(current.fromAccountId) ? current.fromAccountId : "";
  if (!fromAccountId || fromAccountId === toAccountId) {
    fromAccountId = sourceAccounts.find((account) => String(account.id) !== toAccountId)?.id?.toString() ?? "";
  }

  if (!toAccountId && fromAccountId) {
    toAccountId = destinationAccounts.find((account) => String(account.id) !== fromAccountId)?.id?.toString() ?? "";
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
