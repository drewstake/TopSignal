// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { streamProjectXMarketTrades } from "./api";
import { parseMarketTradeBatch } from "./marketTradeTypes";
import { setDemoModeEnabled } from "./demoMode";

vi.mock("./supabase", () => ({ getAccessToken: vi.fn().mockResolvedValue("test-token") }));
const contract = "MNQ";
const batch = { contract_id: contract, dropped: 0, trades: [{ id: "1", timestamp: "2026-11-03T15:00:00Z", price: 20000, volume: 5, side: "buy" }] };
let close: (() => void) | undefined;
beforeEach(() => { vi.useFakeTimers(); setDemoModeEnabled(false); });
afterEach(() => { close?.(); close = undefined; setDemoModeEnabled(false); vi.useRealTimers(); vi.unstubAllGlobals(); });

function response(batches: unknown[], eof: boolean) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) {
    const wire = batches.map(value => `event: trades\r\ndata: ${JSON.stringify(value)}\r\n\r\n`).join("");
    // Deliberately split CRLF and JSON across chunks.
    for (const char of wire) controller.enqueue(new TextEncoder().encode(char));
    if (eof) controller.close();
  } }));
}

it("authenticates, handles fragmented SSE, filters contracts and reconnects after EOF", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response([{ ...batch, contract_id: "MES" }, batch], true))
    .mockResolvedValueOnce(response([batch], false));
  vi.stubGlobal("fetch", fetch);
  const onTrades = vi.fn(), onState = vi.fn();
  close = streamProjectXMarketTrades(contract, { onTrades, onState });
  await vi.advanceTimersByTimeAsync(0);
  expect(onTrades).toHaveBeenCalledExactlyOnceWith(batch);
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe("Bearer test-token");
  expect(onState.mock.calls.some(([value]) => value.state === "disconnected")).toBe(true);
  await vi.advanceTimersByTimeAsync(500);
  expect(fetch).toHaveBeenCalledTimes(2);
  close();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("stops retries in Demo mode and never connects when already in Demo mode", async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError("Offline"));
  vi.stubGlobal("fetch", fetch);
  close = streamProjectXMarketTrades(contract, { onTrades: vi.fn(), onState: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  setDemoModeEnabled(true);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(fetch).toHaveBeenCalledTimes(1);
  close();
  close = streamProjectXMarketTrades(contract, { onTrades: vi.fn(), onState: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("reports a missing endpoint without endlessly retrying", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("Unavailable", { status: 404 }));
  vi.stubGlobal("fetch", fetch);
  const onState = vi.fn();
  close = streamProjectXMarketTrades(contract, { onTrades: vi.fn(), onState });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ state: "unavailable" }));
});

it("keeps unknown sides gray and flags malformed prints instead of inventing values", () => {
  expect(parseMarketTradeBatch({ ...batch, trades: [{ ...batch.trades[0], side: 0 }, { ...batch.trades[0], price: "20000" }] }))
    .toMatchObject({ dropped: 1, trades: [{ side: "unknown", volume: 5 }] });
  expect(parseMarketTradeBatch({ ...batch, trades: Array(257).fill(batch.trades[0]) })).toBeNull();
});
