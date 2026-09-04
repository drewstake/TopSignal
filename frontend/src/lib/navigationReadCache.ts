export interface NavigationReadOptions {
  signal?: AbortSignal;
  bypassCache?: boolean;
}

interface CacheEntry {
  promise: Promise<unknown>;
  controller: AbortController;
  expiresAt: number | null;
  consumers: number;
}

/** Session-only display data. Never use this for trading authorization or order state. */
export class NavigationReadCache {
  private readonly entries = new Map<string, CacheEntry>();
  private readonly ttlMs: number;
  private readonly maxEntries: number;

  constructor(ttlMs = 10 * 60_000, maxEntries = 128) {
    this.ttlMs = ttlMs;
    this.maxEntries = maxEntries;
  }

  get<T>(key: string, load: (signal: AbortSignal) => Promise<T>, options: NavigationReadOptions = {}): Promise<T> {
    if (options.signal?.aborted) return Promise.reject(options.signal.reason);
    let entry = this.entries.get(key);
    if (options.bypassCache || (entry?.expiresAt !== null && (entry?.expiresAt ?? 0) <= Date.now())) {
      this.entries.delete(key);
      entry = undefined;
    }
    if (!entry) {
      const controller = new AbortController();
      const created: CacheEntry = {
        controller,
        promise: Promise.resolve().then(() => {
          controller.signal.throwIfAborted();
          return load(controller.signal);
        }),
        expiresAt: null,
        consumers: 0,
      };
      entry = created;
      this.entries.set(key, created);
      while (this.entries.size > this.maxEntries) {
        this.entries.delete(this.entries.keys().next().value!);
      }
      void created.promise.then(
        () => { created.expiresAt = Date.now() + this.ttlMs; },
        () => { if (this.entries.get(key) === created) this.entries.delete(key); },
      );
    }
    const current = entry;
    current.consumers += 1;
    return new Promise<T>((resolve, reject) => {
      let done = false;
      const finish = () => {
        if (done) return false;
        done = true;
        options.signal?.removeEventListener("abort", abort);
        current.consumers -= 1;
        if (current.expiresAt === null && current.consumers === 0) {
          if (this.entries.get(key) === current) this.entries.delete(key);
          current.controller.abort();
        }
        return true;
      };
      const abort = () => { if (finish()) reject(options.signal?.reason); };
      options.signal?.addEventListener("abort", abort, { once: true });
      void current.promise.then(
        (value) => { if (finish()) resolve(value as T); },
        (error: unknown) => { if (finish()) reject(error); },
      );
    });
  }

  invalidate(prefix: string): void {
    // Existing consumers may finish, but their result cannot repopulate the cache.
    for (const key of this.entries.keys()) {
      if (key.startsWith(prefix)) this.entries.delete(key);
    }
  }
}
