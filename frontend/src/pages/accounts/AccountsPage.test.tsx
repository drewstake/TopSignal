// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ACCOUNT_LIST_CHANGED_EVENT,
  type AccountListChangedDetail,
} from "../../lib/accountSelection";
import { accountsApi } from "../../lib/api";
import type { AccountInfo } from "../../lib/types";
import { AccountsPage } from "./AccountsPage";

function account({
  id,
  name,
  source,
  main = false,
  archived = false,
}: {
  id: number;
  name: string;
  source: AccountInfo["trade_data_source"];
  main?: boolean;
  archived?: boolean;
}): AccountInfo {
  return {
    id,
    name,
    provider_name: name,
    custom_display_name: null,
    trade_data_source: source,
    balance: source === "projectx" ? 50_000 : null,
    provider_data_stale: source === "projectx",
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: main,
    is_archived: archived,
    can_trade: source === "projectx" ? true : null,
    is_visible: true,
    last_trade_at: "2026-07-25T14:30:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function renderAccountsPage(initialEntry = "/accounts") {
  const router = createMemoryRouter(
    [{ path: "/accounts", element: <AccountsPage /> }],
    { initialEntries: [initialEntry] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AccountsPage local-first management", () => {
  it("loads one archived-inclusive local snapshot and never requests ProjectX on mount", async () => {
    const live = account({ id: 88001, name: "Live Funded", source: "csv_import", main: true });
    const express = account({ id: 7101, name: "Express Cached", source: "projectx" });
    const getAccounts = vi.spyOn(accountsApi, "getAccounts").mockResolvedValue([live, express]);
    const fetchSpy = vi.fn(() => Promise.reject(new Error("network must stay idle")));
    vi.stubGlobal("fetch", fetchSpy);

    renderAccountsPage("/accounts?account=88001");

    expect(await screen.findByRole("button", { name: "Select Live Funded account" })).not.toBeNull();
    expect(getAccounts).toHaveBeenCalledTimes(1);
    expect(getAccounts).toHaveBeenCalledWith({
      showInactive: true,
      showMissing: true,
      refreshProvider: false,
      bypassCache: false,
      includeArchived: true,
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByText("Stale data")).not.toBeNull();
  });

  it("coalesces repeated explicit Express refresh clicks into one provider request", async () => {
    const live = account({ id: 88001, name: "Live Funded", source: "csv_import", main: true });
    const refresh = deferred<AccountInfo[]>();
    const getAccounts = vi
      .spyOn(accountsApi, "getAccounts")
      .mockResolvedValueOnce([live])
      .mockReturnValueOnce(refresh.promise);

    renderAccountsPage("/accounts?account=88001");
    const refreshButton = await screen.findByRole("button", { name: "Refresh Express Accounts" });
    await waitFor(() => expect((refreshButton as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(refreshButton);
    fireEvent.click(refreshButton);

    expect(getAccounts).toHaveBeenCalledTimes(2);
    expect(getAccounts.mock.calls[1]?.[0]).toMatchObject({
      refreshProvider: true,
      bypassCache: true,
      includeArchived: true,
    });

    await act(async () => {
      refresh.resolve([live]);
      await refresh.promise;
    });
    await waitFor(() => expect((refreshButton as HTMLButtonElement).disabled).toBe(false));
  });

  it("keeps cached Live and stale Express rows usable when explicit refresh fails", async () => {
    const user = userEvent.setup();
    const live = account({ id: 88001, name: "Live Funded", source: "csv_import", main: true });
    const express = account({ id: 7101, name: "Express Cached", source: "projectx" });
    vi.spyOn(accountsApi, "getAccounts")
      .mockResolvedValueOnce([live, express])
      .mockRejectedValueOnce(new Error("ProjectX unavailable"));

    renderAccountsPage("/accounts?account=88001");
    await user.click(await screen.findByRole("button", { name: "Refresh Express Accounts" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Express refresh failed. Saved account data is still available. ProjectX unavailable",
    );
    expect(screen.getByRole("button", { name: "Select Live Funded account" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "Select Express Cached account" })).not.toBeNull();
    expect(screen.getByText("Stale data")).not.toBeNull();
  });
});

describe("AccountsPage accessibility and Live lifecycle", () => {
  it("activates account selection with Enter and Space and exposes a narrow-width scroll region", async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 320 });
    const first = account({ id: 88001, name: "Live One", source: "csv_import", main: true });
    const second = account({ id: 88002, name: "Live Two", source: "csv_import" });
    vi.spyOn(accountsApi, "getAccounts").mockResolvedValue([first, second]);
    vi.spyOn(accountsApi, "getLastTrade").mockResolvedValue({
      account_id: second.id,
      last_trade_at: second.last_trade_at,
      source: "local",
    });
    const router = renderAccountsPage("/accounts?account=88001");

    const scrollRegion = await screen.findByRole("region", { name: "Trading accounts table" });
    expect(scrollRegion.classList.contains("overflow-x-auto")).toBe(true);
    expect(scrollRegion.getAttribute("tabindex")).toBe("0");

    const secondButton = await screen.findByRole("button", { name: "Select Live Two account" });
    secondButton.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(router.state.location.search).toBe("?account=88002"));

    const firstButton = screen.getByRole("button", { name: "Select Live One account" });
    firstButton.focus();
    await user.keyboard(" ");
    await waitFor(() => expect(router.state.location.search).toBe("?account=88001"));
  });

  it("archives and restores only Live accounts, reconciles active/main selection, and emits list changes", async () => {
    const user = userEvent.setup();
    const liveMain = account({ id: 88001, name: "Live Main", source: "csv_import", main: true });
    const express = account({ id: 7101, name: "Express Replacement", source: "projectx" });
    const archivedLive = { ...liveMain, is_main: false, is_archived: true };
    const replacementMain = { ...express, is_main: true };
    const restoredLive = { ...archivedLive, is_archived: false };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(accountsApi, "getAccounts")
      .mockResolvedValueOnce([liveMain, express])
      .mockResolvedValueOnce([archivedLive, replacementMain])
      .mockResolvedValueOnce([restoredLive, replacementMain]);
    const archive = vi.spyOn(accountsApi, "archiveLiveAccount").mockResolvedValue({
      account_id: liveMain.id,
      is_archived: true,
      is_main: false,
      replacement_main_account_id: express.id,
    });
    const restore = vi.spyOn(accountsApi, "unarchiveLiveAccount").mockResolvedValue({
      account_id: liveMain.id,
      is_archived: false,
      is_main: false,
      replacement_main_account_id: null,
    });
    const changes: AccountListChangedDetail[] = [];
    const listener = (event: Event) => {
      changes.push((event as CustomEvent<AccountListChangedDetail>).detail);
    };
    window.addEventListener(ACCOUNT_LIST_CHANGED_EVENT, listener);
    const router = renderAccountsPage("/accounts?account=88001");

    await user.click(await screen.findByRole("button", { name: "Archive Live Main" }));

    await waitFor(() => expect(router.state.location.search).toBe("?account=7101"));
    expect(archive).toHaveBeenCalledWith(88001, 7101);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("switch the active account to Express account Express Replacement and refresh ProjectX"));
    expect(screen.queryByRole("button", { name: "Select Live Main account" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive Express Replacement" })).toBeNull();

    await user.click(screen.getByRole("switch", { name: "Show archived accounts" }));
    const archivedSelection = await screen.findByRole("button", { name: "Select Live Main account" });
    expect((archivedSelection as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Archived")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Set Main" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Restore Live Main" }));

    await waitFor(() => expect(router.state.location.search).toBe("?account=88001"));
    expect(restore).toHaveBeenCalledWith(88001);
    expect((screen.getByRole("button", { name: "Select Live Main account" }) as HTMLButtonElement).disabled).toBe(false);
    expect(changes).toEqual([
      { accountId: 88001, action: "archived", replacementAccountId: 7101 },
      { accountId: 88001, action: "unarchived", replacementAccountId: 88001 },
    ]);

    window.removeEventListener(ACCOUNT_LIST_CHANGED_EVENT, listener);
  });

  it("keeps an active non-main archive on another Live account and announces that choice", async () => {
    const user = userEvent.setup();
    const activeLive = account({ id: 88001, name: "Live Active", source: "csv_import" });
    const backupLive = account({ id: 88002, name: "Live Backup", source: "csv_import" });
    const expressMain = account({ id: 7101, name: "Express Main", source: "projectx", main: true });
    const archivedLive = { ...activeLive, is_archived: true };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const getAccounts = vi.spyOn(accountsApi, "getAccounts")
      .mockResolvedValueOnce([activeLive, backupLive, expressMain])
      .mockResolvedValueOnce([archivedLive, backupLive, expressMain]);
    const archive = vi.spyOn(accountsApi, "archiveLiveAccount").mockResolvedValue({
      account_id: activeLive.id,
      is_archived: true,
      is_main: false,
      replacement_main_account_id: null,
    });
    const changes: AccountListChangedDetail[] = [];
    const listener = (event: Event) => changes.push((event as CustomEvent<AccountListChangedDetail>).detail);
    window.addEventListener(ACCOUNT_LIST_CHANGED_EVENT, listener);
    const router = renderAccountsPage("/accounts?account=88001");

    await user.click(await screen.findByRole("button", { name: "Archive Live Active" }));

    await waitFor(() => expect(router.state.location.search).toBe("?account=88002"));
    expect(archive).toHaveBeenCalledWith(88001, undefined);
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Live Backup will become active"));
    expect(confirm).not.toHaveBeenCalledWith(expect.stringContaining("refresh ProjectX"));
    expect(changes).toEqual([
      { accountId: 88001, action: "archived", replacementAccountId: 88002 },
    ]);
    expect(getAccounts).toHaveBeenCalledTimes(2);
    for (const [options] of getAccounts.mock.calls) {
      expect(options).toMatchObject({ refreshProvider: false });
    }

    window.removeEventListener(ACCOUNT_LIST_CHANGED_EVENT, listener);
  });
});
