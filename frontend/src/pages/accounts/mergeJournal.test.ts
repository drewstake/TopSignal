import { describe, expect, it, vi } from "vitest";

import type { AccountInfo, JournalMergeResult } from "../../lib/types";
import {
  approveMergeJournalSubmission,
  buildMergeJournalOverwriteConfirmation,
  buildMergeJournalSuccessMessage,
  filterMergeSourceAccounts,
  getMergeDestinationAccounts,
  reconcileMergeJournalForm,
  validateMergeJournalForm,
} from "./mergeJournal";

const baseAccounts: AccountInfo[] = [
  {
    id: 7001,
    name: "Old XFA",
    provider_name: "Old XFA",
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 0,
    provider_data_stale: false,
    last_seen_at: null,
    status: "MISSING",
    account_state: "MISSING",
    is_main: false,
    is_archived: false,
    can_trade: false,
    is_visible: true,
    last_trade_at: null,
  },
  {
    id: 7002,
    name: "New XFA",
    provider_name: "New XFA",
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 0,
    provider_data_stale: false,
    last_seen_at: null,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: true,
    is_archived: false,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
  },
  {
    id: 7003,
    name: "Locked XFA",
    provider_name: "Locked XFA",
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 0,
    provider_data_stale: false,
    last_seen_at: null,
    status: "LOCKED_OUT",
    account_state: "LOCKED_OUT",
    is_main: false,
    is_archived: false,
    can_trade: false,
    is_visible: true,
    last_trade_at: null,
  },
  {
    id: 7004,
    name: "Hidden XFA",
    provider_name: "Hidden XFA",
    custom_display_name: null,
    trade_data_source: "projectx",
    balance: 0,
    provider_data_stale: false,
    last_seen_at: null,
    status: "HIDDEN",
    account_state: "HIDDEN",
    is_main: false,
    is_archived: false,
    can_trade: true,
    is_visible: false,
    last_trade_at: null,
  },
];

describe("filterMergeSourceAccounts", () => {
  it("matches old accounts by name or id", () => {
    expect(filterMergeSourceAccounts(baseAccounts, "hidden").map((account) => account.id)).toEqual([7004]);
    expect(filterMergeSourceAccounts(baseAccounts, "7001").map((account) => account.id)).toEqual([7001]);
  });
});

describe("getMergeDestinationAccounts", () => {
  it("keeps active and locked-out destination accounts while excluding hidden ones", () => {
    expect(getMergeDestinationAccounts(baseAccounts).map((account) => account.id)).toEqual([7002, 7003]);
  });
});

describe("reconcileMergeJournalForm", () => {
  it("defaults the destination to the preferred account and picks a different source account", () => {
    const form = reconcileMergeJournalForm(
      {
        fromAccountId: "",
        toAccountId: "",
        onConflict: "skip",
        includeImages: true,
      },
      baseAccounts,
      getMergeDestinationAccounts(baseAccounts),
      7002,
    );

    expect(form.toAccountId).toBe("7002");
    expect(form.fromAccountId).toBe("7001");
  });
});

describe("validateMergeJournalForm", () => {
  it("rejects same-account submissions", () => {
    expect(
      validateMergeJournalForm({
        fromAccountId: "7002",
        toAccountId: "7002",
        onConflict: "skip",
        includeImages: true,
      }),
    ).toBe("Old and new account must be different.");
  });
});

describe("journal overwrite confirmation", () => {
  const accountNamesById = new Map([
    [7001, "Old XFA"],
    [7002, "New XFA"],
  ]);

  it("requires an explicit destructive confirmation for overwrite", () => {
    const form = {
      fromAccountId: "7001",
      toAccountId: "7002",
      onConflict: "overwrite" as const,
      includeImages: true,
    };
    const confirmOverwrite = vi.fn((message: string) => {
      void message;
      return false;
    });

    expect(approveMergeJournalSubmission(form, accountNamesById, confirmOverwrite)).toBe(false);
    expect(confirmOverwrite).toHaveBeenCalledWith(
      buildMergeJournalOverwriteConfirmation(form, accountNamesById),
    );
    expect(confirmOverwrite.mock.calls[0]?.[0]).toContain("Every existing destination entry");
    expect(confirmOverwrite.mock.calls[0]?.[0]).toContain("Old XFA (#7001)");
    expect(confirmOverwrite.mock.calls[0]?.[0]).toContain("New XFA (#7002)");
    expect(confirmOverwrite.mock.calls[0]?.[0]).toContain("linked images");
    expect(confirmOverwrite.mock.calls[0]?.[0]).toContain("cannot be undone");
  });

  it("keeps duplicate display names unambiguous with account IDs", () => {
    const form = {
      fromAccountId: "7001",
      toAccountId: "7002",
      onConflict: "overwrite" as const,
      includeImages: false,
    };
    const duplicateNames = new Map([
      [7001, "Express 50K"],
      [7002, "Express 50K"],
    ]);

    expect(buildMergeJournalOverwriteConfirmation(form, duplicateNames)).toContain(
      "from Express 50K (#7001) into Express 50K (#7002)",
    );
  });

  it("does not prompt for the non-destructive skip behavior", () => {
    const confirmOverwrite = vi.fn((message: string) => {
      void message;
      return false;
    });
    const form = {
      fromAccountId: "7001",
      toAccountId: "7002",
      onConflict: "skip" as const,
      includeImages: true,
    };

    expect(approveMergeJournalSubmission(form, accountNamesById, confirmOverwrite)).toBe(true);
    expect(confirmOverwrite).not.toHaveBeenCalled();
  });
});

describe("buildMergeJournalSuccessMessage", () => {
  it("includes transfer, overwrite, skip, and image details", () => {
    const result: JournalMergeResult = {
      from_account_id: 7001,
      to_account_id: 7002,
      transferred_count: 5,
      skipped_count: 2,
      overwritten_count: 1,
      image_count: 3,
    };
    const accountNamesById = new Map([
      [7001, "Old XFA"],
      [7002, "New XFA"],
    ]);

    const message = buildMergeJournalSuccessMessage(result, accountNamesById);

    expect(message).toContain("Merged 5 entries from Old XFA into New XFA.");
    expect(message).toContain("2 conflicts were skipped.");
    expect(message).toContain("1 destination entry was overwritten.");
    expect(message).toContain("3 images copied.");
  });
});
