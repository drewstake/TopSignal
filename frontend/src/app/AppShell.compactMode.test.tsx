// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountInfo } from "../lib/types";

const { getSelectableAccountsLocalFirstMock, getSelectableAccountsMock, refreshTradesMock } = vi.hoisted(() => ({
  getSelectableAccountsLocalFirstMock: vi.fn(),
  getSelectableAccountsMock: vi.fn(),
  refreshTradesMock: vi.fn(),
}));

vi.mock("../lib/appShellApi", () => ({
  getSelectableAccountsLocalFirst: getSelectableAccountsLocalFirstMock,
  getSelectableAccounts: getSelectableAccountsMock,
  refreshTrades: refreshTradesMock,
}));

import { AppShell } from "./AppShell";

const activeAccount: AccountInfo = {
  id: 10,
  name: "Test Account",
  provider_name: "Test Account",
  custom_display_name: null,
  trade_data_source: "csv_import",
  balance: 50000,
  provider_data_stale: false,
  last_seen_at: null,
  status: "ACTIVE",
  account_state: "ACTIVE",
  is_main: true,
  is_archived: false,
  can_trade: null,
  is_visible: true,
  last_trade_at: null,
};

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  getSelectableAccountsLocalFirstMock.mockReset();
  getSelectableAccountsMock.mockReset();
  refreshTradesMock.mockReset();
});

function renderAppShell(initialEntry: string) {
  Element.prototype.scrollIntoView = vi.fn();
  getSelectableAccountsLocalFirstMock.mockResolvedValue([activeAccount]);
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <AppShell />,
        children: [
          { index: true, element: <div>Dashboard content</div> },
          { path: "trades", element: <div>Trades content</div> },
        ],
      },
    ],
    { initialEntries: [initialEntry] },
  );

  render(<RouterProvider router={router} />);
}

describe("AppShell compact mode", () => {
  it("places a persisted live-updating dashboard switch immediately after Demo Mode", async () => {
    renderAppShell("/?account=10");
    const demoSwitch = await screen.findByRole("switch", { name: "Demo mode" });
    expect(screen.getByRole("option", { name: "Test Account — Live CSV" })).not.toBeNull();
    expect(screen.queryByRole("option", { name: /\b10\b/ })).toBeNull();
    const compactSwitch = screen.getByRole("switch", { name: "Compact Dashboard" });
    const headerContainer = screen.getByRole("banner").firstElementChild as HTMLElement;
    const main = screen.getByRole("main");

    expect(demoSwitch.compareDocumentPosition(compactSwitch) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(compactSwitch.getAttribute("aria-checked")).toBe("false");
    expect(headerContainer.className).toContain("max-w-[1400px]");
    expect(headerContainer.className).toContain("sm:gap-4");
    expect(headerContainer.className).toContain("sm:py-4");
    expect(headerContainer.className).not.toContain("sm:gap-3");
    expect(headerContainer.className).not.toContain("sm:py-3");
    expect(main.className).toContain("max-w-[1400px]");
    expect(main.className).toContain("py-6");

    fireEvent.click(compactSwitch);

    expect(compactSwitch.getAttribute("aria-checked")).toBe("true");
    expect(window.localStorage.getItem("topsignal.compactMode")).toBe("true");
    expect(headerContainer.className).toContain("max-w-[1920px]");
    expect(headerContainer.className).not.toContain("max-w-[1400px]");
    expect(headerContainer.className).toContain("sm:gap-3");
    expect(headerContainer.className).toContain("sm:py-3");
    expect(main.className).toContain("max-w-[1920px]");
    expect(main.className).toContain("pb-6");
    expect(main.className).toContain("pt-2");
    expect(main.className).not.toContain("py-6");
  });

  it("keeps non-dashboard routes on the baseline shell when compact mode is stored", async () => {
    window.localStorage.setItem("topsignal.compactMode", "true");
    renderAppShell("/trades?account=10");

    const demoSwitch = await screen.findByRole("switch", { name: "Demo mode" });
    const headerContainer = screen.getByRole("banner").firstElementChild as HTMLElement;
    const main = screen.getByRole("main");

    expect(screen.queryByRole("switch", { name: "Compact Dashboard" })).toBeNull();
    expect(demoSwitch.className).toContain("self-end");
    expect(headerContainer.className).toContain("max-w-[1400px]");
    expect(headerContainer.className).not.toContain("max-w-[1920px]");
    expect(headerContainer.className).toContain("sm:gap-4");
    expect(headerContainer.className).toContain("sm:py-4");
    expect(main.className).toContain("max-w-[1400px]");
    expect(main.className).toContain("py-6");
    expect(main.className).not.toContain("max-w-[1920px]");
    expect(main.className).not.toContain("pt-2");
    expect(main.className).toContain("lg:overflow-hidden");
  });
});
