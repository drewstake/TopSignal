import { describe, expect, it } from "vitest";

import {
  buildJournalImageMarkdown,
  extractPersistedJournalImageIds,
  insertJournalImageMarkdown,
  removeJournalImageMarkdown,
  stripJournalImageMarkdown,
} from "./journalImages";

describe("insertJournalImageMarkdown", () => {
  it("inserts image markdown on its own line when pasting into text", () => {
    const result = insertJournalImageMarkdown("First lineSecond line", buildJournalImageMarkdown(12), 10, 10);

    expect(result.body).toBe("First line\n![Journal image](journal-image://12)\nSecond line");
    expect(result.selectionStart).toBe(result.selectionEnd);
  });
});

describe("extractPersistedJournalImageIds", () => {
  it("returns unique image ids in body order", () => {
    const body = [
      "Start",
      "![Journal image](journal-image://42)",
      "Middle",
      "![Journal image](journal-image://7)",
      "![Journal image](journal-image://42)",
    ].join("\n");

    expect(extractPersistedJournalImageIds(body)).toEqual([42, 7]);
  });
});

describe("removeJournalImageMarkdown", () => {
  it("replaces removed image references with a placeholder", () => {
    const body = [
      "Setup notes",
      "![Journal image](journal-image://12)",
      "",
      "Follow-through",
    ].join("\n");

    expect(removeJournalImageMarkdown(body, 12)).toBe(["Setup notes", "[removed image]", "", "Follow-through"].join("\n"));
  });
});

describe("stripJournalImageMarkdown", () => {
  it("removes inline image refs from list previews", () => {
    const body = "A setup screenshot ![Journal image](journal-image://12) with context.";

    expect(stripJournalImageMarkdown(body)).toBe("A setup screenshot [image] with context.");
  });
});
