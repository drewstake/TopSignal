import { describe, expect, it } from "vitest";

import { buildJournalQuery, parseTagsInput } from "./journalUtils";

describe("buildJournalQuery", () => {
  it("builds query params and omits empty mood/search values", () => {
    const query = buildJournalQuery({
      startDate: "2026-02-01",
      endDate: "2026-02-20",
      mood: "ALL",
      queryText: "  opening range  ",
      includeArchived: false,
      limit: 20,
      offset: 40,
    });

    expect(query).toEqual({
      start_date: "2026-02-01",
      end_date: "2026-02-20",
      mood: undefined,
      q: "opening range",
      include_archived: false,
      limit: 20,
      offset: 40,
    });
  });
});

describe("parseTagsInput", () => {
  it("trims, lowercases, and deduplicates comma-separated tags", () => {
    const tags = parseTagsInput(" NQ, discipline, nq,  , Playbook ");

    expect(tags).toEqual(["nq", "discipline", "playbook"]);
  });
});
