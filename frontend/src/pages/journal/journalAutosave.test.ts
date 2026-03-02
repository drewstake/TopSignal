import { describe, expect, it, vi } from "vitest";

import { DebouncedAutosaveQueue } from "./journalAutosave";
import { JOURNAL_AUTOSAVE_DELAY_MS } from "./journalUtils";

interface Payload {
  value: string;
}

describe("DebouncedAutosaveQueue", () => {
  it("debounces burst edits into a single save", async () => {
    vi.useFakeTimers();
    const save = vi.fn(async (payload: Payload) => {
      void payload;
      return undefined;
    });
    const queue = new DebouncedAutosaveQueue<Payload>({
      delayMs: JOURNAL_AUTOSAVE_DELAY_MS,
      save,
      equals: (left, right) => left.value === right.value,
      onStateChange: () => undefined,
    });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "draft-1" });
    queue.queue({ value: "draft-2" });

    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS - 1);
    expect(save).toHaveBeenCalledTimes(0);

    await vi.advanceTimersByTimeAsync(1);
    await queue.flush();

    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0]).toEqual({ value: "draft-2" });
    vi.useRealTimers();
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

    const queue = new DebouncedAutosaveQueue<Payload>({
      delayMs: JOURNAL_AUTOSAVE_DELAY_MS,
      save,
      equals: (left, right) => left.value === right.value,
      onStateChange: () => undefined,
    });

    queue.setBaseline({ value: "baseline" });
    queue.queue({ value: "draft-1" });
    await vi.advanceTimersByTimeAsync(JOURNAL_AUTOSAVE_DELAY_MS);

    queue.queue({ value: "draft-2" });
    queue.queue({ value: "draft-3" });

    expect(save).toHaveBeenCalledTimes(1);

    resolveFirstSave();
    await queue.flush();

    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls[1][0]).toEqual({ value: "draft-3" });
    vi.useRealTimers();
  });
});
