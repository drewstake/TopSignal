import { describe, expect, it } from "vitest";

import { buildJournalQuery, draftToUpdatePayload, getTodayTradingDateIso, parseTagsInput } from "./journalUtils";

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

describe("draftToUpdatePayload", () => {
  it("includes entry version for optimistic concurrency", () => {
    const payload = draftToUpdatePayload({
      title: "Session notes",
      mood: "Neutral",
      tagsInput: "nq, discipline",
      body: "Kept risk tight.",
      version: 7,
      is_archived: false,
    });

    expect(payload.version).toBe(7);
  });
});

describe("getTodayTradingDateIso", () => {
  it("uses New York calendar day boundaries", () => {
    expect(getTodayTradingDateIso(new Date("2026-03-02T03:30:00.000Z"))).toBe("2026-03-01");
    expect(getTodayTradingDateIso(new Date("2026-03-02T14:00:00.000Z"))).toBe("2026-03-02");
  });
});
