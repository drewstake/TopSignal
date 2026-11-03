import { afterEach, expect, it, vi } from "vitest";
import { OrderBookStore } from "./orderBook";

const contract = "CON.F.US.MNQ.Z26";
const timestamp = "2026-11-02T02:00:00Z";
const snapshot = { contract_id: contract, sequence: 1, timestamp,
  bids: [{ price: 100, size: 1 }], asks: [{ price: 101, size: 5 }] };
function createStore() {
  const store = new OrderBookStore(contract, "all", 100);
  store.setConnectionState({ contract_id: contract, state: "connected" });
  store.applySnapshot(snapshot);
  return store;
}
afterEach(() => vi.useRealTimers());

it("coalesces a burst into one ladder render while live sampling sees every applied delta immediately", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(timestamp));
  const store = createStore();
  const listener = vi.fn();
  store.subscribeTotals(listener);
  for (let sequence = 2; sequence <= 1001; sequence++) {
    expect(store.applyUpdateResult({ contract_id: contract, sequence, timestamp, side: "bid", price: 100, size: sequence })).toBe("applied");
  }
  expect(listener).not.toHaveBeenCalled();
  expect(store.getLiquiditySnapshot()?.bids).toEqual([[100, 1001]]);
  expect(store.getViewSnapshot().sequence).toBe(1001);
  vi.advanceTimersByTime(100);
  expect(listener).toHaveBeenCalledTimes(1);
  expect(store.getTotalsSnapshot().bidSize).toBe(1001);
  vi.advanceTimersByTime(1000);
  expect(listener).toHaveBeenCalledTimes(1);
});

it("batches ordinary snapshots and cancels queued work on disconnect or contract change", () => {
  vi.useFakeTimers();
  const store = createStore();
  const listener = vi.fn();
  store.subscribeTotals(listener);
  store.applySnapshot({ ...snapshot, sequence: 2, bids: [{ price: 100, size: 7 }] });
  store.applySnapshot({ ...snapshot, sequence: 3, bids: [{ price: 100, size: 9 }] });
  expect(listener).not.toHaveBeenCalled();
  vi.advanceTimersByTime(100);
  expect(listener).toHaveBeenCalledTimes(1);
  expect(store.getTotalsSnapshot().bidSize).toBe(9);
  store.applySnapshot({ ...snapshot, sequence: 4, bids: [{ price: 100, size: 11 }] });
  store.setConnectionState({ contract_id: contract, state: "disconnected" });
  vi.advanceTimersByTime(100);
  expect(listener).toHaveBeenCalledTimes(1);
  expect(store.getLiquiditySnapshot()).toBeNull();
  store.setConnectionState({ contract_id: contract, state: "connected" });
  store.applySnapshot({ ...snapshot, sequence: 5 });
  expect(store.getTotalsSnapshot().bidSize).toBe(1);
  store.applySnapshot({ ...snapshot, sequence: 6, bids: [{ price: 100, size: 99 }] });
  store.selectContract("OTHER");
  vi.advanceTimersByTime(100);
  expect(store.getTotalsSnapshot().bidSize).toBe(0);
});

it("detects a sequence gap immediately even while presentation is pending", () => {
  vi.useFakeTimers();
  const store = createStore();
  store.applyUpdateResult({ contract_id: contract, sequence: 2, timestamp, side: "bid", price: 100, size: 2 });
  expect(store.applyUpdateResult({ contract_id: contract, sequence: 4, timestamp, side: "bid", price: 100, size: 4 })).toBe("gap");
  expect(store.getMetaSnapshot().connection).toBe("reconnecting");
  expect(store.getLiquiditySnapshot()).toBeNull();
  vi.advanceTimersByTime(100);
  expect(store.getTotalsSnapshot().bidSize).toBe(1);
});
