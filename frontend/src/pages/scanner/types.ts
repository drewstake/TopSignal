export type Headline = {
  id: number; tickers: string[]; title: string; source: string; link: string; received: number;
};
export type Coverage = Pick<Headline, "title" | "source" | "link" | "received"> & {
  published: number | null; relation: string;
};
export type ScannerEvent = Headline & {
  kind: "headline" | "move"; published: number | null; baseline: boolean; delayed: boolean;
  summary: string | null; cause: string | null; citation: string | null; citation_time: number | null;
  updates: Coverage[]; jobs: Record<string, {state: string; detail: string | null}>;
};
export type Snapshot = {
  events: ScannerEvent[]; sources: {name: string; status: string; success: number | null}[];
  worker: string; spend: {cycle: string; micro_usd: number} | null;
};
const eastern = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short",
  day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
export function easternTime(stamp: number) { return eastern.format(stamp * 1000); }
