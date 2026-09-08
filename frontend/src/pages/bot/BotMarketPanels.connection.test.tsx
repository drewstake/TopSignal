// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "../../lib/api";
import { BotSignalChart } from "./BotSignalChart";
import { BotMarketPanels } from "./BotMarketPanels";

vi.mock("./BotSignalChart", () => ({ BotSignalChart: vi.fn(() => <div>Market chart</div>) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.clearAllMocks(); });

it("streams the resolved chart contract without an account and disconnects in Demo mode", async () => {
  const contractId = "CON.F.US.MNQ.U26";
  vi.spyOn(api, "getAuthenticatedCacheScope").mockResolvedValue("user:viewer");
  vi.spyOn(api.botsApi, "searchContracts").mockResolvedValue([{ id: contractId, name: "MNQU6", symbol_id: "F.US.MNQ",
    description: null, tick_size: 0.25, tick_value: 0.5, active_contract: true }]);
  let events: api.MarketDepthStreamCallbacks | undefined;
  const close = vi.fn();
  const stream = vi.spyOn(api, "streamProjectXMarketDepth").mockImplementation((_query, callbacks) => {
    events = callbacks;
    return close;
  });
  const start = vi.spyOn(api.botsApi, "startTopBot");
  const evaluate = vi.spyOn(api.botsApi, "evaluate");
  const view = render(<BotMarketPanels bot={null} authenticatedCacheScope={null} activity={null} evaluation={null}
    refreshToken={0} demoMode={false} evaluating={false} />);
  await screen.findByText("Market chart");
  await waitFor(() => expect(stream).toHaveBeenCalledExactlyOnceWith({ contractId }, expect.any(Object)));
  act(() => {
    events?.onState({ contract_id: contractId, state: "connected" });
    events?.onSnapshot({ contract_id: contractId, sequence: 1, timestamp: new Date().toISOString(),
      bids: [{ price: 20000, size: 5 }], asks: [{ price: 20001, size: 6 }] });
  });
  expect(screen.getByLabelText("Best bid 20,000.00, aggregate size 5")).not.toBeNull();
  expect(screen.getByLabelText("Best ask 20,001.00, aggregate size 6")).not.toBeNull();
  const chartRenders = vi.mocked(BotSignalChart).mock.calls.length;
  const chartProps = vi.mocked(BotSignalChart).mock.calls.at(-1)![0];
  await act(async () => chartProps.onMarketData?.({ contractKey: `${contractId}:minute:5`, unit: "minute", unitNumber: 5,
    candles: [], lastPrice: 20000, updatedAt: new Date().toISOString() }));
  expect(BotSignalChart).toHaveBeenCalledTimes(chartRenders);
  expect(stream).toHaveBeenCalledTimes(1);
  view.rerender(<BotMarketPanels bot={null} authenticatedCacheScope={null} activity={null} evaluation={null}
    refreshToken={0} demoMode evaluating={false} />);
  expect(close).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("Best bid 20,000.00, aggregate size 5")).toBeNull();
  expect(start).not.toHaveBeenCalled();
  expect(evaluate).not.toHaveBeenCalled();
});
