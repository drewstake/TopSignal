// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { BotMarketPanels } from "./BotMarketPanels";
import { BotSignalChart } from "./BotSignalChart";
import { OrderBookPanel } from "./OrderBookPanel";
import type { BotMarketSnapshot } from "./botMarketContext";

vi.mock("./BotSignalChart", () => ({ BotSignalChart: vi.fn(() => <div>Chart</div>) }));
vi.mock("./OrderBookPanel", () => ({ OrderBookPanel: vi.fn(() => <div>Depth</div>) }));
vi.mock("./BotAnalysisPanel", () => ({
  BotAnalysisPanel: ({ marketSnapshot }: { marketSnapshot: BotMarketSnapshot | null }) =>
    <div>Analysis price: {marketSnapshot?.lastPrice ?? "none"}</div>,
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("delivers chart snapshots to analysis without rerendering the chart, order book, or parent", async () => {
  const parentRender = vi.fn();
  function Harness() {
    parentRender();
    return <BotMarketPanels bot={null} authenticatedCacheScope="test" activity={null} evaluation={null}
      refreshToken={0} demoMode={false} evaluating={false} />;
  }
  render(<Harness />);
  await screen.findByText("Analysis price: none");
  const chartProps = vi.mocked(BotSignalChart).mock.calls[0][0] as ComponentProps<typeof BotSignalChart>;
  await act(async () => chartProps.onMarketData?.({
    contractKey: "MNQ:minute:5", unit: "minute", unitNumber: 5, candles: [], lastPrice: 21_000,
    updatedAt: new Date().toISOString(),
  }));
  expect(screen.getByText("Analysis price: 21000")).not.toBeNull();
  expect(BotSignalChart).toHaveBeenCalledTimes(1);
  expect(OrderBookPanel).toHaveBeenCalledTimes(1);
  expect(parentRender).toHaveBeenCalledTimes(1);
});
