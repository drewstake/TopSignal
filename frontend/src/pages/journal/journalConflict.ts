import { isApiError } from "../../lib/api";
import type { JournalEntry, JournalMood } from "../../lib/types";

function isJournalMood(value: unknown): value is JournalMood {
  return value === "Focused" || value === "Neutral" || value === "Frustrated" || value === "Confident";
}

function isJournalEntryLike(value: unknown): value is JournalEntry {
  if (!value || typeof value !== "object") {
    return false;
  }

  const candidate = value as Partial<JournalEntry>;
  return (
    typeof candidate.id === "number" &&
    typeof candidate.account_id === "number" &&
    typeof candidate.entry_date === "string" &&
    typeof candidate.title === "string" &&
    isJournalMood(candidate.mood) &&
    Array.isArray(candidate.tags) &&
    typeof candidate.body === "string" &&
    typeof candidate.version === "number" &&
    typeof candidate.is_archived === "boolean" &&
    typeof candidate.created_at === "string" &&
    typeof candidate.updated_at === "string"
  );
}

export function getVersionConflictServerEntry(error: unknown): JournalEntry | null {
  if (!isApiError(error) || error.status !== 409) {
    return null;
  }

  const body = error.body;
  if (!body || typeof body !== "object") {
    return null;
  }

  const payload = body as { detail?: unknown; server?: unknown };
  if (payload.detail !== "version_conflict") {
    return null;
  }

  return isJournalEntryLike(payload.server) ? payload.server : null;
}
