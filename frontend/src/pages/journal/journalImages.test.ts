import { describe, expect, it } from "vitest";

import { extractPersistedJournalImageIds, sanitizeJournalBody, stripJournalImageMarkdown } from "./journalImages";

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

describe("stripJournalImageMarkdown", () => {
  it("removes inline journal image refs from note text", () => {
    const body = "A setup screenshot ![Journal image](journal-image://12) with context.";

    expect(stripJournalImageMarkdown(body)).toBe("A setup screenshot with context.");
  });

  it("collapses blank lines left behind by removed journal image refs", () => {
    const body = [
      "Setup notes",
      "![Journal image](journal-image://12)",
      "",
      "Follow-through",
    ].join("\n");

    expect(stripJournalImageMarkdown(body)).toBe(["Setup notes", "", "Follow-through"].join("\n"));
  });
});

describe("sanitizeJournalBody", () => {
  it("keeps external image markdown as a placeholder while removing journal image refs", () => {
    const body = [
      "Review",
      "![Journal image](journal-image://12)",
      "![External](https://example.com/chart.png)",
    ].join("\n");

    expect(sanitizeJournalBody(body)).toBe(["Review", "[external image]"].join("\n"));
  });
});
