// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MarketDepthStreamCallbacks } from "../../lib/api";
import { OrderBookPanel } from "./OrderBookPanel";
import { OrderBookStore } from "./orderBook";
import { connectOrderBookPanelStream, type MarketDepthStreamFactory } from "./orderBookPanelStream";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("OrderBookPanel", () => {
  it("closes the depth stream while hidden and starts a fresh subscription on return", () => {
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
    const close = vi.fn();
    const streamFactory = vi.fn<MarketDepthStreamFactory>(() => close);
    render(<OrderBookPanel contractId="CON.F.US.MNQ.U26" streamFactory={streamFactory} />);
    expect(streamFactory).toHaveBeenCalledTimes(1);
    act(() => {
      streamFactory.mock.calls[0][1].onSnapshot({ contract_id: "CON.F.US.MNQ.U26", sequence: 1,
        timestamp: "2026-09-08T14:00:00Z", bids: [{ price: 20000, size: 5 }], asks: [] });
    });
    act(() => { hidden.mockReturnValue(true); document.dispatchEvent(new Event("visibilitychange")); });
    expect(close).toHaveBeenCalledTimes(1);
    expect(streamFactory).toHaveBeenCalledTimes(1);
    act(() => { hidden.mockReturnValue(false); document.dispatchEvent(new Event("visibilitychange")); });
    expect(streamFactory).toHaveBeenCalledTimes(2);
    act(() => {
      streamFactory.mock.calls[1][1].onUpdate({ contract_id: "CON.F.US.MNQ.U26", sequence: 2,
        timestamp: "2026-09-08T14:01:00Z", side: "bid", price: 20000, size: 99 });
    });
    expect(screen.queryByLabelText("Best bid 20,000.00, aggregate size 99")).toBeNull();
  });

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

  it("renders a full-depth ladder with a level selector, spread, and state", () => {
    const markup = renderToStaticMarkup(
      <OrderBookPanel contractId="CON.F.US.MNQ.U26" symbol="MNQ" />,
    );

    expect(markup).toContain("Order Book");
    expect(markup).toContain('title="MNQ · CON.F.US.MNQ.U26 · Level 2 market depth"');
    expect(markup).toContain("Size in contracts");
    expect(markup).toContain("All received price levels");
    expect(markup).toContain("Loading");
    expect(markup).toContain("Loading order book…");
    expect(markup).toContain("Bids");
    expect(markup).toContain("Asks");
    expect(markup).toContain("Spread");
    expect(markup).toContain("<select");
    expect(markup).toContain("Levels per side");
    expect(markup).not.toContain("Buy");
    expect(markup).not.toContain("Sell");
  });

  it("displays every received level beyond 50 and changes the view without reopening the stream", () => {
    const contractId = "CON.F.US.MNQ.Z26";
    const streamFactory = vi.fn<MarketDepthStreamFactory>(() => vi.fn());
    const { container } = render(<OrderBookPanel contractId={contractId} streamFactory={streamFactory} />);
    const callbacks = streamFactory.mock.calls[0][1];
    act(() => {
      callbacks.onState({ contract_id: contractId, state: "connected" });
      callbacks.onSnapshot({ contract_id: contractId, sequence: 1, timestamp: "2026-10-30T12:00:00Z",
        bids: Array.from({ length: 75 }, (_, i) => ({ price: 20000 - i * 0.25, size: i + 1 })).reverse(),
        asks: Array.from({ length: 80 }, (_, i) => ({ price: 20000.25 + i * 0.25, size: i + 1 })).reverse(),
      });
    });
    const prices = (side: string) => Array.from(container.querySelectorAll(`[data-order-book-side="${side}"] [data-price]`))
      .map((node) => Number(node.getAttribute("data-price")));
    expect(prices("bids")).toHaveLength(75);
    expect(prices("asks")).toHaveLength(80);
    expect(prices("bids").slice(0, 2)).toEqual([20000, 19999.75]);
    expect(prices("asks").slice(0, 2)).toEqual([20000.25, 20000.5]);
    expect(screen.getByLabelText("bid 19,981.50, aggregate size 75")).toBeDefined();
    expect(screen.getByLabelText("Spread 0.25")).toBeDefined();
    const bidTotal = () => screen.getByLabelText("Bid contracts across displayed levels").textContent;
    const askTotal = () => screen.getByLabelText("Ask contracts across displayed levels").textContent;
    expect(bidTotal()).toBe("· 2,850 contracts");
    expect(askTotal()).toBe("· 3,240 contracts");

    const selector = screen.getByRole("combobox", { name: "Visible order book levels per side" });
    for (const count of [10, 20, 50]) {
      fireEvent.change(selector, { target: { value: String(count) } });
      expect(prices("bids")).toHaveLength(count);
      expect(prices("asks")).toHaveLength(count);
      expect(screen.getByText(`(${count} of 75)`)).toBeDefined();
      expect(bidTotal()).toBe(`· ${(count * (count + 1) / 2).toLocaleString("en-US")} contracts`);
      expect(askTotal()).toBe(bidTotal());
    }
    act(() => {
      callbacks.onUpdate({ contract_id: contractId, sequence: 2, timestamp: "2026-10-30T12:00:01Z",
        side: "bid", price: 19981.5, size: 99 });
    });
    fireEvent.change(selector, { target: { value: "all" } });
    expect(prices("bids")).toHaveLength(75);
    expect(screen.getByLabelText("bid 19,981.50, aggregate size 99")).toBeDefined();
    expect(bidTotal()).toBe("· 2,874 contracts");
    act(() => {
      callbacks.onUpdate({ contract_id: contractId, sequence: null, timestamp: "2026-10-30T12:00:01Z",
        side: "bid", price: 20000, size: 8 });
    });
    expect(bidTotal()).toBe("· 2,881 contracts");
    expect(streamFactory).toHaveBeenCalledTimes(1);

    act(() => {
      callbacks.onUpdate({ contract_id: contractId, sequence: 3, timestamp: "2026-10-30T12:00:02Z",
        side: "ask", price: 20020.25, size: 11 });
      callbacks.onUpdate({ contract_id: contractId, sequence: 4, timestamp: "2026-10-30T12:00:03Z",
        side: "bid", price: 19981.5, size: 0 });
    });
    expect(prices("asks")).toHaveLength(81);
    expect(screen.getByLabelText("ask 20,020.25, aggregate size 11")).toBeDefined();
    expect(prices("bids")).toHaveLength(74);
    expect(screen.queryByLabelText("bid 19,981.50, aggregate size 99")).toBeNull();
    expect(bidTotal()).toBe("· 2,782 contracts");
    expect(askTotal()).toBe("· 3,251 contracts");

    act(() => {
      callbacks.onState({ contract_id: contractId, state: "reconnecting" });
    });
    expect(screen.getByText(/Showing last received depth/)).toBeDefined();
    act(() => {
      callbacks.onSnapshot({ contract_id: contractId, sequence: 1, timestamp: null, bids: [], asks: [], reset: true });
      callbacks.onState({ contract_id: contractId, state: "connected" });
      callbacks.onUpdate({ contract_id: contractId, sequence: 2, timestamp: "2026-10-30T12:00:04Z",
        side: "bid", price: 20000, size: 3 });
    });
    expect(prices("asks")).toEqual([]);
    expect(prices("bids")).toEqual([20000]);
    expect(screen.getByLabelText("Best bid 20,000.00, aggregate size 3")).toBeDefined();
    expect(bidTotal()).toBe("· 3 contracts");
    expect(askTotal()).toBe("· 0 contracts");
    expect(screen.queryByText(/Showing last received depth/)).toBeNull();
  });

  it("clears the full ladder on contract change and ignores callbacks from the old stream", () => {
    const streamFactory = vi.fn<MarketDepthStreamFactory>(() => vi.fn());
    const { container, rerender } = render(<OrderBookPanel contractId="MNQ" streamFactory={streamFactory} />);
    const previous = streamFactory.mock.calls[0][1];
    act(() => {
      previous.onSnapshot({ contract_id: "MNQ", sequence: 1, timestamp: null,
        bids: Array.from({ length: 75 }, (_, i) => ({ price: 20000 - i, size: 1 })), asks: [] });
    });
    expect(container.querySelectorAll("[data-price]")).toHaveLength(75);
    rerender(<OrderBookPanel contractId="NQ" streamFactory={streamFactory} />);
    act(() => {
      previous.onSnapshot({ contract_id: "MNQ", sequence: 2, timestamp: null,
        bids: [{ price: 20000, size: 99 }], asks: [] });
    });
    expect(container.querySelectorAll("[data-price]")).toHaveLength(0);
    expect(streamFactory).toHaveBeenCalledTimes(2);
  });

  it("starts with all levels when returning from Demo Mode", () => {
    const contractId = "MNQ";
    const streamFactory = vi.fn<MarketDepthStreamFactory>(() => vi.fn());
    const { container, rerender } = render(<OrderBookPanel contractId={contractId} streamFactory={streamFactory} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "10" } });
    rerender(<OrderBookPanel contractId={contractId} streamFactory={streamFactory} demoMode />);
    expect(screen.queryByRole("combobox")).toBeNull();
    rerender(<OrderBookPanel contractId={contractId} streamFactory={streamFactory} />);
    act(() => {
      streamFactory.mock.calls[1][1].onSnapshot({ contract_id: contractId, sequence: 1, timestamp: null,
        bids: Array.from({ length: 75 }, (_, i) => ({ price: 20000 - i, size: 1 })), asks: [] });
    });
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("all");
    expect(container.querySelectorAll("[data-price]")).toHaveLength(75);
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
