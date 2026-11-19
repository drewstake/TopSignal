// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { AppShell } from "../../app/AppShell";
import { ScannerPage } from "./ScannerPage";
import { getDemoApiResponse } from "../../lib/demoData";
import { requestJson } from "../../lib/api";

vi.mock("../../lib/api", async (original) => ({ ...await original<typeof import("../../lib/api")>(), requestJson: vi.fn() }));
vi.mock("../../lib/appShellApi", () => ({ getSelectableAccountsLocalFirst: async () => [],
  getSelectableAccounts: async () => [], refreshTrades: vi.fn() }));

afterEach(() => { cleanup(); vi.clearAllMocks(); window.localStorage.clear(); });

it("shows a new raw alert on Bot and opens Scanner without a selected account", async () => {
  Element.prototype.scrollIntoView = vi.fn();
  let calls = 0;
  vi.mocked(requestJson).mockImplementation(async (path) => {
    if (path === "/api/scanner/alerts") return calls++ === 0 ? { cursor: 0, events: [] } :
      { cursor: 1, events: [{ id: 1, title: "AMD headline", tickers: ["AMD"], received: 1795101992,
        source: "Hunterbrook newsletter", link: "https://newsletter.hntrbrk.com" }] };
    if (path === "/api/scanner") return getDemoApiResponse(path)?.data;
    throw new Error("Unexpected request");
  });
  const router = createMemoryRouter([{ path: "/", element: <AppShell />, children: [
    { path: "bot", element: <h1>Bot workspace</h1> }, { path: "scanner", element: <ScannerPage /> },
  ] }], { initialEntries: ["/bot"] });
  render(<RouterProvider router={router} />);
  const nav = await screen.findByRole("navigation", {name:"Main navigation"});
  const labels = within(nav).getAllByRole("link").map(a => a.getAttribute("aria-label"));
  expect(labels[labels.indexOf("Bot")+1]).toBe("Scanner");
  expect(screen.getByRole("heading", {name:"Bot workspace"})).toBeTruthy();
  await waitFor(() => expect(screen.getByLabelText("Scanner alert").textContent).toContain("AMD headline"), {timeout:4500});
  fireEvent.click(within(nav).getByRole("link", {name:"Scanner"}));
  expect(await screen.findByText("Cause unknown")).toBeTruthy();
  expect(router.state.location.pathname).toBe("/scanner");
  expect(getDemoApiResponse("/api/scanner/alerts")?.data).toEqual({cursor:1,events:[]});
}, 8000);
