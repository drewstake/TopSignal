import { describe, expect, it, vi } from "vitest";

import { AccountRequestGate } from "../../lib/accountRequestGate";
import { runAccountScopedTradeSync } from "./tradesAccountRequests";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

describe("runAccountScopedTradeSync", () => {
  it("never reloads or repaints after an Express-to-Live account switch", async () => {
    const gate = new AccountRequestGate();
    gate.activate(7101);
    const expressRefresh = deferred<{ fetched_count: number; inserted_count: number }>();
    const reload = vi.fn(async () => undefined);
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const onSettled = vi.fn();

    const sync = runAccountScopedTradeSync({
      accountId: 7101,
      gate,
      refresh: () => expressRefresh.promise,
      reload,
      onSuccess,
      onError,
      onSettled,
    });

    gate.activate(88001);
    expressRefresh.resolve({ fetched_count: 4, inserted_count: 2 });
    await sync;

    expect(reload).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(onSettled).not.toHaveBeenCalled();
  });

  it("does not surface an old-account failure on the newly active account", async () => {
    const gate = new AccountRequestGate();
    gate.activate(7101);
    const expressRefresh = deferred<never>();
    const onError = vi.fn();
    const onSettled = vi.fn();

    const sync = runAccountScopedTradeSync({
      accountId: 7101,
      gate,
      refresh: () => expressRefresh.promise,
      reload: vi.fn(async () => undefined),
      onSuccess: vi.fn(),
      onError,
      onSettled,
    });

    gate.activate(88001);
    expressRefresh.reject(new Error("Express unavailable"));
    await sync;

    expect(onError).not.toHaveBeenCalled();
    expect(onSettled).not.toHaveBeenCalled();
  });
});
