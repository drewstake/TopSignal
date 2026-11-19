// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { performance, type Actual } from "./imbalanceReport";
import { InclusionPanel, TopBotEdgePanel } from "./TopBotEdgePanel";

const trade = (id: number, net: number, date: string): Actual => ({
  id, side: "BUY", mode: "fixed", version: "v1", session: "a", entry: date, exit: date, net, costs: 1,
  fill: 10, submit: null, qualify: null,
});

describe("closed outcomes", () => {
  it("combines historical versions under one strategy", () => {
    const date = "2026-11-19T18:00:00Z";
    const data = { account_id: 1, config_id: 1, contract: "MNQ", started: date, as_of: date,
      truncated: false, studies: [], coverage: [], actual: [trade(1, 10, date), { ...trade(2, 20, date), version: "v2", mode: "trailing" }] };
    render(<TopBotEdgePanel state={{ account: 1, data }} now={Date.parse(date)} />);
    expect(screen.queryByRole("combobox", { name: "Saved strategy version" })).toBeNull();
    expect(screen.getByText("Net P&L").nextElementSibling?.textContent).toBe("$30.00");
  });
  it("sorts by closing time, includes fees once and excludes flats from both win rates", () => {
    const t = (id: number, net: number) => trade(id, net, "2026-11-10T12:0"+id+":00Z");
    const m=performance([t(2,-20), t(1,10), t(3,0), {...t(4,200), exit:null}]);
    expect(m.count).toBe(3);
    expect(m.net).toBe(-10);
    expect(m.gross).toBe(-7);
    expect(m.breakeven).toBeCloseTo(2/3);
    expect(m.rate).toBe(.5);
    expect(m.drawdown).toBe(20);
    expect(performance([]).mean).toBeNull();
    expect(performance([t(1,2)]).breakeven).toBeNull();
  });
  it("keeps unprocessed outcomes in their denominators and respects comparison filters", () => {
    const item={id:1,mode:"fixed",version:"a",date:"2026-11-10",outcome:"win",status:"usable",reasons:[]};
    render(<InclusionPanel version="a" mode="fixed" start="" end="2026-11-10" rows={[item,
      {...item,id:2,status:"pending"}, {...item,id:3,version:"b"}, {...item,id:4,outcome:"loss",status:"excluded",reasons:["feed_unavailable"]}]} />);
    expect(within(screen.getByRole("row",{name:/Winners/})).getByText("1 / 2")).toBeTruthy();
    expect(within(screen.getByRole("row",{name:/Losers/})).getByText("0 / 1")).toBeTruthy();
    expect(screen.getByText(/Most common exclusion: feed unavailable/)).toBeTruthy();
  });
});
