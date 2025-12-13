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

export async function searchAccounts(onlyActiveAccounts = true) {
  // docs list onlyActiveAccounts as an optional parameter
  return topstepPost<AccountSearchResponse>("/api/Account/search", {
    onlyActiveAccounts,
  });
}
