import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AccountInfo, JournalMergeResult } from "../../../lib/types";
import type { MergeJournalFormState } from "../mergeJournal";
import { MergeJournalCard } from "./MergeJournalCard";

const accounts: AccountInfo[] = [
  {
    id: 8101,
    name: "Old Combine",
    provider_name: "Old Combine",
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
    id: 8102,
    name: "New Combine",
    provider_name: "New Combine",
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
    id: 8103,
    name: "Locked Combine",
    provider_name: "Locked Combine",
    custom_display_name: null,
    balance: 0,
    status: "LOCKED_OUT",
    account_state: "LOCKED_OUT",
    is_main: false,
    can_trade: false,
    is_visible: true,
    last_trade_at: null,
  },
  {
    id: 8104,
    name: "Hidden Combine",
    provider_name: "Hidden Combine",
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

const form: MergeJournalFormState = {
  fromAccountId: "8101",
  toAccountId: "8102",
  onConflict: "skip",
  includeImages: true,
};

const successResult: JournalMergeResult = {
  from_account_id: 8101,
  to_account_id: 8102,
  transferred_count: 7,
  skipped_count: 0,
  overwritten_count: 0,
  image_count: 4,
};

describe("MergeJournalCard", () => {
  it("renders a success status message", () => {
    const markup = renderToStaticMarkup(
      <MergeJournalCard
        sourceAccounts={accounts}
        destinationAccounts={[accounts[1], accounts[2]]}
        form={form}
        oldAccountSearch="old"
        loading={false}
        submitDisabled={false}
        validationMessage={null}
        errorMessage={null}
        successMessage="Merged 7 entries from Old Combine into New Combine."
        successResult={successResult}
        onOldAccountSearchChange={() => undefined}
        onFromAccountChange={() => undefined}
        onToAccountChange={() => undefined}
        onConflictChange={() => undefined}
        onIncludeImagesChange={() => undefined}
        onSubmit={() => undefined}
      />,
    );

    expect(markup).toContain('role="status"');
    expect(markup).toContain("Merged 7 entries from Old Combine into New Combine.");
    expect(markup).toContain("Transferred 7, skipped 0, overwritten 0, images 4.");
    expect(markup).toContain("Search by account name or ID");
    expect(markup).toContain("Locked Combine (#8103) - Locked out");
    expect(markup.split("Hidden Combine (#8104) - Hidden")).toHaveLength(2);
  });

  it("renders an error status message", () => {
    const markup = renderToStaticMarkup(
      <MergeJournalCard
        sourceAccounts={accounts}
        destinationAccounts={[accounts[1], accounts[2]]}
        form={form}
        oldAccountSearch=""
        loading={false}
        submitDisabled={true}
        validationMessage={null}
        errorMessage="Destination account not found."
        successMessage={null}
        successResult={null}
        onOldAccountSearchChange={() => undefined}
        onFromAccountChange={() => undefined}
        onToAccountChange={() => undefined}
        onConflictChange={() => undefined}
        onIncludeImagesChange={() => undefined}
        onSubmit={() => undefined}
      />,
    );

    expect(markup).toContain('role="alert"');
    expect(markup).toContain("Destination account not found.");
    expect(markup).toContain("disabled");
  });
});
