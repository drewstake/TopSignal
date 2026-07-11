export function canApplyAccountScopedResult(
  requestedAccountId: number,
  activeAccountId: number | null,
  isLatestGeneration: boolean,
): boolean {
  return isLatestGeneration && activeAccountId === requestedAccountId;
}
