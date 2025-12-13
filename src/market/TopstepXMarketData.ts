import axios from "axios";
import {
  HubConnection,
  HubConnectionBuilder,
  HubConnectionState,
  HttpTransportType,
  LogLevel,
} from "@microsoft/signalr";

const REST_BASE = "https://api.topstepx.com";
const MARKET_HUB = "https://rtc.topstepx.com/hubs/market";
const DEFAULT_OPTIONS = { symbol: "MNQ", levels: 10, throttleMs: 150 } as const;

type MaybeProcess = {
  env?: Record<string, string | undefined>;
  on?: (event: string, cb: () => void) => void;
  off?: (event: string, cb: () => void) => void;
};

const globalProcess = (globalThis as { process?: MaybeProcess }).process;

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
  side: DepthSide;
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

type UnsubscribeFn = () => void;

type MarketDataCallbacks = {
  onQuote(cb: (quote: QuoteUpdate) => void): UnsubscribeFn;
  onDepth(cb: (snapshot: DepthSnapshot) => void): UnsubscribeFn;
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

  apply(update: DepthPayload) {
    const price = toNumber(update.price);
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
        throw new Error("PROJECTX_JWT environment variable is missing.");
      }

      this.contractId = await this.resolveContractId(this.options.symbol, jwt);
      this.connection = this.connectMarketHub(jwt);

      this.attachHubHandlers(this.connection);

      await this.connection.start();
      await this.subscribe(this.connection, this.contractId);
      this.connected = true;
      this.registerShutdownHooks();
    } catch (err) {
      this.started = false;
      this.connected = false;
      throw err;
    }
  }

  async stop() {
    if (!this.started) return;
    this.started = false;
    this.connected = false;

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

  private computeSpread() {
    const { bestAsk, bestBid } = this.quoteState;
    if (bestAsk === null || bestBid === null) return null;
    return bestAsk - bestBid;
  }

  private loadJwt() {
    if (typeof import.meta !== "undefined" && import.meta.env) {
      if (import.meta.env.PROJECTX_JWT) return String(import.meta.env.PROJECTX_JWT);
      if (import.meta.env.VITE_PROJECTX_JWT) return String(import.meta.env.VITE_PROJECTX_JWT);
    }

    if (globalProcess?.env?.PROJECTX_JWT) {
      return String(globalProcess.env.PROJECTX_JWT);
    }

    return "";
  }

  private async resolveContractId(symbol: string, jwt: string) {
    const res = await axios.post<{ id: string }[]>(
      `${REST_BASE}/api/Contract/search`,
      { live: false, searchText: symbol },
      {
        headers: { Authorization: `Bearer ${jwt}` },
      }
    );

    const match = res.data.find((c) => c.id?.startsWith("CON.F.US.MNQ"));
    if (!match?.id) {
      throw new Error("MNQ contract not found in search results.");
    }

    return match.id;
  }

  private connectMarketHub(jwt: string) {
    const connection = new HubConnectionBuilder()
      .withUrl(`${MARKET_HUB}?access_token=${encodeURIComponent(jwt)}`, {
        transport: HttpTransportType.WebSockets,
        skipNegotiation: true,
      })
      .withAutomaticReconnect({
        nextRetryDelayInMilliseconds: (ctx) =>
          Math.min(30_000, 1000 * 2 ** Math.min(ctx.previousRetryCount, 10)),
      })
      .configureLogging(LogLevel.Error)
      .build();

    return connection;
  }

  private async subscribe(connection: HubConnection, contractId: string) {
    await connection.invoke("SubscribeContractQuotes", contractId);
    await connection.invoke("SubscribeContractMarketDepth", contractId);
  }

  private attachHubHandlers(connection: HubConnection) {
    connection.on("GatewayQuote", (_ignored, payload: QuotePayload) => {
      this.handleQuote(payload);
    });

    connection.on("GatewayDepth", (_ignored, payload: DepthPayload) => {
      this.handleDepth(payload);
    });

    connection.onreconnected(async () => {
      this.connected = true;
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
    });

    connection.onclose(() => {
      this.connected = false;
    });
  }

  private handleQuote(payload: QuotePayload) {
    this.quoteState.last = toNumber(payload.lastPrice);
    this.quoteState.bestBid = toNumber(payload.bestBidPrice);
    this.quoteState.bestAsk = toNumber(payload.bestAskPrice);
    this.quoteState.volume = toNumber(payload.volume);
    this.quoteState.ts = payload.timestamp || payload.time || new Date().toISOString();

    this.emitQuoteThrottled();
  }

  private handleDepth(payload: DepthPayload) {
    this.orderBook.apply(payload);
    this.latestDepth = this.orderBook.snapshot(this.options.levels);
    this.emitDepthThrottled();
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
      globalProcess.on("SIGINT", sigHandler);
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
