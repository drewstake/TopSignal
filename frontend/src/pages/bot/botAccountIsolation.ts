import type { AccountInfo, BotConfig, BotConfigListResponse } from "../../lib/types";

export interface BotConfigLoadResult {
  configs: BotConfigListResponse;
  cacheScope: string | null;
}

export type BotConfigLoader = (accountId: number) => Promise<BotConfigLoadResult>;

export function resolveActiveBotAccount(
  accounts: AccountInfo[],
  accountFromQuery: number | null,
) {
  if (accountFromQuery !== null) {
    const queryAccount = accounts.find((account) => account.id === accountFromQuery);
    if (queryAccount) {
      return queryAccount;
    }
  }

  return accounts.find((account) => account.is_main) ?? accounts[0] ?? null;
}

export function isProjectXBotAccount(account: AccountInfo | null): account is AccountInfo {
  return account?.trade_data_source === "projectx";
}

export function getProjectXBotAccounts(accounts: AccountInfo[]) {
  return accounts.filter((account) => account.trade_data_source === "projectx");
}

export function getBotProviderAccountId(account: AccountInfo | null) {
  return isProjectXBotAccount(account) ? account.id : null;
}

export function filterBotConfigsByAccount(
  configs: BotConfig[],
  accountId: number,
) {
  return configs.filter((config) => config.account_id === accountId);
}

export async function loadBotConfigsForProviderAccount(
  accountId: number | null,
  loader: BotConfigLoader,
): Promise<BotConfigLoadResult | null> {
  if (accountId === null) {
    return null;
  }

  const result = await loader(accountId);
  const items = filterBotConfigsByAccount(result.configs.items, accountId);
  return {
    ...result,
    configs: {
      ...result.configs,
      items,
      total: items.length,
    },
  };
}
