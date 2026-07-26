import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { MarketDepthStreamCallbacks } from "../../lib/api";
import { OrderBookPanel } from "./OrderBookPanel";
import { OrderBookStore } from "./orderBook";
import { connectOrderBookPanelStream, type MarketDepthStreamFactory } from "./orderBookPanelStream";

describe("OrderBookPanel", () => {
  it("renders aggregate depth, spread, state, and the supported level counts without trading controls", () => {
    const markup = renderToStaticMarkup(
      <OrderBookPanel contractId="CON.F.US.MNQ.U26" symbol="MNQ" />,
    );

    expect(markup).toContain("Order Book");
    expect(markup).toContain("MNQ · CON.F.US.MNQ.U26 · aggregate size by price");
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
