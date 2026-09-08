// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { streamProjectXMarketPrice } from "./api";
import { setDemoModeEnabled } from "./demoMode";

vi.mock("./supabase", () => ({ getAccessToken: vi.fn().mockResolvedValue(null) }));

const contractId = "CON.F.US.MNQ.U26";
const price = { contract_id: contractId, symbol: "F.US.MNQ", price: 20000.25, timestamp: "2026-09-08T14:00:00Z" };
const encoder = new TextEncoder();
let close: (() => void) | undefined;
beforeEach(() => { vi.useFakeTimers(); setDemoModeEnabled(false); });
afterEach(() => { close?.(); close = undefined; setDemoModeEnabled(false); vi.useRealTimers(); vi.unstubAllGlobals(); });

function responseWithPrices(prices: unknown[], eof = true) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    for (const value of prices) controller.enqueue(encoder.encode(`event: price\ndata: ${JSON.stringify(value)}\n\n`));
    if (eof) controller.close();
  } }));
}

it("reconnects after EOF and forwards live prices only for the exact selected expiry", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(responseWithPrices([{ ...price, contract_id: "CON.F.US.MNQ.Z26" }, price]))
    .mockResolvedValueOnce(responseWithPrices([{ ...price, price: 20001 }], false));
  vi.stubGlobal("fetch", fetchMock);
  const onPrice = vi.fn();
  const onError = vi.fn();
  close = streamProjectXMarketPrice({ contractId, symbol: "F.US.MNQ" }, { onPrice, onError });
  await vi.advanceTimersByTimeAsync(0);
  expect(onError.mock.calls.map(([error]) => error.message)).toEqual(["Market price stream disconnected."]);
  expect(onPrice).toHaveBeenCalledExactlyOnceWith(price);
  expect(onError).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(onPrice).toHaveBeenLastCalledWith({ ...price, price: 20001 });
});

it("retries a backend restart with bounded backoff and cancels pending retries on close", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
  vi.stubGlobal("fetch", fetchMock);
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onError: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(999);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(2_000);
  expect(fetchMock).toHaveBeenCalledTimes(3);
  close();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it("stops reconnecting when Demo mode activates", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new TypeError("Disconnected"));
  vi.stubGlobal("fetch", fetchMock);
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onError: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  setDemoModeEnabled(true);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("does not retry an authentication rejection", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("Sign in required", { status: 401 }));
  vi.stubGlobal("fetch", fetchMock);
  const onError = vi.fn();
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onError });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(onError).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
