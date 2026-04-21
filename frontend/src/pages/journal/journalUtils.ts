import type {
  JournalEntriesQuery,
  JournalEntry,
  JournalEntrySaveResult,
  JournalStatsSnapshot,
  JournalEntryUpdateInput,
  JournalMood,
} from "../../lib/types";
import { sanitizeJournalBody } from "./journalImages";

export const JOURNAL_PAGE_SIZE = 20;
export const JOURNAL_AUTOSAVE_DELAY_MS = 800;
export const CURRENT_JOURNAL_STATS_SNAPSHOT_VERSION = 2;

export interface JournalDraft {
  title: string;
  mood: JournalMood;
  tagsInput: string;
  body: string;
  version: number;
  is_archived: boolean;
}

export type JournalMoodFilter = JournalMood | "ALL";

export interface ReconcileDraftWithServerParams {
  currentDraft: JournalDraft | null;
  currentEntryId: number | null;
  serverEntry: JournalEntry;
}

export interface ReconcileDraftWithServerResult {
  nextDraft: JournalDraft;
  replaceBaseline: boolean;
}

export function entryToDraft(entry: JournalEntry): JournalDraft {
  return {
    title: entry.title,
    mood: entry.mood,
    tagsInput: entry.tags.join(", "),
    body: sanitizeJournalBody(entry.body),
    version: entry.version,
    is_archived: entry.is_archived,
  };
}

export function hasJournalTradeStatsSnapshot<T extends Pick<JournalEntry, "stats_json">>(
  entry: T,
): entry is T & { stats_json: JournalStatsSnapshot } {
  const snapshot = entry.stats_json;
  if (!snapshot || typeof snapshot !== "object") {
    return false;
  }
  if (snapshot.snapshot_version === CURRENT_JOURNAL_STATS_SNAPSHOT_VERSION) {
    return true;
  }

  return [snapshot.trade_count, snapshot.net_realized_pnl, snapshot.net, snapshot.total_pnl, snapshot.gross].some(
    (value) => typeof value === "number" && Number.isFinite(value),
  );
}

export function draftToUpdatePayload(draft: JournalDraft, versionOverride?: number): JournalEntryUpdateInput {
  return {
    version: versionOverride ?? draft.version,
    title: draft.title,
    mood: draft.mood,
    tags: parseTagsInput(draft.tagsInput),
    body: sanitizeJournalBody(draft.body),
    is_archived: draft.is_archived,
  };
}

export function reconcileDraftWithServerEntry({
  currentDraft,
  currentEntryId,
  serverEntry,
}: ReconcileDraftWithServerParams): ReconcileDraftWithServerResult {
  const serverDraft = entryToDraft(serverEntry);
  if (!currentDraft || currentEntryId !== serverEntry.id) {
    return {
      nextDraft: serverDraft,
      replaceBaseline: true,
    };
  }

  const currentPayload = draftToUpdatePayload(currentDraft, serverDraft.version);
  const serverPayload = draftToUpdatePayload(serverDraft, serverDraft.version);
  if (journalPayloadEquals(currentPayload, serverPayload)) {
    return {
      nextDraft: serverDraft,
      replaceBaseline: true,
    };
  }

  if (currentDraft.version === serverDraft.version) {
    return {
      nextDraft: currentDraft,
      replaceBaseline: false,
    };
  }

  return {
    nextDraft: {
      ...currentDraft,
      version: serverDraft.version,
    },
    replaceBaseline: false,
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

function _readDatePart(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((part) => part.type === type)?.value ?? "00";
}

export function getTodayTradingDateIso(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const year = _readDatePart(parts, "year");
  const month = _readDatePart(parts, "month");
  const day = _readDatePart(parts, "day");
  return `${year}-${month}-${day}`;
}

export function getYesterdayTradingDateIso(now: Date = new Date()): string {
  const today = getTodayTradingDateIso(now);
  const [yearText, monthText, dayText] = today.split("-");
  const year = Number.parseInt(yearText, 10);
  const month = Number.parseInt(monthText, 10);
  const day = Number.parseInt(dayText, 10);
  const priorDateUtc = new Date(Date.UTC(year, month - 1, day));
  priorDateUtc.setUTCDate(priorDateUtc.getUTCDate() - 1);
  return priorDateUtc.toISOString().slice(0, 10);
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

export function applyJournalSaveResultToEntry(params: {
  entry: JournalEntry;
  patch: Omit<JournalEntryUpdateInput, "version">;
  result: JournalEntrySaveResult;
}): JournalEntry {
  const { entry, patch, result } = params;
  return {
    ...entry,
    entry_date: result.entry_date,
    title: result.title,
    mood: result.mood,
    tags: result.tags,
    body: patch.body ?? entry.body,
    version: result.version,
    is_archived: result.is_archived,
    updated_at: result.updated_at,
  };
}

export function applyJournalSaveResultToDraft(params: {
  draft: JournalDraft;
  patch: Omit<JournalEntryUpdateInput, "version">;
  result: JournalEntrySaveResult;
}): JournalDraft {
  const { draft, patch, result } = params;
  return {
    ...draft,
    title: result.title,
    mood: result.mood,
    tagsInput: result.tags.join(", "),
    body: patch.body ?? draft.body,
    version: result.version,
    is_archived: result.is_archived,
  };
}
