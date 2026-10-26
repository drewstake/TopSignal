// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { requestJson } from "../../lib/api";
import { BacktestingPage } from "./BacktestingPage";

vi.mock("../../lib/api", () => ({ requestJson: vi.fn() }));
vi.mock("./TopBotBacktestPanel", () => ({ TopBotBacktestPanel: () => <div>TopBot runner</div> }));
const chartProps = vi.hoisted(() => ({ current: null as null | { initialAnchor?: number; initialView: string; onViewportChange: (range: { from: number; to: number }) => void } }));
vi.mock("./DatabentoChart", () => ({ DatabentoChart: (props: NonNullable<typeof chartProps.current>) => { chartProps.current = props; return <div>Historical chart</div>; } }));
const response = { symbol: "MNQ", source: "Databento", timeframe: "5m", coverage_start: "2019-05-05T22:03:00Z",
  coverage_end: "2026-07-10T20:20:00Z", start: "2026-07-03T20:20:00Z", end: "2026-07-10T20:20:00Z",
  total: 1, aggregate_size: 1, truncated: false, candles: [{ time: 1783714500, end_time: 1783714800, count: 1, open: 20000, high: 20002, low: 19999, close: 20001, volume: 100, contract: "MNQU6" }] };
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("loads the selected dates as whole UTC days and switches timeframe", async () => {
  vi.mocked(requestJson).mockResolvedValue(response);
  render(<BacktestingPage />);
  await screen.findByText("Historical chart");
  fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2024-03-11" } });
  fireEvent.change(screen.getByLabelText("Through date"), { target: { value: "2024-03-12" } });
  fireEvent.click(screen.getByRole("button", { name: "Load candles" }));
  await waitFor(() => expect(requestJson).toHaveBeenLastCalledWith("/api/market-data/databento/candles", expect.objectContaining({ query: { timeframe: "5m", start: "2024-03-11T00:00:00Z", end: "2024-03-13T00:00:00.000Z", overview: true, limit: 1000 } })));
  await screen.findByText("Historical chart");
  fireEvent.click(screen.getByRole("button", { name: "1m" }));
  await waitFor(() => expect(requestJson).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ query: expect.objectContaining({ timeframe: "1m", start: "2024-03-11T00:00:00Z" }) })));
});

it("requests and displays the whole history, with no recent-candle cutoff", async () => {
  vi.mocked(requestJson).mockResolvedValue({ ...response, total: 507395, aggregate_size: 255 });
  render(<BacktestingPage />);
  expect(await screen.findByText("507,395")).toBeTruthy();
  expect(requestJson).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ query: { timeframe: "5m", overview: true, limit: 1000 } }));
  fireEvent.click(screen.getByRole("button", { name: "All history" }));
  await waitFor(() => expect(requestJson).toHaveBeenCalledTimes(2));
  expect(screen.queryByText(/Showing the latest/)).toBeNull();
});

it("shows empty periods without inventing candles", async () => {
  vi.mocked(requestJson).mockResolvedValue({ ...response, candles: [], total: 0 });
  render(<BacktestingPage />);
  expect(await screen.findByText(/No closed candles/)).toBeTruthy();
  expect(screen.queryByText("Historical chart")).toBeNull();
});

it("explains missing local history and retries", async () => {
  vi.mocked(requestJson).mockRejectedValueOnce(new Error("databento_chart_history_missing")).mockResolvedValue(response);
  render(<BacktestingPage />);
  expect((await screen.findByRole("alert")).textContent).toContain("not installed");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("Historical chart");
  expect(screen.queryByRole("alert")).toBeNull();
});


it("preserves the visible date when changing timeframe and resets it for a new range", async () => {
  vi.mocked(requestJson).mockResolvedValue(response);
  render(<BacktestingPage />);
  await screen.findByText("Historical chart");
  act(() => { chartProps.current!.onViewportChange({ from: 1741600000, to: 1741620000 }); });
  fireEvent.click(screen.getByRole("button", { name: "1m" }));
  await screen.findByText("Historical chart");
  expect(chartProps.current!.initialAnchor).toBe(1741610000);
  fireEvent.click(screen.getByRole("button", { name: "All history" }));
  await screen.findByText("Historical chart");
  expect(chartProps.current!.initialAnchor).toBeUndefined();
  expect(chartProps.current!.initialView).toBe("overview");
});

it("discards a late page response after the tab unmounts", async () => {
  let resolve!: (value: typeof response) => void;
  vi.mocked(requestJson).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const view = render(<BacktestingPage />);
  const options = vi.mocked(requestJson).mock.calls[0][1];
  view.unmount();
  await act(async () => { resolve(response); });
  expect(options?.signal?.aborted).toBe(true);
  expect(screen.queryByText("Historical chart")).toBeNull();
});

it("validates reversed dates without starting a request", async () => {
  vi.mocked(requestJson).mockResolvedValue(response);
  render(<BacktestingPage />);
  await screen.findByText("Historical chart");
  fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-07-09" } });
  fireEvent.change(screen.getByLabelText("Through date"), { target: { value: "2026-07-08" } });
  fireEvent.submit(screen.getByRole("button", { name: "Load candles" }).closest("form")!);
  expect(screen.getByRole("alert").textContent).toContain("on or before");
  expect(requestJson).toHaveBeenCalledTimes(1);
});


it("shows the inclusive through-date rather than the exclusive API end-date", async () => {
  vi.mocked(requestJson).mockResolvedValue({ ...response, start: "2025-03-10T00:00:00Z", end: "2025-03-15T00:00:00Z" });
  render(<BacktestingPage />);
  await screen.findByText("Historical chart");
  expect(screen.getByText(/Range:/).textContent).toContain("Mar 14, 2025");
  expect(screen.getByText(/Range:/).textContent).not.toContain("Mar 15, 2025");
});
