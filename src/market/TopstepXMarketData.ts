import {
  HubConnection,
  HubConnectionBuilder,
  HubConnectionState,
  HttpTransportType,
  LogLevel,
  type RetryContext,
} from "@microsoft/signalr";

const REST_BASE = "https://api.topstepx.com";
const MARKET_HUB = "https://rtc.topstepx.com/hubs/market";
const DEFAULT_OPTIONS = { symbol: "MNQ", levels: 10, throttleMs: 150 } as const;

const SESSION_TOKEN_KEY = "topsignal.topstep.sessionToken.v1";

type MaybeProcess = {
  env?: Record<string, string | undefined>;
  on?: (event: string, cb: () => void) => void;
  off?: (event: string, cb: () => void) => void;
};

const globalProcess = (globalThis as { process?: MaybeProcess }).process;

function safeGetStorageItem(storage: Storage, key: string) {
  try {
    return storage.getItem(key) || "";
  } catch {
    return "";
  }
}

function loadStoredSessionToken() {
  if (typeof sessionStorage !== "undefined") {
    const token = safeGetStorageItem(sessionStorage, SESSION_TOKEN_KEY).trim();
    if (token) return token;
  }

  if (typeof localStorage !== "undefined") {
    const token = safeGetStorageItem(localStorage, SESSION_TOKEN_KEY).trim();
    if (token) return token;
  }

  return "";
}

export type MarketDataOptions = {
  symbol: string;
  levels: number;
  throttleMs: number;
};

export type QuoteUpdate = {
  last: number | null;
  bestBid: number | null;
  bestAsk: number | null;
  spread: number | null;
  volume: number | null;
  ts: string | null;
};

export type DepthLevel = { price: number; size: number };
export type DepthSnapshot = { bids: DepthLevel[]; asks: DepthLevel[] };

export type MarketDataSnapshot = {
  quote: QuoteUpdate;
  orderBook: DepthSnapshot;
};

type DepthSide = "Bid" | "Ask";

type DepthPayload = {
  side?: DepthSide;
  type?: number;
  price: number;
  volume?: number | null;
  currentVolume?: number | null;
};

type QuotePayload = {
  lastPrice?: number | null;
  bestBidPrice?: number | null;
  bestAskPrice?: number | null;
  volume?: number | null;
  timestamp?: string | null;
  time?: string | null;
};

type ContractSearchResult = {
  id?: string | null;
  contractId?: string | null;
  activeContract?:
    | boolean
    | string
    | null
    | { id?: string | null; contractId?: string | null };
  symbolId?: string | null;
};

type UnsubscribeFn = () => void;

type MarketDataCallbacks = {
  onQuote(cb: (quote: QuoteUpdate) => void): UnsubscribeFn;
  onDepth(cb: (snapshot: DepthSnapshot) => void): UnsubscribeFn;
  onStatus(cb: (connected: boolean) => void): UnsubscribeFn;
};

function toNumber(value: number | string | null | undefined) {
  if (value === null || value === undefined) return null;
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  return Number.isFinite(n) ? n : null;
}

function createThrottled(fn: () => void, intervalMs: number) {
  let last = 0;
  let timeout: ReturnType<typeof setTimeout> | null = null;

  return () => {
    const now = Date.now();
    const elapsed = now - last;

    if (elapsed >= intervalMs) {
      last = now;
      fn();
      return;
    }

    if (timeout) return;

    // schedule a trailing call so bursts still emit the latest update once the
    // throttle window cools down.
    timeout = setTimeout(() => {
      last = Date.now();
      timeout = null;
      fn();
    }, intervalMs - elapsed);
  };
}

class OrderBook {
  private bids = new Map<number, number>();
  private asks = new Map<number, number>();

  reset() {
    this.bids.clear();
    this.asks.clear();
  }

