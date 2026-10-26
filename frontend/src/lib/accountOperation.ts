import { authIdentityRevision, subscribeToAuthIdentityChanges, tokenIdentity } from "./authIdentity";
import { isDemoModeEnabled, subscribeToDemoModeChanges } from "./demoMode";
import { getAccessTokenSync } from "./supabase";

export class AccountOperationCancelled extends Error {
  constructor() {
    super("Reconciliation cancelled because the account, mode, or page changed. Review the current account and reconcile again.");
    this.name = "AccountOperationCancelled";
  }
}

export interface AccountOperation {
  signal: AbortSignal;
  assertCurrent: (requestToken?: string | null) => void;
  dispose: () => void;
}

/** A permanent cancellation latch, including A -> B -> A and demo round trips. */
export function createAccountOperation(parentSignal?: AbortSignal): AccountOperation {
  const identity = tokenIdentity(getAccessTokenSync());
  const revision = authIdentityRevision();
  const demo = isDemoModeEnabled();
  const controller = new AbortController();
  const cancel = () => controller.abort(new AccountOperationCancelled());
  const unsubscribeAuth = subscribeToAuthIdentityChanges(cancel);
  const unsubscribeDemo = subscribeToDemoModeChanges(({ enabled }) => {
    if (enabled !== demo) cancel();
  });
  parentSignal?.addEventListener("abort", cancel, { once: true });
  if (parentSignal?.aborted) cancel();
  return {
    signal: controller.signal,
    assertCurrent(requestToken) {
      if (revision !== authIdentityRevision() || identity !== tokenIdentity(getAccessTokenSync())
        || demo !== isDemoModeEnabled() || (requestToken !== undefined && identity !== tokenIdentity(requestToken))) cancel();
      controller.signal.throwIfAborted();
    },
    dispose() {
      cancel();
      unsubscribeAuth();
      unsubscribeDemo();
      parentSignal?.removeEventListener("abort", cancel);
    },
  };
}
