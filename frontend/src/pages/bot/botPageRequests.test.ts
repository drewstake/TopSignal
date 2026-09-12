import { afterEach, describe, expect, it, vi } from "vitest";
import { boundedBotRequest, BotRequestTimeout } from "./botPageRequests";

afterEach(() => vi.useRealTimers());

describe("bounded bot diagnostics", () => {
  it("settles a hung request at its deadline and aborts the transport", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const request = boundedBotRequest(new Promise(() => {}), controller, "Timed out", 45_000);
    const assertion = expect(request).rejects.toBeInstanceOf(BotRequestTimeout);
    await vi.advanceTimersByTimeAsync(45_000);
    await assertion;
    expect(controller.signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("releases a cancelled request even when its adapter ignores abort", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const request = boundedBotRequest(new Promise(() => {}), controller, "Timed out");
    const assertion = expect(request).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await assertion;
    expect(vi.getTimerCount()).toBe(0);
  });
});
