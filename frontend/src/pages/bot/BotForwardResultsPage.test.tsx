// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { botsApi } from "../../lib/api";
import type { BotConfig } from "../../lib/types";
import { BotForwardResultsPage } from "./BotForwardResultsPage";

vi.mock("../../lib/api", () => ({ botsApi: { listConfigsWithCacheScope: vi.fn(), getForwardResults: vi.fn() } }));
vi.mock("../../components/demo/useDemoInteractionPolicy", () => ({ useDemoInteractionPolicy: () => ({ demoModeEnabled: false }) }));
const bot = { id: 32, name: "TopBot", account_id: 12345, symbol: "MNQ" } as BotConfig;
const renderPage = (path = "/bot-results/32") => {
  const router = createMemoryRouter([{ path: "/bot-results/:botId", element: <BotForwardResultsPage /> }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
};
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("loads owned research without an active account picker or broker discovery", async () => {
  vi.mocked(botsApi.listConfigsWithCacheScope).mockResolvedValue({ configs: { items: [bot], total: 1 }, cacheScope: "owner" });
  vi.mocked(botsApi.getForwardResults).mockRejectedValue(new Error("results unavailable"));
  renderPage();
  expect(await screen.findByRole("heading", { name: "TopBot results" })).toBeTruthy();
  expect(screen.getByText("MNQ · Account 12345 · Bot 32")).toBeTruthy();
  expect(botsApi.listConfigsWithCacheScope).toHaveBeenCalledWith(undefined, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(botsApi.getForwardResults).toHaveBeenCalledWith(32, 30, expect.anything());
  expect(screen.queryByRole("button", { name: /start|buy|sell|flatten/i })).toBeNull();
});

it("does not request results for a bot outside the authenticated config list", async () => {
  vi.mocked(botsApi.listConfigsWithCacheScope).mockResolvedValue({ configs: { items: [], total: 0 }, cacheScope: "other-owner" });
  renderPage();
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(botsApi.getForwardResults).not.toHaveBeenCalled();
});

it("discards a previous bot's late response after navigation", async () => {
  let resolve!: (value: Awaited<ReturnType<typeof botsApi.listConfigsWithCacheScope>>) => void;
  vi.mocked(botsApi.listConfigsWithCacheScope).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const router = renderPage();
  vi.mocked(botsApi.listConfigsWithCacheScope).mockResolvedValue({ configs: { items: [], total: 0 }, cacheScope: "owner" });
  await act(async () => { await router.navigate("/bot-results/99"); });
  await screen.findByRole("alert");
  await act(async () => resolve({ configs: { items: [bot], total: 1 }, cacheScope: "owner" }));
  expect(screen.queryByRole("heading", { name: "TopBot results" })).toBeNull();
  expect(botsApi.getForwardResults).not.toHaveBeenCalled();
});

it("rejects malformed bot links before fetching", () => {
  renderPage("/bot-results/32bad");
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(botsApi.listConfigsWithCacheScope).not.toHaveBeenCalled();
});