  apply(update: DepthPayload & { side: DepthSide }) {
    const price = toNumber(update.price);
    // depth payloads sometimes send cumulative size (currentVolume) instead of a
    // delta, so prefer it when present to avoid compounding totals.
    const rawSize = update.currentVolume ?? update.volume;
    const size = toNumber(rawSize);

    if (price === null) return;

    const target = update.side === "Bid" ? this.bids : this.asks;
    if (size === null || size <= 0) {
      target.delete(price);
      return;
    }

    target.set(price, size);
  }

  snapshot(levels: number): DepthSnapshot {
    const bids = [...this.bids.entries()]
      .sort((a, b) => b[0] - a[0])
      .slice(0, levels)
      .map(([price, size]) => ({ price, size }));

    const asks = [...this.asks.entries()]
      .sort((a, b) => a[0] - b[0])
      .slice(0, levels)
      .map(([price, size]) => ({ price, size }));

    return { bids, asks };
  }
}

class MarketDataServiceImpl implements MarketDataCallbacks {
  private readonly options: MarketDataOptions;
  private readonly orderBook = new OrderBook();
  private readonly quoteHandlers = new Set<(quote: QuoteUpdate) => void>();
  private readonly depthHandlers = new Set<(snapshot: DepthSnapshot) => void>();
  private readonly statusHandlers = new Set<(connected: boolean) => void>();

  private readonly quoteState: QuoteUpdate = {
    last: null,
    bestBid: null,
    bestAsk: null,
    spread: null,
    volume: null,
    ts: null,
  };

  private connection: HubConnection | null = null;
  private contractId: string | null = null;
  private started = false;
  private connected = false;
  private latestDepth: DepthSnapshot = { bids: [], asks: [] };

  private emitQuoteThrottled: () => void;
  private emitDepthThrottled: () => void;

  private stopHandlers: (() => void)[] = [];

  constructor(options?: Partial<MarketDataOptions>) {
    this.options = { ...DEFAULT_OPTIONS, ...(options ?? {}) };
    this.emitQuoteThrottled = createThrottled(() => this.emitQuote(), this.options.throttleMs);
    this.emitDepthThrottled = createThrottled(() => this.emitDepth(), this.options.throttleMs);
  }

  onQuote(cb: (quote: QuoteUpdate) => void): UnsubscribeFn {
    this.quoteHandlers.add(cb);
    return () => this.quoteHandlers.delete(cb);
  }

  onDepth(cb: (snapshot: DepthSnapshot) => void): UnsubscribeFn {
    this.depthHandlers.add(cb);
    return () => this.depthHandlers.delete(cb);
  }

  onStatus(cb: (connected: boolean) => void): UnsubscribeFn {
    this.statusHandlers.add(cb);
    return () => this.statusHandlers.delete(cb);
  }

  getSnapshot(): MarketDataSnapshot {
    return {
      quote: { ...this.quoteState },
      orderBook: {
        bids: [...this.latestDepth.bids],
        asks: [...this.latestDepth.asks],
      },
    };
  }

  isConnected() {
    return this.connected && this.connection?.state === HubConnectionState.Connected;
  }

  async start() {
    if (this.started) return;
    this.started = true;

    try {
      const jwt = this.loadJwt();
      if (!jwt) {
        throw new Error(
          "Topstep session token not found. Connect in Settings or set VITE_PROJECTX_JWT."
        );
      }

      this.contractId = await this.resolveContractId(this.options.symbol, jwt);
      this.connection = this.connectMarketHub(jwt);

      this.attachHubHandlers(this.connection);

      await this.connection.start();
      await this.subscribe(this.connection, this.contractId);
      this.connected = true;
      this.emitStatus();
      this.registerShutdownHooks();
    } catch (err) {
      this.started = false;
      this.connected = false;
      this.emitStatus();
      throw err;
    }
  }

  async stop() {
    if (!this.started) return;
    this.started = false;
    this.connected = false;
    this.emitStatus();

    for (const teardown of this.stopHandlers) {
      teardown();
    }
    this.stopHandlers = [];

    if (this.connection) {
      try {
        await this.connection.stop();
      } catch {
        /* swallow */
      }
      this.connection = null;
    }
  }

