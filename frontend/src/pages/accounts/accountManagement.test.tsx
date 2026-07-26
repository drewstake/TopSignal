import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { AccountInfo } from "../../lib/types";
import {
  AccountSelectionButton,
  AccountTableScrollArea,
} from "./accountManagement";
import { filterAccountManagementRows, loadAccountManagementRows } from "./accountManagementData";

function account(
  id: number,
  accountState: AccountInfo["account_state"],
  tradeDataSource: AccountInfo["trade_data_source"] = "projectx",
): AccountInfo {
  return {
    id,
    name: `Account ${id}`,
    provider_name: `Account ${id}`,
    custom_display_name: null,
    trade_data_source: tradeDataSource,
    balance: null,
    provider_data_stale: tradeDataSource === "projectx",
    last_seen_at: null,
    status: accountState,
    account_state: accountState,
    is_main: false,
    is_archived: false,
    can_trade: null,
    is_visible: accountState !== "HIDDEN",
    last_trade_at: null,
  };
}

describe("account management loading", () => {
  it("loads one local snapshot without refreshing ProjectX", async () => {
    const rows = [account(1, "ACTIVE", "csv_import"), account(2, "ACTIVE")];
    const loader = vi.fn().mockResolvedValue(rows);

    await expect(loadAccountManagementRows(loader, false)).resolves.toBe(rows);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledWith({
      showInactive: true,
      showMissing: true,
      refreshProvider: false,
      bypassCache: false,
      includeArchived: true,
    });
  });

  it("uses one uncached provider request only for an explicit Express refresh", async () => {
    const loader = vi.fn().mockResolvedValue([account(2, "ACTIVE")]);

    await loadAccountManagementRows(loader, true);

    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledWith({
      showInactive: true,
      showMissing: true,
      refreshProvider: true,
      bypassCache: true,
      includeArchived: true,
    });
  });

  it("filters hidden and missing rows locally while retaining the complete cached snapshot", () => {
    const rows = [
      account(1, "ACTIVE", "csv_import"),
      account(2, "HIDDEN"),
      account(3, "MISSING"),
    ];

    rows[2] = { ...rows[2], is_archived: true };

    expect(filterAccountManagementRows(rows, { showHidden: false, showMissing: false, showArchived: false }).map(({ id }) => id)).toEqual([1]);
    expect(filterAccountManagementRows(rows, { showHidden: true, showMissing: false, showArchived: false }).map(({ id }) => id)).toEqual([1, 2]);
    expect(filterAccountManagementRows(rows, { showHidden: true, showMissing: true, showArchived: true })).toEqual(rows);
  });
});

describe("account management accessibility", () => {
  it("provides a focusable horizontal scroll region at narrow widths", () => {
    const markup = renderToStaticMarkup(
      <AccountTableScrollArea>
        <table>
          <tbody>
            <tr>
              <td>Live Funded</td>
            </tr>
          </tbody>
        </table>
      </AccountTableScrollArea>,
    );

    expect(markup).toContain("overflow-x-auto");
    expect(markup).toContain('role="region"');
    expect(markup).toContain('aria-label="Trading accounts table"');
    expect(markup).toContain('tabindex="0"');
    expect(markup).not.toContain("overflow-hidden");
  });

  it("uses a native button so Enter and Space activate account selection", () => {
    const markup = renderToStaticMarkup(
      <AccountSelectionButton accountName="Live Funded" active onSelect={() => undefined} />,
    );

    expect(markup).toContain('<button type="button"');
    expect(markup).toContain('aria-label="Select Live Funded account"');
    expect(markup).toContain('aria-pressed="true"');
    expect(markup).toContain("focus-visible:ring-2");
    expect(markup).toContain("min-h-11");
    expect(markup).toContain("sm:min-h-0");
  });
});
