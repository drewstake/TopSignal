// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountInfo } from "../lib/types";

const {
  getAccessTokenMock,
  getAccessTokenSyncMock,
  getSelectableAccountsLocalFirstMock,
  getSelectableAccountsMock,
  reloadPageMock,
  replacePagePathMock,
} = vi.hoisted(() => ({
  getAccessTokenMock: vi.fn<() => Promise<string | null>>(async () => null),
  getAccessTokenSyncMock: vi.fn<() => string | null>(() => null),
  getSelectableAccountsLocalFirstMock: vi.fn(),
  getSelectableAccountsMock: vi.fn(),
  reloadPageMock: vi.fn(),
  replacePagePathMock: vi.fn(),
}));

vi.mock("../lib/appShellApi", () => ({
  getSelectableAccountsLocalFirst: getSelectableAccountsLocalFirstMock,
  getSelectableAccounts: getSelectableAccountsMock,
  refreshTrades: vi.fn(),
}));

vi.mock("../lib/supabase", () => ({
  getAccessToken: getAccessTokenMock,
  getAccessTokenSync: getAccessTokenSyncMock,
  getCurrentUserEmailSync: vi.fn(() => null),
  hasSupabaseConfig: false,
  signOutSupabase: vi.fn(async () => undefined),
}));

vi.mock("./pageNavigation", () => ({
  reloadPage: reloadPageMock,
  replacePagePath: replacePagePathMock,
}));

import { accountsApi } from "../lib/api";
import { writeStoredAccountId } from "../lib/accountSelection";
import { liveMutationLeaseInternals } from "../lib/liveMutationState";
import { AppShell } from "./AppShell";

const mainAccount: AccountInfo = {
  id: 7301,
  name: "LIVE-MAIN",
  provider_name: "LIVE-MAIN",
  custom_display_name: "Live Main",
  trade_data_source: "projectx",
  balance: 50_000,
  provider_data_stale: false,
  last_seen_at: "2026-07-24T15:00:00.000Z",
  status: "active",
  account_state: "ACTIVE",
  is_main: true,
  is_archived: false,
  can_trade: true,
  is_visible: true,
  last_trade_at: "2026-07-24T15:00:00.000Z",
};

const secondaryAccount: AccountInfo = {
  ...mainAccount,
  id: 7302,
  name: "LIVE-SECONDARY",
  provider_name: "LIVE-SECONDARY",
  custom_display_name: "Live Secondary",
  is_main: false,
};

function renderShell(initialEntry = "/", accounts = [mainAccount, secondaryAccount]) {
  Element.prototype.scrollIntoView = vi.fn();
  getSelectableAccountsLocalFirstMock.mockResolvedValue(accounts);
  getSelectableAccountsMock.mockResolvedValue(accounts);
  const router = createMemoryRouter(
    [{
      path: "/",
      element: <AppShell />,
      children: [
        { index: true, element: <div>Live dashboard</div> },
        { path: "themes", element: <div>Theme gallery</div> },
      ],
    }],
    { initialEntries: [initialEntry] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("AppShell transition safety integration", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.localStorage.setItem("topsignal.demoMode", "false");
    getAccessTokenMock.mockReset();
    getAccessTokenMock.mockResolvedValue(null);
    getAccessTokenSyncMock.mockReset();
    getAccessTokenSyncMock.mockReturnValue(null);
  });

  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("blocks Demo entry for an actual cache-writing provider GET until it settles", async () => {
    renderShell("/?account=7301", [mainAccount]);
    const demoSwitch = await screen.findByRole("switch", { name: "Demo mode" });
    let resolveFetch!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    })));

    const providerRefresh = accountsApi.getAccounts({
      showInactive: true,
      showMissing: true,
      bypassCache: true,
      refreshProvider: true,
      includeArchived: true,
    });
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    fireEvent.click(demoSwitch);
    expect(await screen.findByText(
      "Finish the current live save, import, provider refresh, or backtest before entering Demo Mode.",
    )).toBeTruthy();
    expect(window.localStorage.getItem("topsignal.demoMode")).toBe("false");
    expect(reloadPageMock).not.toHaveBeenCalled();

    resolveFetch(new Response(JSON.stringify([mainAccount]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    await expect(providerRefresh).resolves.toEqual([mainAccount]);

    fireEvent.click(demoSwitch);
    await waitFor(() => expect(reloadPageMock).toHaveBeenCalledTimes(1));
    expect(window.localStorage.getItem("topsignal.demoMode")).toBe("true");
  });

  it("rejects and reverses a storage-driven Demo entry while another tab holds a lease", async () => {
    renderShell("/?account=7301", [mainAccount]);
    await screen.findByRole("switch", { name: "Demo mode" });
    window.localStorage.setItem(
      `${liveMutationLeaseInternals.storagePrefix}other-document`,
      JSON.stringify({ owner_id: "other-document", active_count: 1, expires_at_ms: Date.now() + 30_000 }),
    );

    window.localStorage.setItem("topsignal.demoMode", "true");
    window.dispatchEvent(new StorageEvent("storage", {
      key: "topsignal.demoMode",
      oldValue: "false",
      newValue: "true",
    }));

    expect(await screen.findByText(
      "Finish the current live save, import, provider refresh, or backtest before entering Demo Mode.",
    )).toBeTruthy();
    expect(window.localStorage.getItem("topsignal.demoMode")).toBe("false");
    expect(reloadPageMock).not.toHaveBeenCalled();
  });

  it("keeps a non-main active account through Themes and when the query is absent", async () => {
    writeStoredAccountId(secondaryAccount.id);
    const router = renderShell();
    const accountSelect = await screen.findByRole("combobox", { name: /active account/i });
    expect((accountSelect as HTMLSelectElement).value).toBe(String(secondaryAccount.id));

    const themesLink = screen.getByRole("link", { name: "Themes" });
    expect(themesLink.getAttribute("href")).toBe(`/themes?account=${secondaryAccount.id}`);
    fireEvent.click(themesLink);

    await screen.findByText("Theme gallery");
    expect(router.state.location.pathname).toBe("/themes");
    expect(router.state.location.search).toBe(`?account=${secondaryAccount.id}`);
    expect((screen.getByRole("combobox", { name: /active account/i }) as HTMLSelectElement).value)
      .toBe(String(secondaryAccount.id));
  });
});
