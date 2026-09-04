import { tradingDayKey } from "./tradingDay";

export interface TradeRefreshRange { start?: string; end?: string }

const easternParts = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", weekday: "short", hour: "2-digit", hourCycle: "h23",
});

/** Analytics refresh cadence only. Never use this cache to authorize trading. */
export function tradeRefreshCadence(now: Date): { ttlMs: number; period: string } {
  const parts = easternParts.formatToParts(now);
  const day = parts.find((part) => part.type === "weekday")?.value;
  const hour = Number(parts.find((part) => part.type === "hour")?.value);
  const weekend = (day === "Fri" && hour >= 17) || day === "Sat" || (day === "Sun" && hour < 18);
  const maintenance = hour === 17 && day !== "Sun";
  const closed = weekend || maintenance;
  // A change of period expires a closed-session observation at reopening.
  // Holiday/unscheduled closures simply retain the more conservative short TTL.
  return { ttlMs: closed ? 10 * 60_000 : 30_000, period: `${closed ? "closed" : "open"}:${tradingDayKey(now)}` };
}

interface Coverage {
  start: number | null;
  end: number;
  tail: boolean;
}
interface Entry<T> {
  coverage: Coverage;
  period: string;
  expiresAt: number;
  promise: Promise<T>;
  pending: boolean;
}

function coverage(range: TradeRefreshRange, now: number): Coverage {
  return {
    // An omitted start uses a provider-dependent lookback: never claim it covers
    // arbitrary explicit history, or vice versa.
    start: range.start === undefined ? null : Date.parse(range.start),
    end: range.end === undefined ? now : Math.min(Date.parse(range.end), now),
    tail: range.end === undefined || Date.parse(range.end) >= now - 30_000,
  };
}

function covers(previous: Coverage, next: Coverage): boolean {
  const startCovered = previous.start === null || next.start === null
    ? previous.start === next.start
    : previous.start <= next.start;
  return startCovered && (previous.end >= next.end || (previous.tail && next.tail));
}

/** In-memory, bounded, user/account-scoped successful observations survive route remounts. */
export class TradeRefreshCache<T> {
  private readonly entries = new Map<string, Entry<T>[]>();

  invalidate(accountId?: number): void {
    for (const key of this.entries.keys()) {
      if (accountId === undefined || key.endsWith(`|account:${accountId}`)) this.entries.delete(key);
    }
  }

  run(options: {
    scope: string;
    accountId: number;
    range: TradeRefreshRange;
    automatic: boolean;
    load: () => Promise<T>;
    now?: number;
  }): Promise<T> {
    const now = options.now ?? Date.now();
    const cadence = tradeRefreshCadence(new Date(now));
    const requested = coverage(options.range, now);
    const key = `${options.scope}|account:${options.accountId}`;
    const entries = (this.entries.get(key) ?? []).filter((entry) => entry.pending || entry.expiresAt > now);
    this.entries.set(key, entries);
    if (options.automatic) {
      const reusable = entries.find((entry) => entry.period === cadence.period && covers(entry.coverage, requested));
      if (reusable) return reusable.promise;
    } else {
      // Manual sync always reaches the provider. Superseded requests cannot
      // restore automatic freshness after the operator asked for newer data.
      entries.length = 0;
    }
    const entry: Entry<T> = {
      coverage: requested, period: cadence.period, expiresAt: now + cadence.ttlMs,
      pending: true, promise: Promise.resolve().then(options.load),
    };
    entries.push(entry);
    if (entries.length > 16) entries.shift();
    while (this.entries.size > 128) this.entries.delete(this.entries.keys().next().value!);
    void entry.promise.then(
      () => { entry.pending = false; },
      () => {
        // Failure is never a successful freshness marker, including HTTP errors.
        entry.pending = false;
        entry.expiresAt = 0;
      },
    );
    return entry.promise;
  }
}
