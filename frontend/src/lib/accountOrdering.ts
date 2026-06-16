import type { AccountInfo } from "./types";

const COMBINE_ACCOUNT_PREFIXES = ["50KTC", "100KTC", "150KTC"] as const;
const EXPRESS_ACCOUNT_PREFIX = "EXPRESS";
const PRACTICE_ACCOUNT_PATTERN = /^PA(?:[-_\s]|$)|\bPRACTICE\b/i;

function isExpressAccountName(name: string): boolean {
  return name.trim().toUpperCase().startsWith(EXPRESS_ACCOUNT_PREFIX);
}

function isCombineAccountName(name: string): boolean {
  const normalized = name.trim().toUpperCase();
  return COMBINE_ACCOUNT_PREFIXES.some((prefix) => normalized.startsWith(prefix));
}

function isPracticeAccountName(name: string): boolean {
  return PRACTICE_ACCOUNT_PATTERN.test(name.trim());
}

function getSelectionGroup(name: string): number {
  if (isExpressAccountName(name)) {
    return 0;
  }
  if (isCombineAccountName(name)) {
    return 1;
  }
  if (isPracticeAccountName(name)) {
    return 3;
  }
  return 2;
}

function getAccountTypeRank(name: string): number {
  const normalized = name.trim().toUpperCase();
  if (isExpressAccountName(normalized)) {
    return 0;
  }
  const combineRank = COMBINE_ACCOUNT_PREFIXES.findIndex((prefix) => normalized.startsWith(prefix));
  if (combineRank >= 0) {
    return combineRank;
  }
  if (isPracticeAccountName(normalized)) {
    return 2;
  }
  return 0;
}

function getGroupingName(account: Pick<AccountInfo, "name"> & Partial<Pick<AccountInfo, "provider_name">>): string {
  return account.provider_name ?? account.name;
}

export function compareAccountsForSelection(
  left: Pick<AccountInfo, "id" | "name" | "is_main"> & Partial<Pick<AccountInfo, "provider_name">>,
  right: Pick<AccountInfo, "id" | "name" | "is_main"> & Partial<Pick<AccountInfo, "provider_name">>,
): number {
  const groupDifference = getSelectionGroup(getGroupingName(left)) - getSelectionGroup(getGroupingName(right));
  if (groupDifference !== 0) {
    return groupDifference;
  }

  const mainDifference = Number(right.is_main) - Number(left.is_main);
  if (mainDifference !== 0) {
    return mainDifference;
  }

  const typeRankDifference = getAccountTypeRank(getGroupingName(left)) - getAccountTypeRank(getGroupingName(right));
  if (typeRankDifference !== 0) {
    return typeRankDifference;
  }

  const nameDifference = left.name.localeCompare(right.name, undefined, {
    sensitivity: "base",
    numeric: true,
  });
  if (nameDifference !== 0) {
    return nameDifference;
  }

  return left.id - right.id;
}

export function sortAccountsForSelection<
  T extends Pick<AccountInfo, "id" | "name" | "is_main"> & Partial<Pick<AccountInfo, "provider_name">>,
>(
  accounts: readonly T[],
): T[] {
  return [...accounts].sort(compareAccountsForSelection);
}
