import { loadSessionToken } from "../lib/session";

type RateLimitConfig = {
  limit: number;
  windowMs: number;
};

const RATE_LIMITS: Record<string, RateLimitConfig> = {
  default: { limit: 200, windowMs: 60_000 },
  "/api/History/retrieveBars": { limit: 50, windowMs: 30_000 },
};

type RateLimiterState = {
  timestamps: number[];
  queue: Promise<void>;
};

const limiterState = new Map<string, RateLimiterState>();
const RATE_LIMIT_RETRIES = 3;
const MIN_RETRY_DELAY_MS = 500;

function getLimiterKey(path: string) {
  return path === "/api/History/retrieveBars" ? path : "default";
}

function getRateLimitConfig(path: string) {
  return RATE_LIMITS[getLimiterKey(path)];
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function scheduleRateLimitedRequest(config: RateLimitConfig, key: string) {
  const state: RateLimiterState = limiterState.get(key) ?? {
    timestamps: [],
    queue: Promise.resolve(),
  };

  limiterState.set(key, state);

  state.queue = state.queue.then(async () => {
    const now = Date.now();
    state.timestamps = state.timestamps.filter((ts) => now - ts < config.windowMs);

    if (state.timestamps.length >= config.limit) {
      const earliest = state.timestamps[0];
      const waitMs = config.windowMs - (now - earliest);

      if (waitMs > 0) {
        await sleep(waitMs);
      }
    }

    const ready = Date.now();
    state.timestamps = state.timestamps.filter((ts) => ready - ts < config.windowMs);
    state.timestamps.push(ready);
  });

  return state.queue;
}

async function parseJsonOrThrow(res: Response) {
  const text = await res.text();

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text || "No body"}`);
  }

  if (!text) return null;

  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Expected JSON but got: ${text}`);
  }
}

type TopstepPostOptions = {
  cacheTtlMs?: number;
  cacheKey?: string;
  forceRefresh?: boolean;
};

type CacheEntry = {
  expiresAt: number;
  data: unknown;
};

const responseCache = new Map<string, CacheEntry>();

function makeCacheKey(path: string, body: unknown, cacheKey?: string) {
  return cacheKey ?? `${path}:${JSON.stringify(body)}`;
}

export async function topstepPost<T>(path: string, body: unknown = {}, opts: TopstepPostOptions = {}): Promise<T> {
  const token = loadSessionToken();
  if (!token) throw new Error("No session token. Connect in Settings first.");

  const limiterKey = getLimiterKey(path);
  const config = getRateLimitConfig(path);

  if (opts.cacheTtlMs && opts.cacheTtlMs > 0 && !opts.forceRefresh) {
    const key = makeCacheKey(path, body, opts.cacheKey);
    const cached = responseCache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      return cached.data as T;
    }
  }

  let lastError: Error | null = null;

  for (let attempt = 0; attempt < RATE_LIMIT_RETRIES; attempt += 1) {
    await scheduleRateLimitedRequest(config, limiterKey);

    const res = await fetch(`/topstep${path}`, {
      method: "POST",
      headers: {
        accept: "text/plain",
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });

    if (res.status === 429) {
      const retryAfterHeader = res.headers.get("retry-after");
      const retryAfterSeconds = retryAfterHeader ? Number.parseInt(retryAfterHeader, 10) : null;
      const fallbackDelay = Math.ceil(config.windowMs / config.limit);
      const retryDelayMs = Math.max(
        MIN_RETRY_DELAY_MS,
        Number.isFinite(retryAfterSeconds) ? retryAfterSeconds! * 1000 : fallbackDelay,
      );
      const errorText = await res.text();

      lastError = new Error(
        errorText ||
          "Request was throttled. Limits are 50 requests/30s for /api/History/retrieveBars and 200 requests/60s for other endpoints.",
      );

      if (attempt < RATE_LIMIT_RETRIES - 1) {
        await sleep(retryDelayMs * (attempt + 1));
        continue;
      }

      break;
    }

    const parsed = (await parseJsonOrThrow(res)) as T;

    if (opts.cacheTtlMs && opts.cacheTtlMs > 0) {
      const key = makeCacheKey(path, body, opts.cacheKey);
      responseCache.set(key, {
        expiresAt: Date.now() + opts.cacheTtlMs,
        data: parsed,
      });
    }

    return parsed;
  }

  throw lastError ?? new Error("Rate limit exceeded. Please slow down and try again.");
}

export function clearTopstepCache(path?: string) {
  if (!path) {
    responseCache.clear();
    return;
  }

  const prefix = `${path}:`;
  for (const key of responseCache.keys()) {
    if (key.startsWith(prefix)) {
      responseCache.delete(key);
    }
  }
}
