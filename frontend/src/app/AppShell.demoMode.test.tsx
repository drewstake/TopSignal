// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountInfo } from "../lib/types";

const {
  getAccessTokenSyncMock,
  getCurrentUserEmailSyncMock,
  getSelectableAccountsLocalFirstMock,
  getSelectableAccountsMock,
  refreshTradesMock,
  reloadPageMock,
  replacePagePathMock,
  signOutSupabaseMock,
  hasActiveLiveMutationRequestsMock,
} = vi.hoisted(() => ({
  getAccessTokenSyncMock: vi.fn(() => "real-access-token" as string | null),
  getCurrentUserEmailSyncMock: vi.fn(() => "real.user@example.com" as string | null),
  getSelectableAccountsLocalFirstMock: vi.fn(),
  getSelectableAccountsMock: vi.fn(),
  refreshTradesMock: vi.fn(),
  reloadPageMock: vi.fn(),
  replacePagePathMock: vi.fn(),
  signOutSupabaseMock: vi.fn(async () => undefined),
  hasActiveLiveMutationRequestsMock: vi.fn(() => false),
}));

vi.mock("../lib/appShellApi", () => ({
  getSelectableAccountsLocalFirst: getSelectableAccountsLocalFirstMock,
  getSelectableAccounts: getSelectableAccountsMock,
  refreshTrades: refreshTradesMock,
}));

vi.mock("../lib/supabase", () => ({
  getAccessTokenSync: getAccessTokenSyncMock,
  getCurrentUserEmailSync: getCurrentUserEmailSyncMock,
  hasSupabaseConfig: true,
  signOutSupabase: signOutSupabaseMock,
}));

vi.mock("./pageNavigation", () => ({
  reloadPage: reloadPageMock,
  replacePagePath: replacePagePathMock,
}));

vi.mock("../lib/liveMutationState", () => ({
  hasActiveLiveMutationRequests: hasActiveLiveMutationRequestsMock,
}));

import { clearLiveModeReturnSnapshot, readLiveModeReturnSnapshot } from "../lib/accountSelection";
import { AppShell } from "./AppShell";

const demoAccount: AccountInfo = {
  id: 910001,
  name: "50KTC-DEMO-Main-Day-Trade",
  provider_name: "50KTC-DEMO-Main-Day-Trade",
  custom_display_name: "Demo Main — Intraday",
  trade_data_source: "projectx",
  balance: 50_820,
  provider_data_stale: false,
  last_seen_at: "2026-07-24T17:39:00.000Z",
  status: "active",
  account_state: "ACTIVE",
  is_main: true,
  is_archived: false,
  can_trade: true,
  is_visible: true,
  last_trade_at: "2026-07-24T17:39:00.000Z",
};

const liveAccount: AccountInfo = {
  ...demoAccount,
  id: 7301,
  name: "LIVE-SECONDARY",
  provider_name: "LIVE-SECONDARY",
  custom_display_name: "Live Secondary",
  is_main: false,
};

