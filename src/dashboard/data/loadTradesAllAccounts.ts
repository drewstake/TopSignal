import { searchAccounts, type TopstepAccount } from "../../api/account";
import { searchTrades, type TopstepTrade } from "../../api/trade";
import { hasSessionToken } from "../../lib/session";

type LoadTradesOptions = {
  startTimestamp: string; // ISO
  endTimestamp: string; // ISO
  onlyActiveAccounts?: boolean; // default false
  includeInvisibleAccounts?: boolean; // default false
  daysPerChunk?: number; // default 30
  concurrency?: number; // default 2
  forceRefresh?: boolean; // default false
};

export type TradesAllAccountsResult = {
  accounts: TopstepAccount[];
  allTrades: TopstepTrade[];
  byAccountId: Record<number, TopstepTrade[]>;
};

const memCache = new Map<string, TopstepTrade[]>();

function toISO(d: Date) {
  return d.toISOString();
}

function clampChunkDays(n: number) {
  if (!Number.isFinite(n) || n <= 0) return 30;
  if (n > 60) return 60;
  return Math.floor(n);
}

function clampConcurrency(n: number) {
  if (!Number.isFinite(n) || n <= 0) return 2;
  if (n > 4) return 4;
  return Math.floor(n);
}

function addDays(d: Date, days: number) {
  const x = new Date(d.getTime());
  x.setUTCDate(x.getUTCDate() + days);
  return x;
}

function splitRange(startISO: string, endISO: string, daysPerChunk: number) {
  const start = new Date(startISO);
  const end = new Date(endISO);

  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime())) {
    throw new Error("Invalid startTimestamp/endTimestamp (must be ISO strings).");
  }
  if (end <= start) {
    throw new Error("endTimestamp must be after startTimestamp.");
  }

  const chunks: { start: string; end: string }[] = [];

  let cur = start;
  while (cur < end) {
    const next = addDays(cur, daysPerChunk);
    const chunkEnd = next < end ? next : end;

    chunks.push({ start: toISO(cur), end: toISO(chunkEnd) });
    cur = chunkEnd;
  }

  return chunks;
}

async function mapLimit<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let i = 0;

  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const idx = i;
      i += 1;
      if (idx >= items.length) return;
      out[idx] = await fn(items[idx], idx);
    }
  });

  await Promise.all(workers);
  return out;
}

function dedupeTrades(trades: TopstepTrade[]) {
  const seen = new Set<string>();
  const out: TopstepTrade[] = [];
  for (const t of trades) {
    const key = `${t.accountId}:${t.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(t);
  }
  out.sort((a, b) => a.creationTimestamp.localeCompare(b.creationTimestamp));
  return out;
}

async function loadTradesForAccountChunked(
  accountId: number,
  startISO: string,
  endISO: string,
  daysPerChunk: number,
  forceRefresh: boolean
) {
  const cacheKey = `v1:acct:${accountId}:${startISO}:${endISO}:d${daysPerChunk}`;
  const cached = memCache.get(cacheKey);
  if (cached && !forceRefresh) return cached;

  const chunks = splitRange(startISO, endISO, daysPerChunk);

  const all: TopstepTrade[] = [];
  for (const c of chunks) {
    const res = await searchTrades({
      accountId,
      startTimestamp: c.start,
      endTimestamp: c.end,
    });

    if (!res.success || res.errorCode !== 0) {
      throw new Error(res.errorMessage || `Trade/search failed (errorCode ${res.errorCode}).`);
    }

    if (res.trades?.length) all.push(...res.trades);
  }

  const cleaned = dedupeTrades(all);
  memCache.set(cacheKey, cleaned);
  return cleaned;
}

export async function loadTradesAllAccounts(opts: LoadTradesOptions): Promise<TradesAllAccountsResult> {
  if (!hasSessionToken()) {
    throw new Error("Not connected. Go to Settings and connect first.");
  }

  const onlyActiveAccounts = opts.onlyActiveAccounts ?? false;
  const includeInvisible = opts.includeInvisibleAccounts ?? false;
  const daysPerChunk = clampChunkDays(opts.daysPerChunk ?? 30);
  const concurrency = clampConcurrency(opts.concurrency ?? 2);
  const forceRefresh = opts.forceRefresh ?? false;

  const accRes = await searchAccounts({
    onlyActiveAccounts,
    includeInvisibleAccounts: includeInvisible,
  });
  if (!accRes.success || accRes.errorCode !== 0) {
    throw new Error(accRes.errorMessage || `Account/search failed (errorCode ${accRes.errorCode}).`);
  }

  const accounts = accRes.accounts || [];

  const byAccountId: Record<number, TopstepTrade[]> = {};
  const perAccountTrades = await mapLimit(
    accounts,
    concurrency,
    async (a) => {
      const trades = await loadTradesForAccountChunked(
        a.id,
        opts.startTimestamp,
        opts.endTimestamp,
        daysPerChunk,
        opts.forceRefresh ?? false
      );
      byAccountId[a.id] = trades;
      return trades;
    }
  );

  const merged = dedupeTrades(perAccountTrades.flat());

  return {
    accounts,
    allTrades: merged,
    byAccountId,
  };
}
