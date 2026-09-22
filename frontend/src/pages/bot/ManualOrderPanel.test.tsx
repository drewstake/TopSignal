// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, botsApi } from "../../lib/api";
import type { ManualOrderState } from "../../lib/manualOrderTypes";
import { ManualOrderPanel } from "./ManualOrderPanel";

const snapshot: ManualOrderState = { account_id: 101, contract: { id: "CON.F.US.MNQ.Z26", name: "MNQZ6", tick_size: .25, tick_value: .5 },
  max_stop_risk: 250, positions: [], orders: [], recent_attempts: [] };
const props = { accountId: 101, accountName: "Practice", disabledReason: null, demoMode: false, onFlatten: vi.fn(), flattening: false };
beforeEach(() => {
  vi.spyOn(botsApi, "getManualOrderState").mockResolvedValue(snapshot);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

async function ready() {
  const buy = await screen.findByRole("button", { name: "Buy market + SL/TP" });
  await waitFor(() => expect((buy as HTMLButtonElement).disabled).toBe(false));
  return buy;
}

describe("manual broker test panel", () => {
  it.each(["Buy", "Sell"])("sends exactly one confirmed %s entry with the selected brackets", async (side) => {
    let resolve!: (value: never) => void;
    const send = vi.spyOn(botsApi, "submitManualOrder").mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<ManualOrderPanel {...props} />);
    await ready();
    fireEvent.change(screen.getByLabelText("Test contracts"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Test stop loss ticks"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Test take profit ticks"), { target: { value: "24" } });
    const button = screen.getByRole("button", { name: `${side} market + SL/TP` });
    fireEvent.click(button); fireEvent.click(button);
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]).toEqual([101, expect.objectContaining({ side: side.toUpperCase(), quantity: 2,
      stop_loss_ticks: 12, take_profit_ticks: 24, confirm_live_order_routing: true, request_id: expect.any(String) })]);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("Practice (101)"));
    await act(async () => { resolve({ ...send.mock.calls[0][1], account_id: 101, attempt_id: 1,
      contract_id: snapshot.contract.id, status: "submitted", provider_order_id: "123", message: null } as never); });
    expect(await screen.findByText(/TopstepX order 123/)).not.toBeNull();
  });

  it("does not send if confirmation is cancelled", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const send = vi.spyOn(botsApi, "submitManualOrder");
    render(<ManualOrderPanel {...props} />);
    fireEvent.click(await ready());
    expect(send).not.toHaveBeenCalled();
  });

  it.each([['take_profit_hit', 'Take profit hit'], ['stop_loss_hit', 'Stop loss hit']])(
    "replaces the submission receipt with %s on broker refresh", async (executionStatus, label) => {
      let submitted: ManualOrderState['recent_attempts'][number];
      vi.spyOn(botsApi, "submitManualOrder").mockImplementation(async (_id, input) => {
        submitted = { ...input, account_id: 101, attempt_id: 1, contract_id: snapshot.contract.id,
          status: "submitted", provider_order_id: "123", message: null };
        return submitted;
      });
      render(<ManualOrderPanel {...props} />);
      fireEvent.click(await ready());
      await screen.findByText(/fill not yet confirmed/);
      vi.mocked(botsApi.getManualOrderState).mockResolvedValue({ ...snapshot, recent_attempts: [
        { ...submitted!, execution_status: executionStatus, entry_fill_price: 24000, exit_fill_price: 24010 },
      ] });
      fireEvent.click(screen.getByRole('button', { name: 'Refresh broker status' }));
      await waitFor(() => expect(screen.getByRole('status').textContent).toContain(`${label} · trade closed`));
      expect(screen.getByRole('status').textContent).toContain('Entry 24000 · Exit 24010');
      expect(screen.queryByText(/fill not yet confirmed/)).toBeNull();
      expect(botsApi.submitManualOrder).toHaveBeenCalledTimes(1);
    });

  it("shows a broker rejection without claiming the order was accepted", async () => {
    vi.spyOn(botsApi, "submitManualOrder").mockImplementation(async (_accountId, input) => ({
      ...input, account_id: 101, attempt_id: 1, contract_id: snapshot.contract.id,
      status: "error", provider_order_id: null, message: "Invalid take profit ticks (40).",
    }));
    render(<ManualOrderPanel {...props} />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Sell market + SL/TP" }));
    expect((await screen.findByRole("status")).textContent).toContain("SELL 1 · Rejected");
    expect(screen.getByRole("status").textContent).toContain("Invalid take profit ticks");
    expect(screen.queryByText(/accepted/i)).toBeNull();
  });

  it("blocks invalid quantities and excessive stop risk", async () => {
    render(<ManualOrderPanel {...props} />);
    const buy = await ready();
    for (const value of ["0", "1.5", "11"]) {
      fireEvent.change(screen.getByLabelText("Test contracts"), { target: { value } });
      expect((buy as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.change(screen.getByLabelText("Test contracts"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Test stop loss ticks"), { target: { value: "100" } });
    expect((buy as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("Reduce contracts");
  });

  it("recovers an uncertain response with the identical request ID and settings", async () => {
    const send = vi.spyOn(botsApi, "submitManualOrder").mockRejectedValue(new Error("Connection lost"));
    render(<ManualOrderPanel {...props} />);
    fireEvent.click(await ready());
    const recover = await screen.findByRole("button", { name: "Recover same submission" });
    expect((screen.getByRole("button", { name: "Buy market + SL/TP" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(recover);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1]).toEqual(send.mock.calls[0]);
  });

  it("lets the user correct settings after a definite preflight rejection", async () => {
    vi.spyOn(botsApi, "submitManualOrder").mockRejectedValue(new ApiError("Stop automation first", 409, null, null));
    render(<ManualOrderPanel {...props} />);
    fireEvent.click(await ready());
    await screen.findByText("Stop automation first");
    expect(screen.queryByRole("button", { name: "Recover same submission" })).toBeNull();
    expect((screen.getByLabelText("Test contracts") as HTMLInputElement).disabled).toBe(false);
  });

  it("shows actual bracket prices and blocks entries when the account has exposure", async () => {
    vi.mocked(botsApi.getManualOrderState).mockResolvedValue({ ...snapshot,
      positions: [{ id: "p", contract_id: snapshot.contract.id, type: 1, size: 1, average_price: 24000 }],
      orders: [{ order_id: "sl", contract_id: snapshot.contract.id, order_type: 4, side: 1, size: 1,
        stop_price: 23995, limit_price: null, parent_order_id: "entry" }] });
    render(<ManualOrderPanel {...props} />);
    await screen.findByText(/average fill 24000/);
    expect(screen.getByText(/Stop 23995/)).not.toBeNull();
    expect((screen.getByRole("button", { name: "Buy market + SL/TP" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Close positions & cancel orders" }));
    expect(props.onFlatten).toHaveBeenCalled();
  });

  it("does not access the broker in Demo Mode", () => {
    render(<ManualOrderPanel {...props} demoMode />);
    expect(botsApi.getManualOrderState).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Buy market + SL/TP" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not show the old account's delayed submission after switching accounts", async () => {
    let resolve!: (value: never) => void;
    const send = vi.spyOn(botsApi, "submitManualOrder").mockReturnValue(new Promise((r) => { resolve = r; }));
    const view = render(<ManualOrderPanel key={101} {...props} />);
    fireEvent.click(await ready());
    vi.mocked(botsApi.getManualOrderState).mockResolvedValue({ ...snapshot, account_id: 102 });
    view.rerender(<ManualOrderPanel key={102} {...props} accountId={102} accountName="Other Practice" />);
    await ready();
    await act(async () => { resolve({ ...send.mock.calls[0][1], account_id: 101, attempt_id: 1,
      contract_id: snapshot.contract.id, status: "submitted", provider_order_id: "old-account-order", message: null } as never); });
    expect(screen.queryByText(/old-account-order/)).toBeNull();
    expect(screen.getByText(/Other Practice \(102\)/)).not.toBeNull();
  });
});
