import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({ getAccessToken: vi.fn(async () => null as string | null) }));
import {
  accountsApi, botsApi, clearFinancialReadCache, createExpense, createPayout, deleteExpense,
  deletePayout, getExpenseTotals, getFinancialSummary, getPayoutTotals, listExpenses, listPayouts, updateExpense,
} from "./api";
import { getAccessToken } from "./supabase";

const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
const jwt = (sub: string) => `header.${btoa(JSON.stringify({ iss: "navigation-tests", sub }))}.signature`;
let sequence = 0;
beforeEach(() => {
  vi.mocked(getAccessToken).mockResolvedValue(jwt(`user-${++sequence}`));
  vi.stubGlobal("fetch", vi.fn(async () => json({ items: [], total: 0 })));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe("tab navigation cache transport", () => {
  it("reuses expenses, payouts and financial totals across visits, with explicit refresh", async () => {
    const visit = () => Promise.all([
      listExpenses(), listPayouts(), getFinancialSummary({ asOfDate: "2026-09-04" }),
      getExpenseTotals("all_time"), getPayoutTotals(),
    ]);
    await visit();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(5);
    clearFinancialReadCache();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(10);
  });

  it.each([
    ["expense create", () => createExpense({ expense_date: "2026-09-04", category: "evaluation_fee", amount_cents: 100 })],
    ["expense edit", () => updateExpense(1, { amount_cents: 200 })],
    ["expense delete", () => deleteExpense(1)],
    ["payout create", () => createPayout({ payout_date: "2026-09-04", amount_cents: 100 })],
    ["payout delete", () => deletePayout(1)],
  ] as const)("invalidates all financial displays after %s", async (_name, mutate) => {
    const visit = () => Promise.all([listExpenses(), listPayouts(), getFinancialSummary()]);
    await visit();
    await mutate();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(7);
  });

  it("bypasses expense display cache for authoritative reconciliation", async () => {
    await listExpenses({ category: "evaluation_fee" });
    await listExpenses({ category: "evaluation_fee" }, { bypassCache: true });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("reuses journal entries and image metadata and invalidates after editing or conflict", async () => {
    vi.mocked(fetch).mockImplementation(async (url) => json(String(url).endsWith("/images") ? [] : { items: [], total: 0 }));
    const visit = () => Promise.all([accountsApi.getJournalEntries(42), accountsApi.listJournalImages(42, 1)]);
    await visit();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(2);
    await accountsApi.updateJournalEntry(42, 1, { version: 1, title: "Updated" });
    await visit();
    expect(fetch).toHaveBeenCalledTimes(5);
    vi.mocked(fetch).mockResolvedValueOnce(new Response("Conflict", { status: 409 }));
    await expect(accountsApi.updateJournalEntry(42, 1, { version: 1 })).rejects.toMatchObject({ status: 409 });
    await visit();
    expect(fetch).toHaveBeenCalledTimes(8);
  });

  it.each([
    ["create", () => accountsApi.createJournalEntry(43, { entry_date: "2026-09-04", title: "New", mood: "Neutral", tags: [], body: "" })],
    ["delete", () => accountsApi.deleteJournalEntry(43, 1)],
    ["image upload", () => accountsApi.uploadJournalImage(43, 1, new Blob(["test"]), "test.png")],
    ["image delete", () => accountsApi.deleteJournalImage(43, 1, 1)],
    ["trade stats", () => accountsApi.pullJournalTradeStats(43, 1)],
  ] as const)("invalidates journal reads after %s", async (_name, mutate) => {
    vi.mocked(fetch).mockImplementation(async (url, options) => json(options?.method === "POST"
      ? { url: "/image.png" }
      : String(url).endsWith("/images") ? [] : { items: [], total: 0 }));
    const visit = () => Promise.all([accountsApi.getJournalEntries(43), accountsApi.listJournalImages(43, 1)]);
    await visit();
    await mutate();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(5);
  });

  it("keeps account, filter, signed-in user and demo data separate", async () => {
    await accountsApi.getJournalEntries(44);
    await accountsApi.getJournalEntries(45);
    await accountsApi.getJournalEntries(44, { q: "different" });
    await listExpenses({ category: "evaluation_fee" });
    await listExpenses({ category: "activation_fee" });
    vi.mocked(getAccessToken).mockResolvedValue(jwt("another-user"));
    await accountsApi.getJournalEntries(44);
    await listExpenses({ category: "evaluation_fee" });
    expect(fetch).toHaveBeenCalledTimes(7);
    vi.stubGlobal("localStorage", { getItem: () => "true" });
    await listExpenses();
    expect(fetch).toHaveBeenCalledTimes(7);
  });

  it("expires successful reads after ten minutes and never caches HTTP failures", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    await accountsApi.getJournalEntries(46);
    vi.setSystemTime(Date.now() + 600_000);
    await accountsApi.getJournalEntries(46);
    expect(fetch).toHaveBeenCalledTimes(2);
    vi.mocked(fetch).mockResolvedValueOnce(new Response("Unavailable", { status: 503 }));
    await expect(listExpenses()).rejects.toMatchObject({ status: 503 });
    await listExpenses();
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("manual Trades refresh bypasses local caches without forcing duplicate provider syncs", async () => {
    const visit = (bypassCache = false) => Promise.all([
      accountsApi.getTrades(47, {}, { bypassCache }), accountsApi.getSummary(47, {}, { bypassCache }),
    ]);
    await visit();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(2);
    await visit(true);
    expect(fetch).toHaveBeenCalledTimes(4);
    for (const [url, options] of vi.mocked(fetch).mock.calls) {
      expect(String(url)).not.toContain("refresh=true");
      expect(options?.method).toBe("GET");
    }
  });

  it("always reads Bot safety settings, runtime and activity from the server", async () => {
    const visit = () => Promise.all([botsApi.listConfigs(47), botsApi.getRuntimeStatus(), botsApi.getActivity(1)]);
    await visit();
    await visit();
    expect(fetch).toHaveBeenCalledTimes(6);
  });
});
