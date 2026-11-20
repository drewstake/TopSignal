// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { router as appRouter } from "../../app/routes";
import { requestJson, streamProjectXMarketDepth } from "../../lib/api";

vi.mock("../../lib/appShellApi", () => {
  const accounts = async () => [1, 2].map(id => ({
    id, name: `Account ${id}`, trade_data_source: "projectx", is_main: id === 1, is_visible: true,
  }));
  return { getSelectableAccountsLocalFirst: accounts, getSelectableAccounts: accounts, refreshTrades: vi.fn() };
});
vi.mock("../../lib/api", async original => ({ ...await original<typeof import("../../lib/api")>(), requestJson: vi.fn(),
  streamProjectXMarketDepth: vi.fn(), streamProjectXMarketTrades: () => () => {} }));
vi.mock("../bot/useProjectXChartConnection", () => ({ useProjectXChartConnection: () => ({
  connection: { scope: "owner", market: { contract_id: "CON.F.US.MNQ.Z26", tick_size: 0.25, symbol: "MNQ" } },
}) }));
vi.mock("../bot/BotPage", () => ({ BotPage: () => <h1>TopBot</h1> }));
vi.mock("../scanner/ScannerAlerts", () => ({ ScannerAlerts: () => null }));
vi.mock("../bot/TopBotComparison", () => ({ TopBotComparison: () => <section>Exit comparison</section> }));
vi.mock("../bot/TopBotResearchStatus", () => ({ TopBotResearchStatus: () => <section>Recording and research status</section> }));
vi.mock("../../app/AccountAccess", () => ({ AccountAccess: ({ children }: { children: React.ReactNode }) => children }));
afterEach(() => { cleanup(); vi.resetAllMocks(); window.localStorage.clear(); });

it("opens an old bookmark, compares prior-day signals and preserves the selected account going both ways", async () => {
  Element.prototype.scrollIntoView = vi.fn();
  const calls: number[] = [];
  const closeDepth = vi.fn();
  vi.mocked(streamProjectXMarketDepth).mockReturnValue(closeDepth);
  vi.mocked(requestJson).mockImplementation(async (path, options) => {
    const query = options?.query ?? {};
    if (path === "/api/accounts/2/recorded-signals") {
      const prior = query.day === "2026-11-17";
      return { account_id: 2, day: prior ? query.day : "2026-11-18", signals: (prior ? [6, 9] : [51]).map(id => ({
        id, at: "2026-11-17T19:55:00Z", contract: "CON.F.US.MNQ.Z26", imbalance: "bid-heavy", rule: "0.1/30",
      })), previous_day: prior ? null : "2026-11-17", next_day: null, next_before: null };
    }
    const replay = /^\/api\/accounts\/2\/recorded-signals\/(\d+)\/replay$/.exec(path);
    if (replay) {
      const id = Number(replay[1]); calls.push(id);
      expect(options?.method ?? "GET").toBe("GET");
      expect(query).toMatchObject({ stop_a: "25", target_a: "25", stop_b: "8", target_b: "10", quantity: 1 });
      return { account_id: 2, id, entry: 100, side: "Long", quantity: 1, exits: {
        a: { net: 48.78, reason: "Target", price: 125 }, b: { net: 18.78, reason: "Target", price: 110 },
      }, coverage: null };
    }
    return new Promise(() => {});
  });
  const router = createMemoryRouter(appRouter.routes, { initialEntries: ["/backtesting?account=2"] });
  render(<RouterProvider router={router} />);
  await screen.findByRole("heading", { name: "Recorded live signal replay" });
  expect(router.state.location.pathname).toBe("/recorders");
  const nav = within(screen.getByRole("navigation", { name: "Main navigation" }));
  expect(nav.getAllByRole("link").map(l => l.textContent).slice(-3)).toEqual(["Scanner", "Recorders", "Appearance"]);
  expect(nav.getByRole("link", { name: "Recorders" }).getAttribute("aria-current")).toBe("page");
  expect(screen.getByRole("region", { name: "Trade recorder" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Liquidity heatmap" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Live & recorded liquidity" })).toBeTruthy();
  expect(streamProjectXMarketDepth).toHaveBeenCalledTimes(1);
  expect(vi.mocked(requestJson).mock.calls.some(([path]) => path === "/api/accounts/2/order-book-history")).toBe(true);
  expect(screen.getByText("Recording and research status")).toBeTruthy();
  await screen.findByLabelText("Select signal 51");
  fireEvent.click(screen.getByRole("button", { name: "Previous recorded day" }));
  await screen.findByLabelText("Select signal 6");
  fireEvent.click(screen.getByLabelText("Select all shown signals"));
  const bracket = within(screen.getByRole("group", { name: "Bracket B" }));
  fireEvent.change(bracket.getByLabelText("Stop (pts)"), { target: { value: "8" } });
  fireEvent.change(bracket.getByLabelText("Target (pts)"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Compare brackets (2)" }));
  expect(await screen.findByText(/A \$97.56 · B \$37.56/)).toBeTruthy();
  expect(calls).toEqual([6, 9]);
  expect(vi.mocked(requestJson).mock.calls.every(([, opt]) => (opt?.method ?? "GET") === "GET")).toBe(true);
  fireEvent.click(nav.getByRole("link", { name: "Bot" }));
  await screen.findByRole("heading", { name: "TopBot" });
  expect(router.state.location.search).toBe("?account=2");
  expect(screen.queryByRole("region", { name: "Trade recorder" })).toBeNull();
  expect(closeDepth).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole("combobox", { name: "Active Account" }), { target: { value: "1" } });
  await waitFor(() => expect(nav.getByRole("link", { name: "Recorders" }).getAttribute("href")).toBe("/recorders?account=1"));
  fireEvent.click(nav.getByRole("link", { name: "Recorders" }));
  await screen.findByRole("heading", { name: "Recorded live signal replay" });
  expect(router.state.location.search).toBe("?account=1");
  expect(vi.mocked(requestJson).mock.calls.some(([path]) => path === "/api/accounts/1/order-book-history")).toBe(true);
  expect(screen.queryByText(/A \$97.56/)).toBeNull();
});
