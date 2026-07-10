import { afterEach, describe, expect, it, vi } from "vitest";

import { parseProjectXMarketDepthSseFrame, streamProjectXMarketDepth } from "./api";

describe("parseProjectXMarketDepthSseFrame", () => {
  it("parses snapshots and drops malformed levels without relabeling currentVolume", () => {
    const parsed = parseProjectXMarketDepthSseFrame([
      "event: snapshot",
      'data: {"contract_id":"CON.F.US.MNQ.U26","sequence":42,"timestamp":"2026-07-10T14:30:00Z",',
      'data: "bids":[{"price":100,"size":7},{"price":99,"volume":4},{"price":98,"current_volume":12}],',
      'data: "asks":[{"price":101,"size":5},{"price":"bad","size":8}],"reset":true}',
    ].join("\n"));

    expect(parsed).toEqual({
      event: "snapshot",
      data: {
        contract_id: "CON.F.US.MNQ.U26",
        sequence: 42,
        timestamp: "2026-07-10T14:30:00Z",
        bids: [{ price: 100, size: 7 }, { price: 99, size: 4 }],
        asks: [{ price: 101, size: 5 }],
        reset: true,
      },
    });
  });

  it("parses incremental level updates and explicit connection states", () => {
    expect(parseProjectXMarketDepthSseFrame([
      "event: update",
      'data: {"contract_id":"CON.F.US.MNQ.U26","sequence":43,"timestamp":"2026-07-10T14:30:01Z","side":"ask","price":101,"size":0}',
    ].join("\n"))).toEqual({
      event: "update",
      data: {
        contract_id: "CON.F.US.MNQ.U26",
        sequence: 43,
        timestamp: "2026-07-10T14:30:01Z",
        side: "ask",
        price: 101,
        size: 0,
      },
    });
    expect(parseProjectXMarketDepthSseFrame([
      "event: state",
      'data: {"contract_id":"CON.F.US.MNQ.U26","state":"reconnecting","message":"Retrying"}',
    ].join("\n"))).toEqual({
      event: "state",
      data: {
        contract_id: "CON.F.US.MNQ.U26",
        state: "reconnecting",
        message: "Retrying",
      },
    });
  });

  it("rejects malformed, unknown, and provider-shaped events", () => {
    expect(parseProjectXMarketDepthSseFrame("event: update\ndata: not-json")).toBeNull();
    expect(parseProjectXMarketDepthSseFrame('event: other\ndata: {"ok":true}')).toBeNull();
    expect(parseProjectXMarketDepthSseFrame([
      "event: update",
      'data: {"contract_id":"CON.F.US.MNQ.U26","sequence":44,"timestamp":"2026-07-10T14:30:02Z","side":"bid","price":100,"current_volume":9}',
    ].join("\n"))).toBeNull();
    expect(parseProjectXMarketDepthSseFrame([
      "event: update",
      'data: {"contract_id":"CON.F.US.MNQ.U26","sequence":44.5,"timestamp":"2026-07-10T14:30:02Z","side":"bid","price":100,"size":9}',
    ].join("\n"))).toBeNull();
  });
});

