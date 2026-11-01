// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import { TopBotAuditTrail } from "./TopBotAuditTrail";
vi.mock("./usePageVisibility", () => ({ usePageVisibility: () => true }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("shows old/new stops with separate outcome evidence and pages older events", async () => {
  const fetch = vi.spyOn(botsApi,"getTopBotAudit").mockResolvedValueOnce({items:[
    {seq:2, at:"2026-11-01T15:00:00Z", kind:"stop_change_verified", operation:"abc", details:{old_stop:100,new_stop:125,reason:"Broker readback"}}
  ],next_before:2,storage:"Worker"}).mockResolvedValue({items:[],next_before:null,storage:"Worker"});
  render(<TopBotAuditTrail accountId={123} demoMode={false} />);
  expect(await screen.findByText("100 → 125")).toBeTruthy();
  expect(screen.getByText("stop change verified")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"Older events"}));
  await waitFor(()=>expect(fetch).toHaveBeenLastCalledWith(123,2,expect.anything()));
});
it("does not access account history in demo mode", () => {
  const fetch=vi.spyOn(botsApi,"getTopBotAudit");
  render(<TopBotAuditTrail accountId={123} demoMode />);
  expect(fetch).not.toHaveBeenCalled();
});
