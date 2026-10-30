// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HistoryView } from "./OrderBookHistoryPage";
import { requestJson } from "../../lib/api";
import type { BookHistory, HistoryPoint } from "./history";
vi.mock("../../lib/api",()=>({requestJson:vi.fn()}));
const points:HistoryPoint[]=Array.from({length:70},(_,i)=>({time:1793379600+i,timestamp:new Date((1793379600+i)*1000).toISOString(),
  contract_id:"CON.F.US.MNQ.Z26",capture_id:"run",generation:1,index:i,bid_contracts:30,ask_contracts:10,
  bid_levels:10,ask_levels:10,imbalance:.5,mid_price:20000-i/10,last_trade_price:20000,spread:.25,quality_flags:[]}));
const data:BookHistory={points,scope:"all",start:points[0].timestamp,end:points.at(-1)!.timestamp,earliest:points[0].timestamp,
  latest:points.at(-1)!.timestamp,available_days:["2026-10-30"],contracts:["CON.F.US.MNQ.Z26"],skipped_segments:0,
  sealed_only:true,interval_ms:250,source:"peteamdpc",generated_at:points.at(-1)!.timestamp};
afterEach(()=>{cleanup();vi.clearAllMocks();});
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
  fireEvent.change(screen.getByLabelText("History end"),{target:{value:"2026-10-30T10:00:00"}});
  fireEvent.click(screen.getByRole("button",{name:"Load history"}));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent","Choose a valid time range of one hour or less.");
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
