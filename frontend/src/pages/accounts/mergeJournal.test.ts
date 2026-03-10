import { describe, expect, it } from "vitest";

import type { AccountInfo, JournalMergeResult } from "../../lib/types";
import {
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
    balance: 0,
    status: "MISSING",
    account_state: "MISSING",
    is_main: false,
    can_trade: false,
    is_visible: true,
    last_trade_at: null,
  },
  {
    id: 7002,
    name: "New XFA",
    provider_name: "New XFA",
    custom_display_name: null,
    balance: 0,
    status: "ACTIVE",
    account_state: "ACTIVE",
    is_main: true,
    can_trade: true,
    is_visible: true,
    last_trade_at: null,
  },
  {
    id: 7003,
    name: "Hidden XFA",
    provider_name: "Hidden XFA",
    custom_display_name: null,
    balance: 0,
    status: "HIDDEN",
    account_state: "HIDDEN",
    is_main: false,
    can_trade: true,
    is_visible: false,
    last_trade_at: null,
  },
];

describe("filterMergeSourceAccounts", () => {
  it("matches old accounts by name or id", () => {
    expect(filterMergeSourceAccounts(baseAccounts, "hidden").map((account) => account.id)).toEqual([7003]);
    expect(filterMergeSourceAccounts(baseAccounts, "7001").map((account) => account.id)).toEqual([7001]);
  });
});

describe("getMergeDestinationAccounts", () => {
  it("keeps only active destination accounts", () => {
    expect(getMergeDestinationAccounts(baseAccounts).map((account) => account.id)).toEqual([7002]);
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