function renderDemoShell() {
  Element.prototype.scrollIntoView = vi.fn();
  const router = createMemoryRouter(
    [{ path: "/", element: <AppShell />, children: [{ index: true, element: <div>Demo dashboard</div> }] }],
    { initialEntries: ["/?account=910001"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("AppShell Demo Mode policy", () => {
  beforeEach(() => {
    clearLiveModeReturnSnapshot();
    window.localStorage.clear();
    window.localStorage.setItem("topsignal.demoMode", "true");
    getSelectableAccountsLocalFirstMock.mockResolvedValue([demoAccount]);
    getSelectableAccountsMock.mockResolvedValue([demoAccount]);
    hasActiveLiveMutationRequestsMock.mockReturnValue(false);
  });

  afterEach(() => {
    cleanup();
    clearLiveModeReturnSnapshot();
    window.localStorage.clear();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("keeps provenance and keyboard access visible while disabling live account actions", async () => {
    const { container } = renderDemoShell();

    const skipLink = await screen.findByRole("link", { name: "Skip to main content" });
    const main = screen.getByRole("main");
    const provenance = screen.getByRole("note", {
      name: "Demonstration data, read only, scenario as of Jul 24, 2026",
    });
    const liveAction = screen.getByRole("button", { name: "Demo Data — Read Only" });

    expect(container.querySelector("a")).toBe(skipLink);
    expect(skipLink.getAttribute("href")).toBe("#app-main-content");
    expect(main.id).toBe("app-main-content");
    expect(main.tabIndex).toBe(-1);
    expect(provenance.textContent).toContain("Demo data · read only · scenario as of Jul 24, 2026");
    expect((liveAction as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("option", { name: "Demo Main — Intraday (910001)" })).not.toBeNull();
    expect(screen.queryByText("real.user@example.com")).toBeNull();
    expect(getCurrentUserEmailSyncMock).not.toHaveBeenCalled();
    expect(getAccessTokenSyncMock).not.toHaveBeenCalled();
    expect(refreshTradesMock).not.toHaveBeenCalled();
    expect(getSelectableAccountsMock).not.toHaveBeenCalled();
  });

  it("labels sign-out as a live-session action and requires explicit confirmation", async () => {
    const confirmMock = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderDemoShell();
    const signOut = await screen.findByRole("button", { name: "Sign out live session" });

    fireEvent.click(signOut);
    expect(confirmMock).toHaveBeenCalledWith(
      "This signs out your real TopSignal session. Demo data itself is not an account. Continue?",
    );
    expect(signOutSupabaseMock).not.toHaveBeenCalled();

    confirmMock.mockReturnValue(true);
    fireEvent.click(signOut);
    await waitFor(() => expect(signOutSupabaseMock).toHaveBeenCalledTimes(1));
  });

  it("composes Demo Mode with the dashboard-only compact shell", async () => {
    window.localStorage.setItem("topsignal.compactMode", "true");
    renderDemoShell();

    const compactSwitch = await screen.findByRole("switch", { name: "Compact Dashboard" });
    const headerContainer = screen.getByRole("banner").firstElementChild as HTMLElement;
    const main = screen.getByRole("main");

    expect(compactSwitch.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("note")).not.toBeNull();
    expect(headerContainer.className).toContain("max-w-[1920px]");
    expect(main.className).toContain("max-w-[1920px]");
  });

  it("captures the exact live route and account before entering Demo, then reloads", async () => {
    window.localStorage.setItem("topsignal.demoMode", "false");
    getSelectableAccountsLocalFirstMock.mockResolvedValue([liveAccount]);
    getSelectableAccountsMock.mockResolvedValue([liveAccount]);

    Element.prototype.scrollIntoView = vi.fn();
    const router = createMemoryRouter(
      [{ path: "/", element: <AppShell />, children: [{ index: true, element: <div>Live dashboard</div> }] }],
      { initialEntries: ["/?account=7301"] },
    );
    render(<RouterProvider router={router} />);

    fireEvent.click(await screen.findByRole("switch", { name: "Demo mode" }));

    await waitFor(() => expect(reloadPageMock).toHaveBeenCalledTimes(1));
    expect(window.localStorage.getItem("topsignal.demoMode")).toBe("true");
    expect(readLiveModeReturnSnapshot()).toMatchObject({ accountId: 7301, path: "/?account=7301" });
    expect(replacePagePathMock).not.toHaveBeenCalled();
  });

  it("does not cross the Demo boundary while a live mutation is still in flight", async () => {
    window.localStorage.setItem("topsignal.demoMode", "false");
    getSelectableAccountsLocalFirstMock.mockResolvedValue([liveAccount]);
    getSelectableAccountsMock.mockResolvedValue([liveAccount]);
    hasActiveLiveMutationRequestsMock.mockReturnValue(true);
    Element.prototype.scrollIntoView = vi.fn();
    const router = createMemoryRouter(
      [{ path: "/", element: <AppShell />, children: [{ index: true, element: <div>Live dashboard</div> }] }],
      { initialEntries: ["/?account=7301"] },
    );
    render(<RouterProvider router={router} />);

    fireEvent.click(await screen.findByRole("switch", { name: "Demo mode" }));

    expect(hasActiveLiveMutationRequestsMock).toHaveBeenCalled();
    expect(
      await screen.findByText("Finish the current live save, import, provider refresh, or backtest before entering Demo Mode."),
    ).toBeTruthy();
    expect(window.localStorage.getItem("topsignal.demoMode")).toBe("false");
    expect(reloadPageMock).not.toHaveBeenCalled();
    expect(replacePagePathMock).not.toHaveBeenCalled();
  });

  it("keeps this tab's live route instead of another tab's snapshot on storage-driven entry", async () => {
    window.localStorage.setItem("topsignal.demoMode", "false");
    window.localStorage.setItem("topsignal.demoReturnScope", "anonymous");
    window.localStorage.setItem(
      "topsignal.demoReturnSnapshot:anonymous",
      JSON.stringify({ accountId: 7001, path: "/accounts?account=7001", scope: "anonymous" }),
    );
    getSelectableAccountsLocalFirstMock.mockResolvedValue([liveAccount]);
    getSelectableAccountsMock.mockResolvedValue([liveAccount]);
    Element.prototype.scrollIntoView = vi.fn();
    const router = createMemoryRouter(
      [{
        path: "/",
        element: <AppShell />,
        children: [{ path: "trades", element: <div>Receiving tab trades</div> }],
      }],
      { initialEntries: ["/trades?account=7301#fills"] },
    );
    render(<RouterProvider router={router} />);

    await screen.findByText("Receiving tab trades");
    await waitFor(() => expect(readLiveModeReturnSnapshot()).toMatchObject({
      accountId: 7301,
      path: "/trades?account=7301#fills",
    }));

    window.localStorage.setItem("topsignal.demoMode", "true");
    window.dispatchEvent(new StorageEvent("storage", {
      key: "topsignal.demoMode",
      oldValue: "false",
      newValue: "true",
    }));

    await waitFor(() => expect(reloadPageMock).toHaveBeenCalledTimes(1));
    expect(readLiveModeReturnSnapshot()).toMatchObject({
      accountId: 7301,
      path: "/trades?account=7301#fills",
    });
    expect(window.localStorage.getItem("topsignal.demoReturnScope")).toBeNull();
    expect(window.sessionStorage.getItem("topsignal.demoReturnScope")).not.toBeNull();
  });

  it("restores the captured live path on local exit and native cross-tab exit", async () => {
    const snapshot = { accountId: 7301, path: "/trades?account=7301#fills", scope: "anonymous" };
    window.localStorage.setItem("topsignal.demoReturnScope", "anonymous");
    window.localStorage.setItem("topsignal.demoReturnSnapshot:anonymous", JSON.stringify(snapshot));
    const localShell = renderDemoShell();

    fireEvent.click(await screen.findByRole("switch", { name: "Demo mode" }));
    await waitFor(() => expect(replacePagePathMock).toHaveBeenCalledWith(snapshot.path));
    expect(reloadPageMock).toHaveBeenCalledTimes(1);

    // A real mode change reloads the document. Remount before simulating the
    // independent cross-tab exit so the live-mode snapshot effect from the
    // mocked (non-reloading) document cannot overwrite this tab's fixture.
    localShell.unmount();
    clearLiveModeReturnSnapshot();
    replacePagePathMock.mockClear();
    reloadPageMock.mockClear();
    window.localStorage.setItem("topsignal.demoMode", "true");
    window.localStorage.setItem("topsignal.demoReturnScope", "anonymous");
    window.localStorage.setItem("topsignal.demoReturnSnapshot:anonymous", JSON.stringify(snapshot));
    renderDemoShell();
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "topsignal.demoMode",
        oldValue: "true",
        newValue: "false",
      }),
    );

    await waitFor(() => expect(replacePagePathMock).toHaveBeenCalledWith(snapshot.path));
    expect(reloadPageMock).toHaveBeenCalledTimes(1);
  });
});
