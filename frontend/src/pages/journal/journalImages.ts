export const JOURNAL_IMAGE_ALT_TEXT = "Journal image";

const JOURNAL_IMAGE_REF_PREFIX = "journal-image://";
const JOURNAL_IMAGE_MARKDOWN_PATTERN = /!\[[^\]]*]\(([^)\s]+)\)/g;
const PERSISTED_JOURNAL_IMAGE_PATTERN = /!\[[^\]]*]\(journal-image:\/\/(\d+)\)/g;

export interface InsertJournalImageResult {
  body: string;
  selectionStart: number;
  selectionEnd: number;
}

export function buildJournalImageRef(imageId: number): string {
  return `${JOURNAL_IMAGE_REF_PREFIX}${imageId}`;
}

export function buildJournalImageMarkdown(imageId: number, altText = JOURNAL_IMAGE_ALT_TEXT): string {
  return `![${altText}](${buildJournalImageRef(imageId)})`;
}

export function insertJournalImageMarkdown(
  body: string,
  imageMarkdown: string,
  selectionStart: number,
  selectionEnd: number,
): InsertJournalImageResult {
  const boundedStart = Math.max(0, Math.min(selectionStart, body.length));
  const boundedEnd = Math.max(boundedStart, Math.min(selectionEnd, body.length));
  const before = body.slice(0, boundedStart);
  const after = body.slice(boundedEnd);
  const prefix = before.length > 0 && !before.endsWith("\n") ? "\n" : "";
  const suffix = after.length > 0 && !after.startsWith("\n") ? "\n" : "";
  const nextBody = `${before}${prefix}${imageMarkdown}${suffix}${after}`;
  const cursorPosition = (before + prefix + imageMarkdown).length;

  return {
    body: nextBody,
    selectionStart: cursorPosition,
    selectionEnd: cursorPosition,
  };
}

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

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function removeJournalImageMarkdown(body: string, imageId: number): string {
  const imageRef = buildJournalImageRef(imageId);
  const pattern = new RegExp(`!?\\[[^\\]]*\\]\\(${escapeRegExp(imageRef)}\\)`, "g");
  return body.replace(pattern, "[removed image]").replace(/\n{3,}/g, "\n\n");
}

export function stripJournalImageMarkdown(body: string): string {
  return body.replace(JOURNAL_IMAGE_MARKDOWN_PATTERN, (_match, ref) =>
    ref.startsWith(JOURNAL_IMAGE_REF_PREFIX) ? "[image]" : "[external image]",
  );
}
