import type { AccountInfo } from "../../lib/types";

export type LiveAccountEnableDecision =
  | { kind: "select"; accountId: number }
  | { kind: "setup"; liveAccountCount: number };

export function resolveLiveAccountEnableDecision(
  accounts: readonly AccountInfo[],
): LiveAccountEnableDecision {
  const liveAccounts = accounts.filter((account) => account.trade_data_source === "csv_import");
  if (liveAccounts.length === 1) {
    return {
      kind: "select",
      accountId: liveAccounts[0].id,
    };
  }
  return {
    kind: "setup",
    liveAccountCount: liveAccounts.length,
  };
}

export function resolveProjectXAccountId(
  accounts: readonly AccountInfo[],
  preferredAccountId: number | null,
): number | null {
  const projectXAccounts = accounts.filter((account) => account.trade_data_source === "projectx");
  if (projectXAccounts.length === 0) {
    return null;
  }

  if (
    preferredAccountId !== null &&
    projectXAccounts.some((account) => account.id === preferredAccountId)
  ) {
    return preferredAccountId;
  }

  return projectXAccounts.find((account) => account.is_main)?.id ?? projectXAccounts[0].id;
}
