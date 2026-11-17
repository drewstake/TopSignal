import { mergeSessionArchive } from "./liveHistory";
import type { BookHistory, DepthScope } from "./history";

export type WindowQuery = { start?: string; end?: string; scope: DepthScope; window?: "latest" | "session" };
export type SessionProgress = { sessionStart: number; history: BookHistory };
const minute = 60;
const iso = (seconds: number) => new Date(seconds * 1000).toISOString();

export function readerDeadline(error: unknown) {
  return error instanceof Error && error.message.startsWith("History is still preparing");
}

function waitForReader(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, 5000);
    function cancel() { clearTimeout(timer); reject(signal.reason); }
    signal.addEventListener("abort", cancel, { once: true });
  });
}

/** Read one range at a time. The newest 15 minutes can be inspected during backfill.
 * Include an overlapping 10-second bucket to align continuity across independent reads. */
export async function readSessionRanges(
  bounds: { start: number; end: number }, scope: DepthScope,
  previous: SessionProgress | null, read: (range: WindowQuery) => Promise<BookHistory>,
  publish: (progress: SessionProgress) => void, signal: AbortSignal,
) {
  let history = previous?.sessionStart === bounds.start ? previous.history : null;
  // Identical tabs share the host's pending read and cache. Live samples and
  // subsequent recent-window polls cover the current, partially sealed minute.
  const sealedMinute = Math.floor(bounds.end / minute) * minute;
  const firstEnd = sealedMinute > bounds.start ? sealedMinute : bounds.end;
  let cursor = history ? Date.parse(history.start) / 1000 : firstEnd;
  let span = 60 * minute;
  let busyRetries = 0;
  while (cursor > bounds.start) {
    signal.throwIfAborted();
    const start = Math.max(bounds.start, Math.floor((cursor - (history ? span : 15 * minute)) / 10) * 10);
    const end = history ? Math.min(cursor + 10, bounds.end) : Math.min(cursor, bounds.end);
    let part: BookHistory;
    try {
      part = await read({ scope, start: iso(start), end: iso(end) });
    } catch (error) {
      if (error instanceof Error && error.message.startsWith("History is busy") && busyRetries < 4) {
        busyRetries++;
        await waitForReader(signal);
        continue;
      }
      if (!history || span <= 15 * minute || !readerDeadline(error)) throw error;
      span /= 2;
      continue;
    }
    signal.throwIfAborted();
    busyRetries = 0;
    // Preserve actual coverage until the requested session start has been reached.
    const joined: BookHistory = history ? mergeSessionArchive(part, history, bounds.end * 1000) : part;
    history = { ...joined, window: "session", start: iso(start), end: history?.end ?? part.end };
    publish({ sessionStart: bounds.start, history });
    cursor = start;
  }
}
