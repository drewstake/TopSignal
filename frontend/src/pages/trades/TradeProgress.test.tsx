// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { TradeProgressChart, TradeProgressReview, type ProgressSample, type ProgressTimeline } from "./TradeProgress";
import { chartSegments } from "./tradeProgressModel";
import type { AccountTrade } from "../../lib/types";
import { requestJson } from "../../lib/api";

vi.mock("../../lib/api", () => ({ requestJson: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const sample = (ts: number, changes: Partial<ProgressSample> = {}): ProgressSample => ({ ts, kind: "periodic", points: 3,
  unrealized_dollars: 12, quantity: 2, average_entry: 100, mark: 103, mark_timestamp: ts,
  issue: null, source: "live_observation", ...changes });
const timeline = (samples: ProgressSample[]): ProgressTimeline => ({ source: "live_observation", coverage: "partial", next_cursor: null,
  position: { id: "p1", contract: "CON.F.US.MNQ.Z26", opened: 10, first_observed: 10, ended: 60,
    reasons: ["market_disconnected"], best: sample(15,{ kind:"best", points:9 }), worst: sample(35,{ kind:"worst", points:-2 }) }, samples, fills: [], fills_truncated: false });

describe("trade progress", () => {
  it("breaks the path for gaps, null prices and entry-basis changes", () => {
    const points=[sample(10),sample(11),sample(12,{ points:null,issue:"stale_price" }),sample(13),sample(14,{ average_entry:102 }),sample(17,{ average_entry:102 })];
    expect(chartSegments(points).map(part=>part.map(s=>s.ts))).toEqual([[10,11],[13],[14],[17]]);
  });
  it("shows observations, extremes, basis and honest coverage", () => {
    render(<TradeProgressChart data={timeline([sample(10,{ kind:"entry" }),sample(20),sample(30,{ kind:"scale_in",average_entry:102 }),sample(40,{ kind:"exit" })])}/>);
    expect(screen.getByText("Points over time")).toBeTruthy();
    expect(screen.getByText("Partial coverage")).toBeTruthy();
    expect(screen.getByText("Best 9 pts")).toBeTruthy();
    expect(screen.getByText("Worst -2 pts")).toBeTruthy();
    expect(screen.getByText(/No realized fill observations/)).toBeTruthy();
    expect(screen.getByText(/Lines break when the entry basis changes/)).toBeTruthy();
  });
  it("does not render missing observations as a flat chart", () => {
    const data=timeline([sample(10,{points:null,issue:"missing_price"})]);
    data.position.best=null; data.position.worst=null;
    render(<TradeProgressChart data={data}/>);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText(/This does not mean the position was flat/)).toBeTruthy();
  });
  it("labels reconstructed input distinctly", () => {
    render(<TradeProgressChart data={{...timeline([sample(10)]),source:"reconstruction"}}/>);
    expect(screen.getByText(/Reconstructed history/)).toBeTruthy();
  });
  it("does not pick an ambiguous position", async () => {
    HTMLDialogElement.prototype.showModal=function () { this.open=true; };
    vi.mocked(requestJson).mockResolvedValue({ matches:[{ id:"one",opened:10,direction:"long" },{ id:"two",opened:20,direction:"short" }],status:{ enrolled:true } });
    render(<TradeProgressReview trade={{id:1,account_id:1} as AccountTrade} onClose={()=>{}}/>);
    await waitFor(()=>expect(screen.getByText(/Several positions overlap/)).toBeTruthy());
    expect(vi.mocked(requestJson).mock.calls.length).toBe(1);
  });
  it("shows unavailable state on a local-store failure", async () => {
    HTMLDialogElement.prototype.showModal=function () { this.open=true; };
    vi.mocked(requestJson).mockRejectedValue(new Error("offline"));
    render(<TradeProgressReview trade={{id:1,account_id:1} as AccountTrade} onClose={()=>{}}/>);
    await waitFor(()=>expect(screen.getByRole("alert").textContent).toContain("Recording unavailable"));
    expect(screen.queryByRole("img")).toBeNull();
  });
  it("distinguishes unenrolled accounts from flat trades", async () => {
    HTMLDialogElement.prototype.showModal=function () { this.open=true; };
    vi.mocked(requestJson).mockResolvedValue({ matches:[],status:{ enrolled:false } });
    render(<TradeProgressReview trade={{id:1,account_id:1} as AccountTrade} onClose={()=>{}}/>);
    await waitFor(()=>expect(screen.getByText(/Account is not enrolled/)).toBeTruthy());
  });
});
