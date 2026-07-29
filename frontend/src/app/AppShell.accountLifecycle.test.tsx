// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { accountsApi } from "../lib/api";
import { TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT } from "../lib/tradeImportEvents";
import type { AccountInfo } from "../lib/types";
import { AccountsPage } from "../pages/accounts/AccountsPage";

const {
  getSelectableAccountsLocalFirstMock,
  getSelectableAccountsMock,
  refreshTradesMock,
} = vi.hoisted(() => ({
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
import { dispatchAccountListChanged } from "../lib/accountSelection";

function account(
  id: number,
  name: string,
  source: AccountInfo["trade_data_source"],
  isMain: boolean,
): AccountInfo {
  return {
    id,
    name,
    provider_name: name,
    custom_display_name: null,
    trade_data_source: source,
    balance: null,
    provider_data_stale: source === "projectx",
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: isMain,
    is_archived: false,
    can_trade: source === "projectx" ? true : null,
    is_visible: true,
    last_trade_at: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
  getSelectableAccountsLocalFirstMock.mockReset();
  getSelectableAccountsMock.mockReset();
  refreshTradesMock.mockReset();
});

describe("AppShell account lifecycle reconciliation", () => {
  it("turns the Live account action into an enabled upload request", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const live = account(88001, "Live Active", "csv_import", true);
    getSelectableAccountsLocalFirstMock.mockResolvedValue([live]);
    const uploadRequested = vi.fn();
    window.addEventListener(TRADE_IMPORT_FILE_PICKER_REQUESTED_EVENT, uploadRequested, { once: true });
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <AppShell />,
          children: [{ index: true, element: <div>Account-aware content</div> }],
        },
      ],
      { initialEntries: ["/?account=88001"] },
    );

    render(<RouterProvider router={router} />);
    const uploadButton = await screen.findByRole("button", { name: "Upload Trade File" });
    expect((uploadButton as HTMLButtonElement).disabled).toBe(false);

    uploadButton.click();

    expect(uploadRequested).toHaveBeenCalledTimes(1);
    expect(refreshTradesMock).not.toHaveBeenCalled();
  });

  it("applies a Live replacement before reloading so no transient Express refresh starts", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const live = account(88001, "Live Active", "csv_import", false);
    const replacement = account(88002, "Live Backup", "csv_import", false);
    const expressMain = account(7101, "Express Main", "projectx", true);
    getSelectableAccountsLocalFirstMock
      .mockResolvedValueOnce([live, replacement, expressMain])
      .mockResolvedValueOnce([replacement, expressMain]);
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <AppShell />,
          children: [{ index: true, element: <div>Account-aware content</div> }],
        },
      ],
      { initialEntries: ["/?account=88001"] },
    );

    render(<RouterProvider router={router} />);
    const accountSelect = await screen.findByRole("combobox", { name: "Active Account" });
    await waitFor(() => expect((accountSelect as HTMLSelectElement).value).toBe("88001"));

    act(() => {
      dispatchAccountListChanged({
        accountId: 88001,
        action: "archived",
        replacementAccountId: 88002,
      });
    });

    await waitFor(() => expect(getSelectableAccountsLocalFirstMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect((accountSelect as HTMLSelectElement).value).toBe("88002"));
    expect(router.state.location.search).toBe("?account=88002");
    expect(screen.queryByRole("option", { name: /Live Active/ })).toBeNull();
    expect(screen.getByRole("option", { name: /Live Backup/ })).not.toBeNull();
    expect(getSelectableAccountsMock).not.toHaveBeenCalled();
  });

  it("updates the shell dropdown after an explicit provider refresh starts from a Live CSV selection", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const user = userEvent.setup();
    const live = {
      ...account(88001, "Live Selected", "csv_import", true),
      provider_sync_status: "not_applicable" as const,
    };
    const refreshedExpress = {
      ...account(7101, "Express Refreshed", "projectx", false),
      provider_data_stale: false,
      provider_sync_status: "provider_fresh" as const,
      provider_last_successful_refresh_at: "2026-07-29T15:00:00Z",
    };
    const discoveredExpress = {
      ...refreshedExpress,
      id: 7102,
      name: "Express Newly Discovered",
      provider_name: "Express Newly Discovered",
    };
    const refreshedAccounts = [live, refreshedExpress, discoveredExpress];
    getSelectableAccountsLocalFirstMock
      .mockResolvedValueOnce([live])
      .mockResolvedValueOnce(refreshedAccounts);
    const getAccountsSpy = vi.spyOn(accountsApi, "getAccounts")
      .mockResolvedValueOnce([live])
      .mockResolvedValueOnce(refreshedAccounts);
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <AppShell />,
          children: [{ path: "accounts", element: <AccountsPage /> }],
        },
      ],
      { initialEntries: ["/accounts?account=88001"] },
    );

    render(<RouterProvider router={router} />);
    const accountSelect = await screen.findByRole("combobox", { name: "Active Account" });
    await waitFor(() => expect((accountSelect as HTMLSelectElement).value).toBe("88001"));
    expect(getSelectableAccountsMock).not.toHaveBeenCalled();

    const refreshButton = await screen.findByRole("button", { name: "Refresh Express Accounts" });
    await waitFor(() => expect((refreshButton as HTMLButtonElement).disabled).toBe(false));
    await user.click(refreshButton);

    await waitFor(() => expect(getAccountsSpy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getSelectableAccountsLocalFirstMock).toHaveBeenCalledTimes(2));
    const shellOptionLabels = Array.from((accountSelect as HTMLSelectElement).options)
      .map((option) => option.textContent);
    expect(shellOptionLabels).toEqual(expect.arrayContaining([
      expect.stringContaining("Express Refreshed"),
      expect.stringContaining("Express Newly Discovered"),
    ]));
    expect((accountSelect as HTMLSelectElement).value).toBe("88001");
    expect(router.state.location.search).toBe("?account=88001");
  });

  it("replaces the local-first dropdown after provider success while preserving a Live CSV selection", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const live = {
      ...account(88001, "Live Selected", "csv_import", false),
      provider_sync_status: "not_applicable" as const,
    };
    const savedExpress = {
      ...account(7101, "Express Saved", "projectx", true),
      provider_data_stale: true,
      provider_sync_status: "cache_stale" as const,
      provider_last_successful_refresh_at: "2026-07-20T14:00:00Z",
    };
    const refreshedExpress = {
      ...savedExpress,
      name: "Express Refreshed",
      provider_name: "Express Refreshed",
      provider_data_stale: false,
      provider_sync_status: "provider_fresh" as const,
      provider_last_successful_refresh_at: "2026-07-29T15:00:00Z",
    };
    const discoveredExpress = {
      ...refreshedExpress,
      id: 7102,
      name: "Express Newly Discovered",
      provider_name: "Express Newly Discovered",
      is_main: false,
    };
    const refresh = deferred<AccountInfo[]>();
    getSelectableAccountsLocalFirstMock.mockResolvedValue([live, savedExpress]);
    getSelectableAccountsMock.mockReturnValue(refresh.promise);
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <AppShell />,
          children: [{ index: true, element: <div>Account-aware content</div> }],
        },
      ],
      { initialEntries: ["/?account=7101"] },
    );

    render(<RouterProvider router={router} />);
    const accountSelect = await screen.findByRole("combobox", { name: "Active Account" });
    await waitFor(() => expect(getSelectableAccountsMock).toHaveBeenCalledWith({ refreshProvider: true }));
    expect(screen.getByRole("status").textContent).toContain("Refreshing ProjectX accounts");

    fireEvent.change(accountSelect, { target: { value: "88001" } });
    await waitFor(() => expect((accountSelect as HTMLSelectElement).value).toBe("88001"));

    await act(async () => {
      refresh.resolve([live, refreshedExpress, discoveredExpress]);
      await refresh.promise;
    });

    await waitFor(() => expect(screen.queryByRole("option", { name: /Express Saved/ })).toBeNull());
    expect(screen.getByRole("option", { name: /Express Refreshed/ })).not.toBeNull();
    expect(screen.getByRole("option", { name: /Express Newly Discovered/ })).not.toBeNull();
    expect((accountSelect as HTMLSelectElement).value).toBe("88001");
    expect(router.state.location.search).toBe("?account=88001");
    expect(screen.getByText(/ProjectX account data refreshed successfully/)).not.toBeNull();
  });

  it("surfaces an HTTP-200 provider authentication fallback while keeping cached rows usable", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const savedExpress = {
      ...account(7101, "Express Cached", "projectx", true),
      provider_data_stale: false,
      provider_sync_status: "cache_fresh" as const,
    };
    const fallbackExpress = {
      ...savedExpress,
      provider_sync_status: "cached_fallback" as const,
      provider_sync_error_code: "projectx_auth_failed",
      provider_sync_error_message: "ProjectX rejected the saved credential for this signed-in user.",
    };
    getSelectableAccountsLocalFirstMock.mockResolvedValue([savedExpress]);
    getSelectableAccountsMock.mockResolvedValue([fallbackExpress]);
    const router = createMemoryRouter(
      [{
        path: "/",
        element: <AppShell />,
        children: [{ index: true, element: <div>Account-aware content</div> }],
      }],
      { initialEntries: ["/?account=7101"] },
    );

    render(<RouterProvider router={router} />);

    expect((await screen.findByRole("alert")).textContent).toContain(
      "ProjectX rejected the saved credential for this signed-in user.",
    );
    expect(screen.getByRole("option", { name: /cached after refresh failure/ })).not.toBeNull();
    expect((screen.getByRole("combobox", { name: "Active Account" }) as HTMLSelectElement).value).toBe("7101");
  });

  it("surfaces a structured configuration failure instead of swallowing the rejected refresh", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const savedExpress = account(7101, "Express Cached", "projectx", true);
    getSelectableAccountsLocalFirstMock.mockResolvedValue([savedExpress]);
    getSelectableAccountsMock.mockRejectedValue({
      detail: {
        code: "projectx_credentials_unavailable",
        message: "The saved ProjectX credential cannot be opened by this runtime.",
      },
    });
    const router = createMemoryRouter(
      [{
        path: "/",
        element: <AppShell />,
        children: [{ index: true, element: <div>Account-aware content</div> }],
      }],
      { initialEntries: ["/?account=7101"] },
    );

    render(<RouterProvider router={router} />);

    expect((await screen.findByRole("alert")).textContent).toContain(
      "The saved ProjectX credential cannot be opened by this runtime. Saved account data remains available.",
    );
    expect(screen.getByRole("option", { name: /Express Cached/ })).not.toBeNull();
  });
});
