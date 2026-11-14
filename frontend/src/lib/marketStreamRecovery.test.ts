// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { streamProjectXMarketDepth, streamProjectXMarketPrice, streamProjectXMarketTrades, type MarketPriceStreamHealth } from "./api";
import { setDemoModeEnabled } from "./demoMode";

vi.mock("./auth", () => ({ getAccessToken: vi.fn().mockResolvedValue(null) }));

const contractId = "CON.F.US.MNQ.Z26";
const encoder = new TextEncoder();
let close: (() => void) | undefined;

beforeEach(() => { vi.useFakeTimers(); setDemoModeEnabled(false); });
afterEach(() => {
  close?.();
  close = undefined;
  setDemoModeEnabled(false);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** An SSE response whose body stays open; frames can be pushed later. */
function openResponse(initial: string[] = []) {
  let push: (frame: string) => void = () => undefined;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      push = (frame) => controller.enqueue(encoder.encode(frame));
      for (const frame of initial) push(frame);
    },
  });
  return { response: new Response(body), push: (frame: string) => push(frame) };
}

const state = (value: string) => `event: state\ndata: ${JSON.stringify({ contract_id: contractId, state: value })}\n\n`;
const heartbeat = (idle = 800) => `event: heartbeat\ndata: ${JSON.stringify({
  contract_id: contractId, server_time: new Date(Date.now() + 1_500).toISOString(), state: "connected",
  upstream_idle_ms: idle, connected_for_ms: 60_000,
})}\n\n`;
const price = (value: number, timestamp = "2026-11-11T19:00:00Z") =>
  `event: price\ndata: ${JSON.stringify({ contract_id: contractId, symbol: "F.US.MNQ", price: value, timestamp })}\n\n`;

function latest(onHealth: ReturnType<typeof vi.fn>): MarketPriceStreamHealth {
  return onHealth.mock.calls.at(-1)?.[0] as MarketPriceStreamHealth;
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

it("reports upstream state, heartbeats and clock offset without treating quiet tape as a fault", async () => {
  const stream = openResponse([state("connected")]);
  const fetchMock = vi.fn().mockResolvedValueOnce(stream.response);
  vi.stubGlobal("fetch", fetchMock);
  const onHealth = vi.fn();
  const onPrice = vi.fn();
  close = streamProjectXMarketPrice({ contractId }, { onPrice, onHealth });
  await vi.advanceTimersByTimeAsync(0);
  expect(latest(onHealth)).toMatchObject({ transport: "open", upstream: "connected", heartbeats: false });

  // No trades for a full minute, but heartbeats keep arriving: still one connection.
  for (let index = 0; index < 12; index += 1) {
    stream.push(heartbeat());
    await vi.advanceTimersByTimeAsync(5_000);
  }
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(onPrice).not.toHaveBeenCalled();
  const health = latest(onHealth);
  expect(health).toMatchObject({ transport: "open", upstream: "connected", heartbeats: true, upstreamIdleMs: 800, reconnects: 0 });
  expect(health.serverClockOffsetMs).toBeGreaterThan(1_000);
  expect(Date.now() - (health.lastActivityAt ?? 0)).toBeLessThanOrEqual(5_000);
});

it("reconnects a silent half-open stream once heartbeats stop, without duplicate connections", async () => {
  const first = openResponse([state("connected"), heartbeat()]);
  const second = openResponse([state("connected")]);
  const fetchMock = vi.fn().mockResolvedValueOnce(first.response).mockResolvedValueOnce(second.response);
  vi.stubGlobal("fetch", fetchMock);
  const onHealth = vi.fn();
  const onError = vi.fn();
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onHealth, onError });
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(14_000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1_500);  // 15s without bytes after a heartbeat-capable connection
  expect(onError.mock.calls.at(-1)?.[0].name).toBe("StreamIdleTimeoutError");
  expect(latest(onHealth)).toMatchObject({ transport: "retrying", lastInterruption: "idle_timeout", reconnects: 1 });
  await vi.advanceTimersByTimeAsync(1_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(latest(onHealth)).toMatchObject({ transport: "open", upstream: "connected" });
});

