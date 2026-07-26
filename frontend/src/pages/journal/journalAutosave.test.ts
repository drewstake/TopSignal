import { afterEach, describe, expect, it, vi } from "vitest";

import { hasActiveLiveMutationRequests } from "../../lib/liveMutationState";
import { DebouncedAutosaveQueue } from "./journalAutosave";
import { JOURNAL_AUTOSAVE_DELAY_MS } from "./journalUtils";

interface Payload {
  value: string;
}

const queues: Array<DebouncedAutosaveQueue<Payload>> = [];

function createQueue({
  save,
  onStateChange = () => undefined,
  onError,
}: {
  save: (payload: Payload) => Promise<void>;
  onStateChange?: (state: "saved" | "saving" | "unsaved" | "error") => void;
  onError?: (error: unknown) => void;
}) {
  const queue = new DebouncedAutosaveQueue<Payload>({
    delayMs: JOURNAL_AUTOSAVE_DELAY_MS,
    save,
    equals: (left, right) => left.value === right.value,
    onStateChange,
    onError,
  });
  queues.push(queue);
  return queue;
}

afterEach(() => {
  queues.splice(0).forEach((queue) => queue.dispose());
  expect(hasActiveLiveMutationRequests()).toBe(false);
  vi.useRealTimers();
});

describe("DebouncedAutosaveQueue", () => {
  it("holds one live-mutation lease from the first queued edit through debounce and rescheduling", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async (payload: Payload) => {
      void payload;
      return undefined;
    });
    const queue = createQueue({ save });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "draft-1" });
    expect(hasActiveLiveMutationRequests()).toBe(true);

    await vi.advanceTimersByTimeAsync(Math.floor(JOURNAL_AUTOSAVE_DELAY_MS / 2));
    queue.queue({ value: "draft-2" });
    expect(hasActiveLiveMutationRequests()).toBe(true);

    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS - 1);
    expect(save).toHaveBeenCalledTimes(0);
    expect(hasActiveLiveMutationRequests()).toBe(true);

    await vi.advanceTimersByTimeAsync(1);

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toEqual({ value: "draft-2" });
    expect(hasActiveLiveMutationRequests()).toBe(false);
  });

  it("queues one follow-up save while a request is in flight", async () => {
    vi.useFakeTimers();
    let resolveFirstSave: () => void = () => undefined;

    const save = vi.fn((payload: Payload) => {
      if (payload.value === "draft-1") {
        return new Promise<void>((resolve) => {
          resolveFirstSave = () => resolve();
        });
      }
      return Promise.resolve();
    });

    const queue = createQueue({ save });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "draft-1" });
    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS);
    expect(hasActiveLiveMutationRequests()).toBe(true);

    queue.queue({ value: "draft-2" });
    queue.queue({ value: "draft-3" });

    expect(save).toHaveBeenCalledTimes(1);
    expect(hasActiveLiveMutationRequests()).toBe(true);

    resolveFirstSave();
    await queue.flush();

    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ value: "draft-3" });
    expect(hasActiveLiveMutationRequests()).toBe(false);
  });

  it("keeps dirty work guarded after an error and releases after a successful retry", async () => {
    vi.useFakeTimers();
    const error = new Error("save failed");
    const save = vi.fn<(payload: Payload) => Promise<void>>()
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce(undefined);
    const onError = vi.fn();
    const states: string[] = [];
    const queue = createQueue({
      save,
      onError,
      onStateChange: (state) => states.push(state),
    });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "unsaved draft" });
    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS);

    expect(onError).toHaveBeenCalledWith(error);
    expect(states.at(-1)).toBe("error");
    expect(hasActiveLiveMutationRequests()).toBe(true);

    await queue.retryNow();

    expect(save).toHaveBeenCalledTimes(2);
    expect(states.at(-1)).toBe("saved");
    expect(hasActiveLiveMutationRequests()).toBe(false);
  });

  it("cancels a pending debounce without saving or leaking the mutation lease", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async () => undefined);
    const queue = createQueue({ save });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "discarded draft" });
    expect(hasActiveLiveMutationRequests()).toBe(true);

    queue.cancel();
    expect(hasActiveLiveMutationRequests()).toBe(false);

    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS);
    expect(save).not.toHaveBeenCalled();
  });

  it("releases a pending lease and timer when disposed", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async () => undefined);
    const queue = createQueue({ save });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "draft" });
    expect(hasActiveLiveMutationRequests()).toBe(true);

    queue.dispose();
    expect(hasActiveLiveMutationRequests()).toBe(false);

    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS);
    expect(save).not.toHaveBeenCalled();
  });
});
