import { MarketDataService } from "./TopstepXMarketData";

type DemoState = {
  lastQuote: string;
  depthVersion: number;
};

const demoState: DemoState = {
  lastQuote: "--",
  depthVersion: 0,
};

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

const logThrottled = createThrottled(() => {
  console.info(`[MarketData] ${demoState.lastQuote} | book updates: ${demoState.depthVersion}`);
}, 250);

export async function startMarketDataDemo() {
  const md = MarketDataService.init({ symbol: "MNQ", levels: 10, throttleMs: 150 });

  md.onQuote((quote) => {
    const ts = quote.ts ? new Date(quote.ts).toISOString() : "--";
    const bestBid = quote.bestBid ?? 0;
    const bestAsk = quote.bestAsk ?? 0;
    const spread = quote.spread ?? bestAsk - bestBid;
    demoState.lastQuote = `Last ${quote.last ?? "--"} | Bid ${bestBid} / Ask ${bestAsk} | Spread ${spread ?? "--"} | ${ts}`;
    logThrottled();
  });

  md.onDepth((snapshot) => {
    demoState.depthVersion += 1;
    if (snapshot.bids.length && snapshot.asks.length) {
      logThrottled();
    }
  });

  try {
    await md.start();
  } catch (err) {
    console.error("Market data demo failed to start", err);
  }

  return md;
}