  private emitQuote() {
    const payload = { ...this.quoteState, spread: this.computeSpread() };
    for (const cb of this.quoteHandlers) {
      cb(payload);
    }
  }

  private emitDepth() {
    for (const cb of this.depthHandlers) {
      cb({ bids: [...this.latestDepth.bids], asks: [...this.latestDepth.asks] });
    }
  }

  private emitStatus() {
    for (const cb of this.statusHandlers) {
      cb(this.isConnected());
    }
  }

  private hasOwn<T extends object>(obj: T, key: PropertyKey) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  private pickNumberField(payload: QuotePayload, key: keyof QuotePayload, current: number | null) {
    if (!this.hasOwn(payload, key)) return current;
    return toNumber(payload[key] as number | string | null | undefined);
  }

  private computeSpread() {
    const { bestAsk, bestBid } = this.quoteState;
    if (bestAsk === null || bestBid === null) return null;
    return bestAsk - bestBid;
  }

  private loadJwt() {
    // support both vite client builds and node contexts so tests/tools can
    // provide credentials without a browser. prefer an interactive session
    // token saved by the ui, then fall back to environment variables.
    const storedToken = loadStoredSessionToken();
    if (storedToken) return storedToken;

    if (typeof import.meta !== "undefined") {
      const env = (import.meta as { env?: Record<string, unknown> }).env;
      const projectxJwt = typeof env?.PROJECTX_JWT === "string" ? env.PROJECTX_JWT : null;
      const viteProjectxJwt = typeof env?.VITE_PROJECTX_JWT === "string" ? env.VITE_PROJECTX_JWT : null;

      if (projectxJwt?.length) return projectxJwt;
      if (viteProjectxJwt?.length) return viteProjectxJwt;
    }

    if (globalProcess?.env?.PROJECTX_JWT) {
      return String(globalProcess.env.PROJECTX_JWT);
    }

    return "";
  }

  private async resolveContractId(symbol: string, jwt: string) {
    const symbolId = this.toSymbolId(symbol);
    const attempts = [
      { live: false, label: "paper" },
      { live: true, label: "live" },
    ];

    for (const attempt of attempts) {
      const available = await this.fetchAvailableContracts(symbolId, jwt, attempt.live);
      if (available) return available;
    }

    const results = await this.searchContracts(symbol, jwt);
    if (results.length === 0) {
      throw new Error(`No contracts returned for ${symbol}.`);
    }

    const match = this.pickContract(results, symbol);
    if (match) return match;

    // fall back to the first contract if nothing matches the symbol prefix to
    // avoid blocking users when the api shape changes or symbols differ by
    // expiration code.
    const fallback = results.find((c) => c.id || c.contractId);
    if (fallback?.id || fallback?.contractId) {
      return fallback.id ?? fallback.contractId!;
    }

    throw new Error(`${symbol} contract not found in search results.`);
  }

