import { AccountRequestGate } from "../../lib/accountRequestGate";

interface AccountScopedTradeSyncOptions<TResult> {
  accountId: number;
  gate: AccountRequestGate;
  refresh: () => Promise<TResult>;
  reload: (accountId: number) => Promise<void>;
  onSuccess: (result: TResult) => void;
  onError: (error: unknown) => void;
  onSettled: () => void;
}

/** Runs the post-sync reload only while the initiating account remains active. */
export async function runAccountScopedTradeSync<TResult>({
  accountId,
  gate,
  refresh,
  reload,
  onSuccess,
  onError,
  onSettled,
}: AccountScopedTradeSyncOptions<TResult>): Promise<void> {
  const request = gate.begin(accountId, "sync");
  try {
    const result = await refresh();
    if (!gate.isCurrent(request)) {
      return;
    }

    await reload(accountId);
    if (gate.isCurrent(request)) {
      onSuccess(result);
    }
  } catch (error) {
    if (gate.isCurrent(request)) {
      onError(error);
    }
  } finally {
    if (gate.isCurrent(request)) {
      onSettled();
    }
  }
}