it("keeps a legacy keepalive-only stream open and gives it a longer idle allowance", async () => {
  const stream = openResponse([state("connected")]);
  const fetchMock = vi.fn().mockResolvedValueOnce(stream.response).mockResolvedValue(openResponse().response);
  vi.stubGlobal("fetch", fetchMock);
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onHealth: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  for (let index = 0; index < 4; index += 1) {
    await vi.advanceTimersByTimeAsync(15_000);
    stream.push(": keepalive\n\n");
  }
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(35_000);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("retries immediately when the network returns instead of waiting out the backoff", async () => {
  const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
  vi.stubGlobal("fetch", fetchMock);
  const onHealth = vi.fn();
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onHealth, onError: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(1_000 + 2_000 + 4_000);
  expect(fetchMock).toHaveBeenCalledTimes(4);  // Now waiting 8s.
  expect(latest(onHealth)).toMatchObject({ transport: "retrying", lastInterruption: "network_error" });
  fetchMock.mockResolvedValueOnce(openResponse([state("connected")]).response);
  window.dispatchEvent(new Event("online"));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetchMock).toHaveBeenCalledTimes(5);
  expect(latest(onHealth)).toMatchObject({ transport: "open", upstream: "connected", reconnects: 4 });
  // A second wake while healthy and recently active does not reconnect.
  window.dispatchEvent(new Event("online"));
  await vi.advanceTimersByTimeAsync(0);
  expect(fetchMock).toHaveBeenCalledTimes(5);
});

it("checks a quiet connection immediately when the page returns from sleep", async () => {
  const sleeping = openResponse([state("connected")]);
  const fetchMock = vi.fn().mockResolvedValueOnce(sleeping.response).mockResolvedValueOnce(openResponse([state("connected")]).response);
  vi.stubGlobal("fetch", fetchMock);
  const onHealth = vi.fn();
  close = streamProjectXMarketPrice({ contractId }, { onPrice: vi.fn(), onHealth, onError: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  setHidden(true);
  await vi.advanceTimersByTimeAsync(13_000);  // Below the legacy watchdog, above the wake threshold.
  setHidden(false);
  await vi.advanceTimersByTimeAsync(0);
  expect(latest(onHealth)).toMatchObject({ transport: "retrying", lastInterruption: "stale_after_wake" });
  await vi.advanceTimersByTimeAsync(1_000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(latest(onHealth)).toMatchObject({ transport: "open" });
  setHidden(false);
});

it("delivers the backend's replayed last trade with its original provider timestamp", async () => {
  const stream = openResponse([state("connected"), price(20_100.25, "2026-11-11T18:59:40Z")]);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(stream.response));
  const onPrice = vi.fn();
  close = streamProjectXMarketPrice({ contractId }, { onPrice, onHealth: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  expect(onPrice).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ price: 20_100.25, timestamp: "2026-11-11T18:59:40Z" }));
});

it("reconnects a silent depth stream and a silent trade-print stream", async () => {
  const depthFetch = vi.fn().mockImplementation(() => Promise.resolve(openResponse().response));
  vi.stubGlobal("fetch", depthFetch);
  const onState = vi.fn();
  close = streamProjectXMarketDepth({ contractId }, { onState, onSnapshot: vi.fn(), onUpdate: vi.fn() });
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(35_000);
  expect(onState.mock.calls.some(([value]) => value.state === "disconnected")).toBe(true);
  await vi.advanceTimersByTimeAsync(1_000);
  expect(depthFetch).toHaveBeenCalledTimes(2);
  close();

  const tradesFetch = vi.fn().mockImplementation(() => Promise.resolve(openResponse().response));
  vi.stubGlobal("fetch", tradesFetch);
  const onTradesState = vi.fn();
  close = streamProjectXMarketTrades(contractId, { onTrades: vi.fn(), onState: onTradesState });
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(35_000);
  expect(onTradesState.mock.calls.some(([value]) => value.state === "disconnected")).toBe(true);
  await vi.advanceTimersByTimeAsync(600);
  expect(tradesFetch).toHaveBeenCalledTimes(2);
});
