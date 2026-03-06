export const JOURNAL_IMAGE_ALT_TEXT = "Journal image";

const JOURNAL_IMAGE_REF_PREFIX = "journal-image://";
const JOURNAL_IMAGE_MARKDOWN_PATTERN = /!\[[^\]]*]\(([^)\s]+)\)/g;
const PERSISTED_JOURNAL_IMAGE_PATTERN = /!\[[^\]]*]\(journal-image:\/\/(\d+)\)/g;
const STANDALONE_PERSISTED_JOURNAL_IMAGE_LINE_PATTERN = /^[ \t]*!\[[^\]]*]\(journal-image:\/\/\d+\)[ \t]*\n?/gm;

export function extractPersistedJournalImageIds(body: string): number[] {
  const ids: number[] = [];
  const seen = new Set<number>();

  for (const match of body.matchAll(PERSISTED_JOURNAL_IMAGE_PATTERN)) {
    const rawId = match[1];
    const parsedId = Number.parseInt(rawId, 10);
    if (!Number.isFinite(parsedId) || seen.has(parsedId)) {
      continue;
    }
    ids.push(parsedId);
    seen.add(parsedId);
  }

  return ids;
}

function collapseImageWhitespace(body: string): string {
  return body
    .replace(/[ \t]{2,}/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function stripJournalImageMarkdown(body: string): string {
  return collapseImageWhitespace(
    body
      .replace(STANDALONE_PERSISTED_JOURNAL_IMAGE_LINE_PATTERN, "")
      .replace(JOURNAL_IMAGE_MARKDOWN_PATTERN, (_match, ref) =>
        ref.startsWith(JOURNAL_IMAGE_REF_PREFIX) ? "" : "[external image]",
      ),
  );
}

export function sanitizeJournalBody(body: string): string {
  return stripJournalImageMarkdown(body);
}
