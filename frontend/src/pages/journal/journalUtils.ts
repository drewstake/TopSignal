import type {
  JournalEntriesQuery,
  JournalEntry,
  JournalEntryUpdateInput,
  JournalMood,
} from "../../lib/types";

export const JOURNAL_PAGE_SIZE = 20;
export const JOURNAL_AUTOSAVE_DELAY_MS = 800;

export interface JournalDraft {
  title: string;
  mood: JournalMood;
  tagsInput: string;
  body: string;
  version: number;
  is_archived: boolean;
}

export type JournalMoodFilter = JournalMood | "ALL";

export function entryToDraft(entry: JournalEntry): JournalDraft {
  return {
    title: entry.title,
    mood: entry.mood,
    tagsInput: entry.tags.join(", "),
    body: entry.body,
    version: entry.version,
    is_archived: entry.is_archived,
  };
}

export function draftToUpdatePayload(draft: JournalDraft, versionOverride?: number): JournalEntryUpdateInput {
  return {
    version: versionOverride ?? draft.version,
    title: draft.title,
    mood: draft.mood,
    tags: parseTagsInput(draft.tagsInput),
    body: draft.body,
    is_archived: draft.is_archived,
  };
}

export function parseTagsInput(value: string): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();

  for (const segment of value.split(",")) {
    const tag = segment.trim().toLowerCase();
    if (!tag || seen.has(tag)) {
      continue;
    }
    normalized.push(tag);
    seen.add(tag);
  }
  return normalized;
}

export function buildJournalQuery(params: {
  startDate: string;
  endDate: string;
  mood: JournalMoodFilter;
  queryText: string;
  includeArchived: boolean;
  limit: number;
  offset: number;
}): JournalEntriesQuery {
  const search = params.queryText.trim();
  return {
    start_date: params.startDate || undefined,
    end_date: params.endDate || undefined,
    mood: params.mood === "ALL" ? undefined : params.mood,
    q: search || undefined,
    include_archived: params.includeArchived,
    limit: params.limit,
    offset: params.offset,
  };
}

export function getTodayUtcDateIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function journalPayloadEquals(a: JournalEntryUpdateInput, b: JournalEntryUpdateInput): boolean {
  if (a.title !== b.title || a.mood !== b.mood || a.body !== b.body || a.is_archived !== b.is_archived) {
    return false;
  }
  const left = a.tags ?? [];
  const right = b.tags ?? [];
  if (left.length !== right.length) {
    return false;
  }
  return left.every((tag, index) => tag === right[index]);
}
