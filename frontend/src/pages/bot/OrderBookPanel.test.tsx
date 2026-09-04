// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MarketDepthStreamCallbacks } from "../../lib/api";
import { OrderBookPanel } from "./OrderBookPanel";
import { OrderBookStore } from "./orderBook";
import { connectOrderBookPanelStream, type MarketDepthStreamFactory } from "./orderBookPanelStream";

afterEach(cleanup);

describe("OrderBookPanel", () => {
  it("shows a stable market-closed state, clears prices, and resumes on the same stream", async () => {
    const contractId = "CON.F.US.MNQ.U26";
    let callbacks: MarketDepthStreamCallbacks | undefined;
    const close = vi.fn();
    const streamFactory = vi.fn<MarketDepthStreamFactory>((_query, next) => {
      callbacks = next;
      return close;
    });
    const { unmount } = render(<OrderBookPanel contractId={contractId} streamFactory={streamFactory} />);
    const snapshot = {
      contract_id: contractId, sequence: 1, timestamp: "2026-09-04T20:59:59Z",
      bids: [{ price: 20000, size: 5 }], asks: [{ price: 20001, size: 6 }],
    };
    act(() => {
      callbacks?.onState({ contract_id: contractId, state: "connected" });
      callbacks?.onSnapshot(snapshot);
    });
    expect(screen.getByLabelText("Best bid 20,000.00, aggregate size 5")).toBeDefined();
    act(() => {
      callbacks?.onState({ contract_id: contractId, state: "market_closed" });
      callbacks?.onSnapshot(snapshot); // Late data must not repopulate a closed book.
      callbacks?.onUpdate({
        contract_id: contractId, sequence: 999, timestamp: snapshot.timestamp,
        side: "bid", price: 20000, size: 99,
      });
    });
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText("Market closed")).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain("resume automatically");
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBeNull();
    expect(screen.queryByLabelText("Best bid 20,000.00, aggregate size 5")).toBeNull();
    expect(screen.queryByText("Reconnecting")).toBeNull();
    expect(streamFactory).toHaveBeenCalledTimes(1);
    expect(close).not.toHaveBeenCalled();
    act(() => {
      callbacks?.onState({ contract_id: contractId, state: "reconnecting" });
      callbacks?.onState({ contract_id: contractId, state: "connected" });
      callbacks?.onSnapshot({ ...snapshot, sequence: 2, bids: [], asks: [], reset: true });
      callbacks?.onUpdate({
        contract_id: contractId, sequence: 3, timestamp: "2026-09-06T22:00:00Z",
        side: "bid", price: 20000, size: 7,
      });
    });
    expect(screen.getByText("Connected")).toBeDefined();
    expect(screen.getByLabelText("Best bid 20,000.00, aggregate size 7")).toBeDefined();
    expect(screen.queryByText("Market closed")).toBeNull();
    expect(streamFactory).toHaveBeenCalledTimes(1);
    unmount();
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("renders aggregate depth, spread, state, and the supported level counts without trading controls", () => {
    const markup = renderToStaticMarkup(
      <OrderBookPanel contractId="CON.F.US.MNQ.U26" symbol="MNQ" />,
    );

    expect(markup).toContain("Order Book");
    expect(markup).toContain("MNQ · CON.F.US.MNQ.U26 · aggregate size by price");
    expect(markup).toContain("Level 1 shows best bid/ask; Level 2 supplies market depth.");
    expect(markup).toContain("Loading");
    expect(markup).toContain("Loading order book…");
    expect(markup).toContain("Ask price");
    expect(markup).toContain("Bid price");
    expect(markup).toContain("Spread");
    expect(markup).toContain('<option value="10">10</option>');
    expect(markup).toContain('<option value="20" selected="">20</option>');
    expect(markup).toContain('<option value="50">50</option>');
    expect(markup).not.toContain("Buy");
    expect(markup).not.toContain("Sell");
  });

  it("renders an explicit unavailable state when no bot contract is selected", () => {
    const markup = renderToStaticMarkup(<OrderBookPanel contractId={null} />);

    expect(markup).toContain("Unavailable");
    expect(markup).toContain("Select a saved bot to view market depth.");
    expect(markup).toContain('role="alert"');
  });

  it("replaces live depth controls with an explicit Demo Mode snapshot state", () => {
    const markup = renderToStaticMarkup(
      <OrderBookPanel contractId="CON.F.US.MNQ.U26" symbol="MNQ" demoMode />,
    );

    expect(markup).toContain("Demo · stream off");
    expect(markup).toContain("Live market depth is paused in Demo Mode");
    expect(markup).not.toContain("Loading order book");
    expect(markup).not.toContain("Visible order book levels per side");
  });

  it("restarts a custom stream factory after the store detects a sequence gap", async () => {
    const contractId = "CON.F.US.MNQ.U26";
    const store = new OrderBookStore(contractId);
    const callbackSets: MarketDepthStreamCallbacks[] = [];
    const closeFunctions = [vi.fn(), vi.fn()];
    const streamFactory = vi.fn<MarketDepthStreamFactory>((_query, callbacks) => {
      const index = callbackSets.length;
      callbackSets.push(callbacks);
      return closeFunctions[index] ?? vi.fn();
    });
    const stop = connectOrderBookPanelStream({ contractId, store, streamFactory });
    const first = callbackSets[0];
    expect(first).toBeDefined();

    first?.onState({ contract_id: contractId, state: "connected" });
    first?.onSnapshot({
      contract_id: contractId,
      sequence: 10,
      timestamp: "2026-07-10T14:30:00Z",
      asks: [{ price: 101, size: 5 }],
      bids: [{ price: 100, size: 6 }],
    });
    first?.onUpdate({
      contract_id: contractId,
      sequence: 12,
      timestamp: "2026-07-10T14:30:01Z",
      side: "bid",
      price: 100,
      size: 99,
    });
    await Promise.resolve();

    expect(store.getMetaSnapshot().connection).toBe("reconnecting");
    expect(store.getBidSlotSnapshot(0)?.size).toBe(6);
    expect(closeFunctions[0]).toHaveBeenCalledTimes(1);
    expect(streamFactory).toHaveBeenCalledTimes(2);

    // The invalidated stream cannot overwrite the new generation.
    first?.onSnapshot({
      contract_id: contractId,
      sequence: 99,
      timestamp: "2026-07-10T14:30:02Z",
      asks: [],
      bids: [{ price: 100, size: 500 }],
    });
    expect(store.getBidSlotSnapshot(0)?.size).toBe(6);

    const second = callbackSets[1];
    second?.onState({ contract_id: contractId, state: "connected" });
    second?.onSnapshot({
      contract_id: contractId,
      sequence: 1,
      timestamp: "2026-07-10T14:30:03Z",
      asks: [{ price: 101, size: 7 }],
      bids: [{ price: 100, size: 8 }],
      reset: true,
    });
    second?.onUpdate({
      contract_id: contractId,
      sequence: 2,
      timestamp: "2026-07-10T14:30:04Z",
      side: "bid",
      price: 100,
      size: 9,
    });
    expect(store.getBidSlotSnapshot(0)?.size).toBe(9);

    stop();
    expect(closeFunctions[1]).toHaveBeenCalledTimes(1);
  });
});
