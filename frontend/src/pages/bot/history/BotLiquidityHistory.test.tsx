// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HistoryExplorer, HistoryView } from "./BotLiquidityHistory";
import { requestJson } from "../../../lib/api";
import type { BookHistory, HistoryPoint } from "./history";
import { OrderBookStore } from "../orderBook";
import { sessionWindow } from "./history";
vi.mock("../../../lib/api",()=>({requestJson:vi.fn()}));
const points:HistoryPoint[]=Array.from({length:70},(_,i)=>({time:1793379600+i,timestamp:new Date((1793379600+i)*1000).toISOString(),
  contract_id:"CON.F.US.MNQ.Z26",capture_id:"run",generation:1,index:i,bid_contracts:30,ask_contracts:10,
  bid_levels:10,ask_levels:10,imbalance:.5,mid_price:20000-i/10,last_trade_price:20000,spread:.25,quality_flags:[]}));
const data:BookHistory={points,scope:"all",start:points[0].timestamp,end:points.at(-1)!.timestamp,earliest:points[0].timestamp,
  latest:points.at(-1)!.timestamp,available_days:["2026-10-30"],contracts:["CON.F.US.MNQ.Z26"],skipped_segments:0,
  sealed_only:true,interval_ms:250,source:"peteamdpc",generated_at:points.at(-1)!.timestamp};
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks();vi.clearAllMocks();});
it("retains zoom and selection when live props advance, with per-metric paths and hover evidence", () => {
  const withFlags={...data,points:points.map((p,i)=>i===2?{...p,mid_price:null,quality_flags:["ask_provider_time_unreliable"]}:p)};
  const view=render(<HistoryExplorer data={withFlags} onInspect={()=>{}} />);
  const chart=screen.getByRole("img",{name:/shared timeline/});
  fireEvent.keyDown(chart,{key:"ArrowRight"});
  fireEvent.click(screen.getByRole("button",{name:"Zoom in"}));
  const before=chart.textContent;
  view.rerender(<HistoryExplorer data={{...withFlags,end:new Date((points.at(-1)!.time+10)*1000).toISOString()}} onInspect={()=>{}} />);
  expect(screen.getByRole("img",{name:/shared timeline/})).toBe(chart);
  expect(chart.textContent).toBe(before);
  expect(chart.querySelectorAll('path')[1].getAttribute('d')).toContain('L');
  vi.spyOn(chart,'getBoundingClientRect').mockReturnValue({left:0,width:1160} as DOMRect);
  fireEvent.pointerMove(chart,{clientX:400});
  expect(screen.getByRole('tooltip').textContent).toContain('bid levels');
  expect(screen.getByRole('tooltip').textContent).toContain('Quality:');
});
it("loads the entire session once and refreshes only the recent archive without losing the session start", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-02T02:00:00Z"));
  const start = sessionWindow(Date.now()).start;
  const early = { ...points[1], time: start + 60, timestamp: new Date((start + 60) * 1000).toISOString() };
  const late = { ...early, time: Date.now() / 1000 - 60, timestamp: new Date(Date.now() - 60000).toISOString() };
  vi.mocked(requestJson).mockResolvedValueOnce({ ...data, points: [early], start: new Date(start * 1000).toISOString(), latest: early.timestamp, interval_ms: 10000, window: "session" });
  vi.mocked(requestJson).mockResolvedValue({ ...data, points: [late], start: new Date(Date.now() - 900000).toISOString(), latest: late.timestamp });
  render(<HistoryView accountId={123} compact />);
  await act(async () => {});
  expect(vi.mocked(requestJson).mock.calls[0][1]?.query).toEqual({ scope: "all", window: "session" });
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(vi.mocked(requestJson).mock.calls[1][1]?.query).toEqual({ scope: "all" });
  expect(screen.getByText(/2 observed samples shown/)).toBeTruthy();
  expect(screen.getByRole("img", { name: /shared timeline/ }).textContent).toContain(new Date(start * 1000).toLocaleTimeString([], { hour12: false }));
});
it("shows live depth before recordings arrive, freezes inspection, and labels stale data honestly", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-02T02:00:00Z"));
  vi.mocked(requestJson).mockReturnValue(new Promise(() => {}));
  const contract = "CON.F.US.MNQ.Z26";
  const store = new OrderBookStore(contract);
  store.setConnectionState({ contract_id: contract, state: "connected" });
  store.applySnapshot({ contract_id: contract, sequence: 1, timestamp: new Date().toISOString(), bids: [{ price: 100, size: 20 }], asks: [{ price: 101, size: 10 }] });
  render(<HistoryView accountId={123} compact liveStore={store} />);
  await act(async () => {});
  expect(screen.getByText("Liquidity before the move")).toBeTruthy();
  expect(screen.getByText(/Live ProjectX depth.*Updates every 1s/)).toBeTruthy();
  const chart = screen.getByRole("img", { name: /shared timeline/ });
  const paths = chart.querySelectorAll("path");
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText(/1 observed samples shown/)).toBeTruthy(); // no fabricated duplicates
  store.applyUpdate({ contract_id: contract, sequence: 2, timestamp: new Date().toISOString(), side: "bid", price: 100, size: 30 });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText(/2 observed samples shown/)).toBeTruthy();
  expect(screen.getByRole("img", { name: /shared timeline/ })).toBe(chart);
  expect(chart.querySelectorAll("path")[0]).toBe(paths[0]);
  expect(chart.textContent).toContain(new Date("2026-11-02T02:00:01Z").toLocaleTimeString([], { hour12: false })); // window advances without remounting
  const followingChartText = chart.textContent;
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(chart.textContent).not.toBe(followingChartText);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText(/2 observed samples shown/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Follow latest" }));
  await act(async () => {});
  expect(screen.getByText(/Waiting for fresh live depth.*Showing available history/)).toBeTruthy();
  expect(screen.getByRole("img", { name: /shared timeline/ }).textContent).toBe(followingChartText);
  expect(screen.getByText(/2 observed samples shown/)).toBeTruthy();
  expect(requestJson).toHaveBeenCalledTimes(2); // live sampling does not poll the archive each second
});
it("refreshes session recordings every 30 seconds without blanking the chart or overlapping slow requests", async () => {
  vi.useFakeTimers();
  vi.mocked(requestJson).mockResolvedValue(data);
  const view = render(<HistoryView accountId={123} compact />);
  await act(async () => {});
  let resolve: (value: BookHistory) => void = () => {};
  vi.mocked(requestJson).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(requestJson).toHaveBeenCalledTimes(2);
  expect(screen.getByText("Liquidity before the move")).toBeTruthy();
  expect(screen.queryByText("Reading saved order book measurements…")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
  expect(requestJson).toHaveBeenCalledTimes(2);
  await act(async () => resolve({ ...data, source: "Updated recording" }));
  expect(screen.getByText("Source: Updated recording")).toBeTruthy();
  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(requestJson).toHaveBeenCalledTimes(2);
});
it("pauses hidden tabs and inspection, and resumes following the latest window", async () => {
  vi.useFakeTimers();
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.mocked(requestJson).mockResolvedValue(data);
  render(<HistoryView accountId={123} compact />);
  await act(async () => {});
  hidden.mockReturnValue(true);
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(requestJson).toHaveBeenCalledTimes(1);
  hidden.mockReturnValue(false);
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(requestJson).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(requestJson).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Follow latest" }));
  await act(async () => {});
  expect(requestJson).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole("button", { name: "Inspect preceding 5 minutes" }));
  await act(async () => {});
  expect(requestJson).toHaveBeenCalledTimes(4);
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(requestJson).toHaveBeenCalledTimes(4);
});
it("retains history on a background failure and clears the error after recovery", async () => {
  vi.useFakeTimers();
  vi.mocked(requestJson).mockResolvedValue(data);
  render(<HistoryView accountId={123} compact />);
  await act(async () => {});
  vi.mocked(requestJson).mockRejectedValueOnce(new Error("Recording host unavailable"));
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(screen.getByRole("alert").textContent).toBe("Recording host unavailable");
  expect(screen.getByText("Liquidity before the move")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(screen.queryByRole("alert")).toBeNull();
});
it("embeds recorded liquidity with chart controls and no retired-page link", async () => {
  vi.mocked(requestJson).mockResolvedValue(data);
  render(<HistoryView accountId={123} compact />);
  expect(await screen.findByText("Liquidity before the move")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Full history & date range" })).toBeNull();
  expect(screen.queryByLabelText("History start")).toBeNull();
  expect(screen.getByRole("button", { name: "Zoom in" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
});
it("shows source, synchronized chart, selected totals and subsequent price changes",async()=>{
  vi.mocked(requestJson).mockResolvedValue(data);
  render(<HistoryView accountId={123}/>);
  expect(await screen.findByText("Source: peteamdpc")).toBeTruthy();
  expect(screen.getByRole("img",{name:/shared timeline/})).toBeTruthy();
  expect(screen.getByText("50.0%")).toBeTruthy();
  expect(screen.getByText("-6 points")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Inspect preceding 5 minutes"}));
  await waitFor(()=>expect(requestJson).toHaveBeenCalledTimes(2));
  expect(vi.mocked(requestJson).mock.calls[1][1]?.query).toMatchObject({scope:"all",
    start:new Date((points[0].time-300)*1000).toISOString(),end:new Date((points[0].time+60)*1000).toISOString()});
});
it("rejects oversized windows before requesting and makes missing history explicit",async()=>{
  vi.mocked(requestJson).mockResolvedValue({...data,points:[]});
  render(<HistoryView accountId={123}/>);
  expect(await screen.findByText("No recordings in this window")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("History start"),{target:{value:"2026-10-30T08:00:00"}});
  fireEvent.change(screen.getByLabelText("History end"),{target:{value:"2026-10-31T10:00:00"}});
  fireEvent.click(screen.getByRole("button",{name:"Load history"}));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent","Choose a valid time range of one session (up to 24 hours) or less.");
  expect(requestJson).toHaveBeenCalledTimes(1);
});
it("does not show a late response after the account view is replaced",async()=>{
  let resolve:(data:BookHistory)=>void=()=>{};
  vi.mocked(requestJson).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  const view=render(<HistoryView key="123" accountId={123}/>);
  vi.mocked(requestJson).mockResolvedValue({...data,points:[],source:"second account"});
  view.rerender(<HistoryView key="456" accountId={456}/>);
  await screen.findByText("Source: second account");
  await act(async()=>resolve(data));
  expect(screen.queryByText("Source: peteamdpc")).toBeNull();
});

it("clears the previous window while a refresh is pending",async()=>{
  vi.mocked(requestJson).mockResolvedValueOnce(data);
  render(<HistoryView accountId={123}/>);
  await screen.findByText("Source: peteamdpc");
  let resolve:(value:BookHistory)=>void=()=>{};
  vi.mocked(requestJson).mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  fireEvent.click(screen.getByRole("button",{name:"Refresh"}));
  expect(screen.getByRole("status").textContent).toContain("Reading saved order book");
  expect(screen.queryByText("Source: peteamdpc")).toBeNull();
  expect(screen.getByRole("button",{name:"Refresh"})).toHaveProperty("disabled",true);
  await act(async()=>resolve({...data,source:"Refreshed recording"}));
  expect(await screen.findByText("Source: Refreshed recording")).toBeTruthy();
  expect(screen.getByRole("button",{name:"Refresh"})).toHaveProperty("disabled",false);
});
