import { describe, expect, it, vi } from "vitest";

import { AccountRequestGate } from "../../lib/accountRequestGate";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

async function settleLikeJournal<T>(
  gate: AccountRequestGate,
  accountId: number,
  channel: string,
  operation: Promise<T>,
  callbacks: {
    success: (result: T) => void;
    failure: (error: unknown) => void;
    settled: () => void;
  },
) {
  const request = gate.begin(accountId, channel);
  try {
    const result = await operation;
    if (gate.isCurrent(request)) {
      callbacks.success(result);
    }
  } catch (error) {
    if (gate.isCurrent(request)) {
      callbacks.failure(error);
    }
  } finally {
    if (gate.isCurrent(request)) {
      callbacks.settled();
    }
  }
}

describe("Journal account-switch mutation guards", () => {
  it.each([
    "create-entry",
    "autosave:41",
    "delete-entry:41",
    "ai-recap",
    "trade-stats:41",
    "image-upload:41",
    "image-delete:41:9",
    "copy:recent-7",
  ])("ignores a stale %s success completion", async (channel) => {
    const gate = new AccountRequestGate();
    gate.activate(7101);
    const operation = deferred<{ account_id: number }>();
    const success = vi.fn();
    const failure = vi.fn();
    const settled = vi.fn();
    const completion = settleLikeJournal(gate, 7101, channel, operation.promise, {
      success,
      failure,
      settled,
    });

    gate.activate(88001);
    operation.resolve({ account_id: 7101 });
    await completion;

    expect(success).not.toHaveBeenCalled();
    expect(failure).not.toHaveBeenCalled();
    expect(settled).not.toHaveBeenCalled();
  });

  it("does not apply an old-account AI skip result", async () => {
    const gate = new AccountRequestGate();
    gate.activate(7101);
    const operation = deferred<{ skipped: boolean; skip_reason: string }>();
    const success = vi.fn();
    const completion = settleLikeJournal(gate, 7101, "ai-recap", operation.promise, {
      success,
      failure: vi.fn(),
      settled: vi.fn(),
    });

    gate.activate(88001);
    operation.resolve({ skipped: true, skip_reason: "no_trades_for_day" });
    await completion;

    expect(success).not.toHaveBeenCalled();
  });

  it("does not expose an old autosave conflict on the new account", async () => {
    const gate = new AccountRequestGate();
    gate.activate(7101);
    const operation = deferred<never>();
    const failure = vi.fn();
    const completion = settleLikeJournal(gate, 7101, "autosave:41", operation.promise, {
      success: vi.fn(),
      failure,
      settled: vi.fn(),
    });

    gate.activate(88001);
    operation.reject(new Error("journal_entry_version_conflict"));
    await completion;

    expect(failure).not.toHaveBeenCalled();
  });

  it("does not expose an old mutation failure or clear the new account's busy state", async () => {
    const gate = new AccountRequestGate();
    gate.activate(7101);
    const operation = deferred<never>();
    const failure = vi.fn();
    const settled = vi.fn();
    const completion = settleLikeJournal(gate, 7101, "delete-entry:41", operation.promise, {
      success: vi.fn(),
      failure,
      settled,
    });

    gate.activate(88001);
    operation.reject(new Error("Delete failed"));
    await completion;

    expect(failure).not.toHaveBeenCalled();
    expect(settled).not.toHaveBeenCalled();
  });
});
