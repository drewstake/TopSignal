import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { JournalEntry } from "../../../lib/types";
import type { JournalDraft } from "../journalUtils";
import { JournalCopyActions } from "./JournalCopyActions";
import { JournalEditor } from "./JournalEditor";

const entry: JournalEntry = {
  id: 1,
  account_id: 101,
  entry_date: "2026-07-22",
  title: "Patient execution",
  mood: "Focused",
  tags: ["mnq"],
  body: "Waited for confirmation.",
  version: 1,
  stats_source: null,
  stats_json: null,
  stats_pulled_at: null,
  is_archived: false,
  created_at: "2026-07-22T14:00:00Z",
  updated_at: "2026-07-22T14:00:00Z",
};

const draft: JournalDraft = {
  title: entry.title,
  mood: entry.mood,
  tagsInput: entry.tags.join(", "),
  body: entry.body,
  version: entry.version,
  is_archived: entry.is_archived,
};

describe("Journal mobile touch targets", () => {
  it("keeps both compact copy controls at least 44px tall below the small breakpoint", () => {
    const markup = renderToStaticMarkup(
      <JournalCopyActions
        activeAction={null}
        onCopyEntry={vi.fn()}
        onCopyRecent={vi.fn()}
      />,
    );

    expect(markup).toContain("h-11 rounded-r-none");
    expect(markup).toContain("h-11 rounded-l-none");
    expect(markup.match(/sm:h-7/g)).toHaveLength(2);
  });

  it("keeps title, mood, and tags controls at least 44px tall on mobile", () => {
    const markup = renderToStaticMarkup(
      <JournalEditor
        entry={entry}
        draft={draft}
        saveState="saved"
        savingDisabled={false}
        conflictServerEntry={null}
        images={[]}
        imagesLoading={false}
        imagesError={null}
        uploadingImage={false}
        deletingEntry={false}
        onDraftChange={vi.fn()}
        onArchiveToggle={vi.fn()}
        onRetrySave={vi.fn()}
        onReloadServerVersion={vi.fn()}
        onPasteImage={vi.fn()}
        onDeleteImage={vi.fn()}
        onDeleteEntry={vi.fn()}
      />,
    );

    expect(markup.match(/h-11 text-sm sm:h-9/g)).toHaveLength(3);
  });
});