  private async fetchAvailableContracts(symbolId: string, jwt: string, live: boolean) {
    const response = await fetch(`${REST_BASE}/api/Contract/available`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${jwt}`,
      },
      body: JSON.stringify({ live }),
    });

    if (!response.ok) {
      throw new Error(
        `Available contracts (${live ? "live" : "paper"}) failed with status ${response.status}`
      );
    }

    const body = await response.json();
    const contracts = this.normalizeContractResults(body);

    const match = contracts.find(
      (c) =>
        (c.symbolId?.toUpperCase?.() === symbolId.toUpperCase() ||
          this.getContractId(c)?.toUpperCase().startsWith(symbolId.toUpperCase())) &&
        this.hasActiveContract(c)
    );

    return this.getContractId(match ?? {} as ContractSearchResult);
  }

  private async searchContracts(symbol: string, jwt: string) {
    const attempts = [
      { live: false, label: "paper" },
      { live: true, label: "live" },
    ];

    for (const attempt of attempts) {
      const response = await fetch(`${REST_BASE}/api/Contract/search`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${jwt}`,
        },
        body: JSON.stringify({ live: attempt.live, searchText: symbol }),
      });

      if (!response.ok) {
        throw new Error(`Contract search (${attempt.label}) failed with status ${response.status}`);
      }

      const body = await response.json();
      const results = this.normalizeContractResults(body);

      if (results.length > 0) return results as ContractSearchResult[];
    }

    return [] as ContractSearchResult[];
  }

  private pickContract(results: ContractSearchResult[], symbol: string) {
    const upperSymbol = symbol.toUpperCase();
    const symbolId = this.toSymbolId(symbol).toUpperCase();
    const prefixes = [`CON.${symbolId}`, symbolId, upperSymbol];

    const matchId = (result: ContractSearchResult | undefined | null) => {
      if (!result) return null;
      return this.getContractId(result);
    };

    const bySymbol = results.find((result) => {
      const id = matchId(result)?.toUpperCase();
      const matchesSymbolId = result.symbolId?.toUpperCase() === symbolId;
      return matchesSymbolId || (!!id && prefixes.some((prefix) => id.startsWith(prefix)));
    });

    const activeSymbol = results.find(
      (result) => this.hasActiveContract(result) && matchId(result)?.toUpperCase().startsWith(`CON.${symbolId}`)
    );

    return matchId(activeSymbol ?? bySymbol ?? results.find((r) => matchId(r)) ?? null);
  }

  private toSymbolId(symbol: string) {
    const upperSymbol = symbol.toUpperCase();
    if (upperSymbol === "MNQ") return "F.US.MNQ";
    if (upperSymbol === "MES") return "F.US.MES";
    return `F.US.${upperSymbol}`;
  }

  private connectMarketHub(jwt: string) {
    return new HubConnectionBuilder()
      .withUrl(`${MARKET_HUB}?access_token=${encodeURIComponent(jwt)}`, {
        transport: HttpTransportType.WebSockets,
        skipNegotiation: true,
      })
      .withAutomaticReconnect({
        nextRetryDelayInMilliseconds: (ctx: RetryContext) =>
          Math.min(30_000, 1000 * 2 ** Math.min(ctx.previousRetryCount, 10)),
      })
      .configureLogging(LogLevel.Error)
      .build();
  }

  private async subscribe(connection: HubConnection, contractId: string) {
    const invokeSubscriptions = async () => {
      await connection.invoke("SubscribeContractQuotes", contractId);

      try {
        await connection.invoke("SubscribeContractMarketDepth", contractId, this.options.levels);
      } catch (err) {
        // Older hubs expect only the contractId; retry without the levels arg so we still
        // receive depth updates instead of failing the whole subscription sequence.
        await connection.invoke("SubscribeContractMarketDepth", contractId);

        if (err instanceof Error) {
          console.warn("Depth subscription fallback without level count", err.message);
        }
      }
    };

    try {
      await invokeSubscriptions();
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      const connectionClosed = connection.state !== HubConnectionState.Connected;
      const invocationCanceled = message.includes("Invocation canceled");
      const canRestart = connection.state === HubConnectionState.Disconnected;

      if (connectionClosed || invocationCanceled) {
        // the hub can momentarily close the underlying socket between start and
        // the first invocation. give it one more try; restart only if the hub
        // fully disconnected.
        if (canRestart) {
          await connection.start();
        }

        await invokeSubscriptions();
        return;
      }

      throw err;
    }
  }

  private attachHubHandlers(connection: HubConnection) {
    connection.on("GatewayQuote", (_ignored: unknown, payload: QuotePayload) => {
      this.handleQuote(payload);
    });

    connection.on("GatewayDepth", (_ignored: unknown, payload: DepthPayload) => {
      this.handleDepth(payload);
    });

    connection.onreconnected(async () => {
      this.connected = true;
      this.emitStatus();

      if (this.contractId) {
        try {
          await this.subscribe(connection, this.contractId);
        } catch (err) {
          console.error("Market depth resubscribe failed", err);
        }
      }
    });

    connection.onreconnecting(() => {
      this.connected = false;
      this.emitStatus();
    });

    connection.onclose(() => {
      this.connected = false;
      this.emitStatus();
    });
  }

  private handleQuote(payload: QuotePayload) {
    this.quoteState.last = this.pickNumberField(payload, "lastPrice", this.quoteState.last);
    this.quoteState.bestBid = this.pickNumberField(payload, "bestBidPrice", this.quoteState.bestBid);
    this.quoteState.bestAsk = this.pickNumberField(payload, "bestAskPrice", this.quoteState.bestAsk);
    this.quoteState.volume = this.pickNumberField(payload, "volume", this.quoteState.volume);

    // only update ts if the payload actually includes a ts field
    if (payload.timestamp !== undefined || payload.time !== undefined) {
      this.quoteState.ts = payload.timestamp ?? payload.time ?? null;
    }

    // always ensure we have some timestamp for the ui
    if (!this.quoteState.ts) {
      this.quoteState.ts = new Date().toISOString();
    }

    this.emitQuoteThrottled();
  }

  private handleDepth(payload: DepthPayload) {
    if (payload.type === 6) {
      this.orderBook.reset();
      this.latestDepth = { bids: [], asks: [] };
      this.emitDepthThrottled();
      return;
    }

    const side = this.resolveDepthSide(payload);
    if (!side) return;

    this.orderBook.apply({ ...payload, side });
    this.latestDepth = this.orderBook.snapshot(this.options.levels);
    this.quoteState.bestBid = this.latestDepth.bids[0]?.price ?? null;
    this.quoteState.bestAsk = this.latestDepth.asks[0]?.price ?? null;
    this.emitDepthThrottled();
    this.emitQuoteThrottled();
  }

  private resolveDepthSide(payload: DepthPayload): DepthSide | null {
    if (payload.side === "Bid" || payload.side === "Ask") return payload.side;

    switch (payload.type) {
      case 1: // Ask
      case 3: // BestAsk
      case 10: // NewBestAsk
        return "Ask";
      case 2: // Bid
      case 4: // BestBid
      case 9: // NewBestBid
        return "Bid";
      default:
        return null;
    }
  }

  private normalizeContractResults(payload: unknown): ContractSearchResult[] {
    if (Array.isArray(payload)) return payload as ContractSearchResult[];
    if (payload && typeof payload === "object") {
      const obj = payload as Record<string, unknown>;
      if (Array.isArray(obj.contracts)) return obj.contracts as ContractSearchResult[];
      if (Array.isArray(obj.results)) return obj.results as ContractSearchResult[];
      if (Array.isArray(obj.data)) return obj.data as ContractSearchResult[];
    }

    return [] as ContractSearchResult[];
  }

  private getContractId(result: ContractSearchResult | null | undefined) {
    if (!result) return null;

    if (typeof result.activeContract === "string") return result.activeContract;
    if (result.activeContract && typeof result.activeContract === "object") {
      return result.activeContract.contractId ?? result.activeContract.id ?? null;
    }

    return result.contractId ?? result.id ?? null;
  }

  private hasActiveContract(result: ContractSearchResult) {
    if (result.activeContract === true) return true;
    if (typeof result.activeContract === "string") return true;
    if (result.activeContract && typeof result.activeContract === "object") {
      return Boolean(result.activeContract.contractId ?? result.activeContract.id);
    }

    return false;
  }

  private registerShutdownHooks() {
    if (typeof window !== "undefined") {
      const handler = () => {
        void this.stop();
      };
      window.addEventListener("beforeunload", handler);
      this.stopHandlers.push(() => window.removeEventListener("beforeunload", handler));
    }

    if (globalProcess && typeof globalProcess.on === "function") {
      const sigHandler = () => {
        void this.stop();
      };
      globalProcess.on!("SIGINT", sigHandler);
      this.stopHandlers.push(() => globalProcess.off?.("SIGINT", sigHandler));
    }
  }
}

export const MarketDataService = {
  init(options?: Partial<MarketDataOptions>) {
    return new MarketDataServiceImpl(options);
  },
};

export { OrderBook, MarketDataServiceImpl };
