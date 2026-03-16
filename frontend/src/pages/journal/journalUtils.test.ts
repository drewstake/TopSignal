import { describe, expect, it } from "vitest";

import type { JournalEntry } from "../../lib/types";
import {
  applyJournalSaveResultToDraft,
  applyJournalSaveResultToEntry,
  buildJournalQuery,
  entryToDraft,
  draftToUpdatePayload,
  getTodayTradingDateIso,
  getYesterdayTradingDateIso,
  hasJournalTradeStatsSnapshot,
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
      body: "Kept risk tight.\n![Journal image](journal-image://7)",
      version: 7,
      is_archived: false,
    });

    expect(payload.version).toBe(7);
    expect(payload.body).toBe("Kept risk tight.");
  });
});

describe("entryToDraft", () => {
  it("removes persisted journal image refs before showing body text", () => {
    const draft = entryToDraft({
      id: 22,
      account_id: 13001,
      entry_date: "2026-03-02",
      title: "Session",
      mood: "Neutral",
      tags: ["nq"],
      body: "Review\n![Journal image](journal-image://12)\nFollow-through",
      version: 5,
      stats_source: null,
      stats_json: null,
      stats_pulled_at: null,
      is_archived: false,
      created_at: "2026-03-02T10:00:00.000Z",
      updated_at: "2026-03-02T10:01:00.000Z",
    });

    expect(draft.body).toBe(["Review", "Follow-through"].join("\n"));
  });
});

describe("hasJournalTradeStatsSnapshot", () => {
  it("treats entries with a saved stats payload as hydrated", () => {
    expect(
      hasJournalTradeStatsSnapshot({
        stats_json: {
          snapshot_version: 2,
          trade_count: 0,
          total_pnl: 0,
          total_fees: 0,
          win_rate: 0,
          avg_win: 0,
          avg_loss: 0,
          largest_win: 0,
          largest_loss: 0,
          gross: 0,
          net: 0,
          net_realized_pnl: 0,
        },
      }),
    ).toBe(true);
  });

  it("treats entries without a saved stats payload as missing snapshots", () => {
    expect(hasJournalTradeStatsSnapshot({ stats_json: null })).toBe(false);
  });

  it("treats legacy stats payloads as hydrated so merged history is preserved", () => {
    expect(
      hasJournalTradeStatsSnapshot({
        stats_json: {
          snapshot_version: 1,
          trade_count: 0,
          total_pnl: 0,
          total_fees: 0,
          win_rate: 0,
          avg_win: 0,
          avg_loss: 0,
          largest_win: 0,
          largest_loss: 0,
          gross: 0,
          net: 0,
          net_realized_pnl: 0,
        },
      }),
    ).toBe(true);
  });

  it("treats invalid stats payloads as missing snapshots", () => {
    expect(hasJournalTradeStatsSnapshot({ stats_json: {} as JournalEntry["stats_json"] })).toBe(false);
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

describe("applyJournalSaveResultToEntry", () => {
  it("merges the lean save response into the existing entry without losing the saved body", () => {
    const entry = {
      id: 21,
      account_id: 13001,
      entry_date: "2026-03-02",
      title: "Session",
      mood: "Neutral",
      tags: ["nq"],
      body: "before save",
      version: 5,
      stats_source: "trade_snapshot",
      stats_json: {
        snapshot_version: 2,
        trade_count: 1,
        total_pnl: 10,
        total_fees: 2,
        win_rate: 100,
        avg_win: 8,
        avg_loss: 0,
        largest_win: 8,
        largest_loss: 0,
        gross: 10,
        net: 8,
        net_realized_pnl: 8,
      },
      stats_pulled_at: "2026-03-02T10:02:00.000Z",
      is_archived: false,
      created_at: "2026-03-02T10:00:00.000Z",
      updated_at: "2026-03-02T10:01:00.000Z",
    } satisfies JournalEntry;

    const nextEntry = applyJournalSaveResultToEntry({
      entry,
      patch: {
        title: "Trimmed title",
        mood: "Focused",
        tags: ["nq", "discipline"],
        body: "latest body",
        is_archived: false,
      },
      result: {
        id: 21,
        account_id: 13001,
        entry_date: "2026-03-02",
        title: "Trimmed title",
        mood: "Focused",
        tags: ["nq", "discipline"],
        version: 6,
        is_archived: false,
        updated_at: "2026-03-02T10:03:00.000Z",
      },
    });

    expect(nextEntry.body).toBe("latest body");
    expect(nextEntry.stats_json).toEqual({
      snapshot_version: 2,
      trade_count: 1,
      total_pnl: 10,
      total_fees: 2,
      win_rate: 100,
      avg_win: 8,
      avg_loss: 0,
      largest_win: 8,
      largest_loss: 0,
      gross: 10,
      net: 8,
      net_realized_pnl: 8,
    });
    expect(nextEntry.version).toBe(6);
  });
});

describe("applyJournalSaveResultToDraft", () => {
  it("updates the local draft version from the lean save response", () => {
    const nextDraft = applyJournalSaveResultToDraft({
      draft: {
        title: "Session",
        mood: "Neutral",
        tagsInput: "nq",
        body: "latest body",
        version: 5,
        is_archived: false,
      },
      patch: {
        title: "Trimmed title",
        mood: "Focused",
        tags: ["nq", "discipline"],
        body: "latest body",
        is_archived: false,
      },
      result: {
        id: 21,
        account_id: 13001,
        entry_date: "2026-03-02",
        title: "Trimmed title",
        mood: "Focused",
        tags: ["nq", "discipline"],
        version: 6,
        is_archived: false,
        updated_at: "2026-03-02T10:03:00.000Z",
      },
    });

    expect(nextDraft).toEqual({
      title: "Trimmed title",
      mood: "Focused",
      tagsInput: "nq, discipline",
      body: "latest body",
      version: 6,
      is_archived: false,
    });
  });
});
