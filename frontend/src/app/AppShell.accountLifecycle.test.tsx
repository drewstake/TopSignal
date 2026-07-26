// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountInfo } from "../lib/types";

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

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  getSelectableAccountsLocalFirstMock.mockReset();
  getSelectableAccountsMock.mockReset();
  refreshTradesMock.mockReset();
});

describe("AppShell account lifecycle reconciliation", () => {
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
    expect((accountSelect as HTMLSelectElement).value).toBe("88001");

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
});