describe("streamProjectXMarketDepth", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("maps terminal backend/configuration errors to unavailable without retrying", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("Depth disabled", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const onState = vi.fn();
    const close = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState, onSnapshot: vi.fn(), onUpdate: vi.fn() },
    );

    await waitForCondition(() => fetchMock.mock.calls.length === 1 && onState.mock.calls.length > 0);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onState).toHaveBeenCalledWith({
      contract_id: "CON.F.US.MNQ.U26",
      state: "unavailable",
      message: "Depth disabled",
    });
    close();
  });

  it("treats backend configuration HTTP 500 errors as terminal while preserving their detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      '{"detail":"projectx_api_base_url_not_configured"}',
      { status: 500 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const onState = vi.fn();
    const close = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState, onSnapshot: vi.fn(), onUpdate: vi.fn() },
    );

    await waitForCondition(() => onState.mock.calls.some(([next]) => next.state === "unavailable"));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onState).toHaveBeenCalledWith({
      contract_id: "CON.F.US.MNQ.U26",
      state: "unavailable",
      message: "projectx_api_base_url_not_configured",
    });
    close();
  });

  it("reports EOF as disconnected and retries with an abortable backoff", async () => {
    const emptyStream = () => new ReadableStream<Uint8Array>({
      start(controller) {
        controller.close();
      },
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(emptyStream(), { status: 200 }))
      .mockResolvedValueOnce(new Response("Depth disabled", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const onState = vi.fn();
    const close = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState, onSnapshot: vi.fn(), onUpdate: vi.fn() },
    );

    await waitForCondition(() => onState.mock.calls.some(([next]) => next.state === "disconnected"));
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ state: "disconnected" }));
    expect(onState).not.toHaveBeenCalledWith(expect.objectContaining({ state: "connected" }));

    await waitForCondition(() => onState.mock.calls.some(([next]) => next.state === "unavailable"), 2_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ state: "reconnecting" }));
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ state: "unavailable" }));
    close();
  });

  it("cancels and reconnects instead of forwarding a sequence-gapped update", async () => {
    const cancelBody = vi.fn();
    const encoder = new TextEncoder();
    const gappedStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode([
          sseEvent("snapshot", {
            contract_id: "CON.F.US.MNQ.U26",
            sequence: 10,
            timestamp: "2026-07-10T14:30:00Z",
            bids: [{ price: 100, size: 6 }],
            asks: [{ price: 101, size: 5 }],
          }),
          sseEvent("update", {
            contract_id: "CON.F.US.MNQ.U26",
            sequence: 12,
            timestamp: "2026-07-10T14:30:01Z",
            side: "bid",
            price: 100,
            size: 99,
          }),
        ].join("")));
      },
      cancel(reason) {
        cancelBody(reason);
      },
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(gappedStream, { status: 200 }))
      .mockResolvedValueOnce(new Response("Depth disabled", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const onState = vi.fn();
    const onSnapshot = vi.fn();
    const onUpdate = vi.fn();
    const close = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState, onSnapshot, onUpdate },
    );

    await waitForCondition(() => onState.mock.calls.some(([next]) => next.state === "unavailable"), 2_000);

    expect(onSnapshot).toHaveBeenCalledTimes(1);
    expect(onUpdate).not.toHaveBeenCalled();
    expect(cancelBody).toHaveBeenCalledWith(expect.stringContaining("10 to 12"));
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({
      state: "disconnected",
      message: expect.stringContaining("10 to 12"),
    }));
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ state: "reconnecting" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    close();
  });

  it("accepts a lower sequence after an authoritative snapshot reset", async () => {
    const encoder = new TextEncoder();
    const resetStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode([
          sseEvent("snapshot", {
            contract_id: "CON.F.US.MNQ.U26",
            sequence: 10,
            timestamp: "2026-07-10T14:30:00Z",
            bids: [{ price: 100, size: 6 }],
            asks: [{ price: 101, size: 5 }],
          }),
          sseEvent("update", {
            contract_id: "CON.F.US.MNQ.U26",
            sequence: 11,
            timestamp: "2026-07-10T14:30:01Z",
            side: "bid",
            price: 100,
            size: 7,
          }),
          sseEvent("snapshot", {
            contract_id: "CON.F.US.MNQ.U26",
            sequence: 1,
            timestamp: "2026-07-10T14:30:02Z",
            bids: [{ price: 100, size: 8 }],
            asks: [{ price: 101, size: 5 }],
            reset: true,
          }),
          sseEvent("update", {
            contract_id: "CON.F.US.MNQ.U26",
            sequence: 2,
            timestamp: "2026-07-10T14:30:03Z",
            side: "bid",
            price: 100,
            size: 9,
          }),
        ].join("")));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(resetStream, { status: 200 })));
    const onSnapshot = vi.fn();
    const onUpdate = vi.fn();
    const close = streamProjectXMarketDepth(
      { contractId: "CON.F.US.MNQ.U26" },
      { onState: vi.fn(), onSnapshot, onUpdate },
    );

    await waitForCondition(() => onUpdate.mock.calls.length === 2);

    expect(onSnapshot.mock.calls.map(([next]) => next.sequence)).toEqual([10, 1]);
    expect(onUpdate.mock.calls.map(([next]) => next.sequence)).toEqual([11, 2]);
    close();
  });
});

function sseEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

async function waitForCondition(predicate: () => boolean, timeoutMs = 1_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) {
      throw new Error("Timed out waiting for asynchronous stream state.");
    }
    await new Promise((resolve) => globalThis.setTimeout(resolve, 10));
  }
}
