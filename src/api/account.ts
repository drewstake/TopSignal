import { topstepPost } from "./topstepClient";

export type TopstepAccount = {
  id: number;
  name: string;
  balance: number;
  canTrade: boolean;
  isVisible: boolean;
};

export type AccountSearchResponse = {
  accounts: TopstepAccount[];
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export type AccountSearchArgs =
  | boolean
  | {
      onlyActiveAccounts?: boolean;
      includeInvisibleAccounts?: boolean;
      cacheTtlMs?: number;
      forceRefresh?: boolean;
    };

export async function searchAccounts(args: AccountSearchArgs = true) {
  const onlyActiveAccounts = typeof args === "boolean" ? args : args.onlyActiveAccounts ?? true;
  const includeInvisibleAccounts =
    typeof args === "boolean" ? false : args.includeInvisibleAccounts ?? false;
  const cacheTtlMs = typeof args === "boolean" ? undefined : args.cacheTtlMs;
  const forceRefresh = typeof args === "boolean" ? false : args.forceRefresh ?? false;

  // docs list onlyActiveAccounts as an optional parameter
  return topstepPost<AccountSearchResponse>(
    "/api/Account/search",
    {
      onlyActiveAccounts,
      includeInvisibleAccounts,
    },
    { cacheTtlMs, forceRefresh }
  );
}
