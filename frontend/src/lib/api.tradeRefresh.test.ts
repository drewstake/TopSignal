import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({ getAccessToken: vi.fn(async () => null as string | null) }));
import { accountsApi } from "./api";
import { getAccessToken } from "./supabase";

const range = { start: "2026-08-31T22:00:00Z", end: "2026-09-04T21:05:00Z" };
const result = { fetched_count: 0, inserted_count: 0 };
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(range.end));
  vi.mocked(getAccessToken).mockResolvedValue(null);
  vi.stubGlobal("fetch", vi.fn(async () => json(result)));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("dashboard automatic sync transport", () => {
  it("preserves cached reads when an automatic sync reuses a recent success, but refreshes on demand", async () => {
    const fetchMock = vi.mocked(fetch).mockImplementation(async (_url, options) =>
      json(options?.method === "POST" ? result : []),
    );
    await accountsApi.getTrades(95001);
    await accountsApi.refreshTrades(95001, range, { automatic: true });
    await accountsApi.getTrades(95001);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    vi.setSystemTime(new Date("2026-09-04T21:08:00Z"));
    await accountsApi.refreshTrades(95001, { ...range, end: new Date().toISOString() }, { automatic: true });
    await accountsApi.getTrades(95001);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await accountsApi.refreshTrades(95001, range);
    await accountsApi.getTrades(95001);
    expect(fetchMock).toHaveBeenCalledTimes(5);
  });

  it("coalesces simultaneous automatic calls and retries HTTP failures", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("Provider unavailable", { status: 503 }));
    const first = accountsApi.refreshTrades(95002, range, { automatic: true });
    const second = accountsApi.refreshTrades(95002, range, { automatic: true });
    expect((await Promise.allSettled([first, second])).every((entry) => entry.status === "rejected")).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    await accountsApi.refreshTrades(95002, range, { automatic: true });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("uses one trade-sync lane for calendar visits, then reads saved calendar data", async () => {
    vi.mocked(fetch).mockImplementation(async (_url, options) => json(options?.method === "POST" ? result : []));
    await accountsApi.refreshTrades(95003, range, { automatic: true });
    await accountsApi.getPnlCalendar(95003, { ...range, all_time: false, refresh: true, automaticRefresh: true });
    await accountsApi.getPnlCalendar(95003, { ...range, all_time: false, refresh: true, automaticRefresh: true });
    expect(fetch).toHaveBeenCalledTimes(2);
    const urls = vi.mocked(fetch).mock.calls.map(([url]) => String(url));
    expect(urls[1]).toContain("/pnl-calendar?");
    expect(urls[1]).toContain("refresh=false");
    expect(urls[1]).not.toContain("automaticRefresh");
  });

  it("does not reuse a refresh across signed-in users or accounts", async () => {
    const jwt = (sub: string) => `header.${btoa(JSON.stringify({ iss: "test", sub }))}.signature`;
    vi.mocked(getAccessToken).mockResolvedValue(jwt("alice"));
    await accountsApi.refreshTrades(95004, range, { automatic: true });
    await accountsApi.refreshTrades(95005, range, { automatic: true });
    vi.mocked(getAccessToken).mockResolvedValue(jwt("bob"));
    await accountsApi.refreshTrades(95004, range, { automatic: true });
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("invalidates automatic freshness after a data mutation", async () => {
    await accountsApi.refreshTrades(95006, range, { automatic: true });
    await accountsApi.setMainAccount(95006);
    await accountsApi.refreshTrades(95006, range, { automatic: true });
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("sends no provider request in Demo Mode", async () => {
    vi.stubGlobal("localStorage", { getItem: () => "true" });
    await expect(accountsApi.refreshTrades(95007, range, { automatic: true })).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
    expect(getAccessToken).not.toHaveBeenCalled();
  });
});
