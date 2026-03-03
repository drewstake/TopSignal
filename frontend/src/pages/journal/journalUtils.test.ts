import { describe, expect, it } from "vitest";

import type { JournalEntry } from "../../lib/types";
import {
  buildJournalQuery,
  draftToUpdatePayload,
  getTodayTradingDateIso,
  getYesterdayTradingDateIso,
  parseTagsInput,
  reconcileDraftWithServerEntry,
} from "./journalUtils";

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

describe("getYesterdayTradingDateIso", () => {
  it("returns the prior New York calendar day", () => {
    expect(getYesterdayTradingDateIso(new Date("2026-03-02T14:00:00.000Z"))).toBe("2026-03-01");
    expect(getYesterdayTradingDateIso(new Date("2026-03-02T03:30:00.000Z"))).toBe("2026-02-28");
  });
});

describe("reconcileDraftWithServerEntry", () => {
  const serverEntry: JournalEntry = {
    id: 21,
    account_id: 13001,
    entry_date: "2026-03-02",
    title: "Session",
    mood: "Neutral",
    tags: ["nq"],
    body: "first save",
    version: 5,
    stats_source: null,
    stats_json: null,
    stats_pulled_at: null,
    is_archived: false,
    created_at: "2026-03-02T10:00:00.000Z",
    updated_at: "2026-03-02T10:01:00.000Z",
  };

  it("keeps local text and only advances version when local edits differ", () => {
    const result = reconcileDraftWithServerEntry({
      currentDraft: {
        title: "Session",
        mood: "Neutral",
        tagsInput: "nq",
        body: "first save + still typing",
        version: 4,
        is_archived: false,
      },
      currentEntryId: 21,
      serverEntry,
    });

    expect(result.replaceBaseline).toBe(false);
    expect(result.nextDraft.body).toBe("first save + still typing");
    expect(result.nextDraft.version).toBe(5);
  });

  it("adopts server draft and baseline when content matches", () => {
    const result = reconcileDraftWithServerEntry({
      currentDraft: {
        title: "Session",
        mood: "Neutral",
        tagsInput: "nq",
        body: "first save",
        version: 4,
        is_archived: false,
      },
      currentEntryId: 21,
      serverEntry,
    });

    expect(result.replaceBaseline).toBe(true);
    expect(result.nextDraft.version).toBe(5);
    expect(result.nextDraft.body).toBe("first save");
  });
});
