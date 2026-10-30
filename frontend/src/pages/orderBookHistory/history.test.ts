import { describe, expect, it } from "vitest";
import { continuousSegments, csvHistory, nearestPoint, priceChangeAfter, type BookHistory, type HistoryPoint } from "./history";

export const point = (time: number, extra: Partial<HistoryPoint> = {}): HistoryPoint => ({
  time, timestamp: new Date(time*1000).toISOString(), contract_id: "CON.F.US.MNQ.Z26", capture_id:"run", generation:1,index:time,
  bid_contracts:30,ask_contracts:10,bid_levels:10,ask_levels:10,imbalance:.5,mid_price:20000-time/10,
  last_trade_price:20000,spread:.25,quality_flags:[],...extra,
});

describe("history comparison", () => {
  it("breaks chart lines and forward comparisons at gaps, reconnects and invalid observations", () => {
    const rows=Array.from({length:70},(_,i)=>point(i));
    expect(priceChangeAfter(rows,rows[0],60)).toBe(-6);
    for(const extra of [{quality_flags:["reset"]},{generation:2},{capture_id:"restart"},{contract_id:"OTHER"}]) {
      const changed=rows.map((p,i)=>i===3?{...p,...extra}:p);
      expect(priceChangeAfter(changed,changed[0],5)).toBeNull();
      expect(continuousSegments(changed).length).toBeGreaterThan(1);
    }
    expect(priceChangeAfter(rows.filter(p=>p.time<3 || p.time>6),rows[0],15)).toBeNull();
    expect(priceChangeAfter(rows,rows[65],15)).toBeNull();
  });
  it("selects the nearest timestamp and exports scope and quality with the measurements", () => {
    const rows=[point(10),point(11),point(12)];
    expect(nearestPoint(rows,10.7)).toBe(rows[1]);
    expect(nearestPoint([],0)).toBeNull();
    const csv=csvHistory({scope:"20"} as BookHistory,rows);
    expect(csv).toContain('"scope"');expect(csv).toContain('"20"');expect(csv).toContain('"quality_flags"');
  });
});
