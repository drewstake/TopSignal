import type { ProgressSample } from "./TradeProgress";

export const SAMPLE_INTERVAL_SECONDS = 1;

export function chartSegments(samples: ProgressSample[]) {
  const segments: ProgressSample[][] = [];
  let current: ProgressSample[] = [];
  for (const sample of samples) {
    const previous = current.at(-1);
    if (sample.points === null || sample.issue || (previous && (sample.ts - previous.ts > SAMPLE_INTERVAL_SECONDS * 2 || sample.average_entry !== previous.average_entry))) {
      if (current.length) segments.push(current);
      current = [];
    }
    if (sample.points !== null && !sample.issue) current.push(sample);
  }
  if (current.length) segments.push(current);
  return segments;
}
