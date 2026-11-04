import { useEffect, useRef, useState } from "react";
import { normalizeOrderBookContractId, type OrderBookStore } from "./orderBook";
import { frameAtTime, HEATMAP_HISTORY_MS, HEATMAP_SAMPLE_MS, LiquidityHeatmapBuffer, quantityAtTick,
  type LiquidityView } from "./liquidityHeatmap";
import { drawLiquidityHeatmap, heatmapPrice, heatmapTime, type HeatmapGeometry } from "./liquidityHeatmapCanvas";
import { useHeatmapTickSize } from "./useHeatmapTickSize";
import { streamProjectXMarketTrades } from "../../lib/api";
import { HeatmapTradeBuffer, type TradeBubbleView } from "./heatmapTrades";
import { usePageVisibility } from "./usePageVisibility";
import "./LiquidityHeatmapPanel.css";

interface Props {
  store: OrderBookStore;
  contractId: string | null | undefined;
  symbol?: string | null;
  tickSize?: number | null;
  demoMode?: boolean;
}

export function LiquidityHeatmapPanel({ store, contractId, symbol, tickSize, demoMode = false }: Props) {
  const contract = normalizeOrderBookContractId(contractId);
  const metadata = useHeatmapTickSize(demoMode ? null : contract, tickSize);
  return <HeatmapDisplay key={`${contract}:${metadata.tickSize}:${demoMode}`} store={store} contractId={contract}
    symbol={symbol} tickSize={metadata.tickSize} demoMode={demoMode} metadataError={metadata.error} retry={metadata.retry} />;
}

function HeatmapDisplay({ store, contractId, symbol, tickSize, demoMode, metadataError, retry }: Props & {
  metadataError: string | null; retry: () => void;
}) {
  const [buffer] = useState(() => new LiquidityHeatmapBuffer(contractId, tickSize ?? null));
  const [view, setView] = useState<LiquidityView>(() => buffer.view(Date.now()));
  const [tradeBuffer] = useState(() => new HeatmapTradeBuffer(contractId, tickSize));
  const [tradeView, setTradeView] = useState<TradeBubbleView>(() => tradeBuffer.view(Date.now()));
  const [tradeStatus, setTradeStatus] = useState("Waiting for trade feed");
  const [showTrades, setShowTrades] = useState(true);
  const [minTradeVolume, setMinTradeVolume] = useState(1);
  const [bubbleScale, setBubbleScale] = useState(1);
  const pageVisible = usePageVisibility();
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(false);
  const [status, setStatus] = useState(view.status);
  const [expanded, setExpanded] = useState(false);
  const [duration, setDuration] = useState(3 * 60_000);
  const [priceTicks, setPriceTicks] = useState(80);
  const [intensity, setIntensity] = useState(1);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [priceCenter, setPriceCenter] = useState<number | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const geometry = useRef<HeatmapGeometry | null>(null);
  const end = anchor ?? view.now;
  const latest = [...view.frames].reverse().find(frame => !frame.gap);
  const midpoint = latest?.bestBid != null && latest.bestAsk != null ? (latest.bestBid + latest.bestAsk) / 2
    : latest?.bestBid ?? latest?.bestAsk ?? null;
  const center = priceCenter ?? midpoint ?? tradeView.bubbles.at(-1)?.price ?? null;

  useEffect(() => {
    if (demoMode || !contractId || !tickSize || !pageVisible) return;
    let closed = false;
    const stop = streamProjectXMarketTrades(contractId, {
      onTrades: batch => {
        if (closed) return;
        tradeBuffer.add(batch, Date.now());
        setTradeStatus("Receiving trade prints");
      },
      onState: state => {
        if (closed) return;
        if (state.state === "disconnected" || state.state === "unavailable") tradeBuffer.markGap();
        setTradeStatus(state.state === "connected" ? "Trade feed connected" : state.state === "market_closed" ? "Trade market closed"
          : state.state === "unavailable" ? "Trade feed unavailable" : state.state === "disconnected" ? "Trade feed disconnected" : "Connecting trade feed…");
      },
    });
    return () => { closed = true; stop(); tradeBuffer.markGap(); };
  }, [tradeBuffer, contractId, tickSize, demoMode, pageVisible]);

  useEffect(() => {
    if (demoMode) return;
    const timer = setInterval(() => {
      const now = Date.now();
      buffer.sample(store, now);
      const next = buffer.view(now);
      setStatus(next.status);
      if (!pausedRef.current) { setView(next); setTradeView(tradeBuffer.view(now)); }
    }, HEATMAP_SAMPLE_MS);
    return () => clearInterval(timer);
  }, [buffer, store, tradeBuffer, demoMode]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const draw = () => { geometry.current = drawLiquidityHeatmap(element, view.frames, {
      end, duration, center, tickSize: tickSize ?? null, priceTicks, intensity,
      trades: showTrades ? tradeView.bubbles : [], minTradeVolume, bubbleScale,
    }); };
    draw();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(draw);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [view, end, duration, center, tickSize, priceTicks, intensity, expanded, tradeView, showTrades, minTradeVolume, bubbleScale]);

  const followLive = () => {
    pausedRef.current = false; setPaused(false); setAnchor(null); setPriceCenter(null); setHover(null);
    setView(buffer.view(Date.now()));
    setTradeView(tradeBuffer.view(Date.now()));
  };
  const first = view.frames[0]?.time ?? view.now;
  const pause = () => {
    pausedRef.current = !paused;
    setPaused(!paused); setHover(null);
    if (paused) { setAnchor(null); setView(buffer.view(Date.now())); setTradeView(tradeBuffer.view(Date.now())); }
  };
  const movePrice = (direction: number) => {
    if (center !== null && tickSize) setPriceCenter(center + direction * priceTicks / 4 * tickSize);
  };

  return <section className="topbot-panel liquidity-heatmap" aria-label="Liquidity Heatmap">
    <header className="liquidity-heading">
      <div><h2>Liquidity Heatmap <span>{symbol?.split(".").at(-1) ?? ""}</span></h2>
        <p>Resting liquidity &amp; executed prints · {contractId ?? "No contract selected"}</p></div>
      <div className="liquidity-actions">
        <span className="liquidity-status" data-live={status.startsWith("Live")} aria-live="polite">
          {demoMode ? "Demo · stream off" : metadataError ?? status}
        </span>
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "Collapse" : "Expand"}</button>
      </div>
    </header>
    <div className="liquidity-toolbar">
      <label>Time <select aria-label="Heatmap time zoom" value={duration} onChange={event => setDuration(Number(event.target.value))}>
        {[1, 3, 5, 10].map(minutes => <option key={minutes} value={minutes * 60_000}>{minutes} min</option>)}
      </select></label>
      <label>Price <select aria-label="Heatmap price zoom" value={priceTicks} onChange={event => setPriceTicks(Number(event.target.value))}>
        {[40, 80, 160, 320].map(ticks => <option key={ticks} value={ticks}>{ticks} ticks</option>)}
      </select></label>
      <label className="liquidity-intensity">Intensity <input aria-label="Heatmap intensity" type="range" min="0.4" max="3" step="0.1"
        value={intensity} onChange={event => setIntensity(Number(event.target.value))} /></label>
      <div className="liquidity-actions">
        <button type="button" aria-pressed={paused} onClick={pause}>{paused ? "Resume display" : "Pause display"}</button>
        <button type="button" onClick={followLive}>Follow live / recenter</button>
      </div>
    </div>
    <div className="liquidity-trade-toolbar">
      <label><input type="checkbox" checked={showTrades} onChange={event => { setShowTrades(event.target.checked); setHover(null); }} />Trade bubbles</label>
      <label>Min size <select aria-label="Minimum trade bubble volume" value={minTradeVolume} disabled={!showTrades}
        onChange={event => { setMinTradeVolume(Number(event.target.value)); setHover(null); }}>
        {[1, 5, 10, 25, 50, 100].map(size => <option key={size} value={size}>{size} contract{size === 1 ? "" : "s"}</option>)}
      </select></label>
      <label>Bubble size <input aria-label="Trade bubble size" type="range" min="0.5" max="2" step="0.1" value={bubbleScale}
        disabled={!showTrades} onChange={event => setBubbleScale(Number(event.target.value))} /></label>
      <span role="status">{demoMode ? "Demo · trades off" : !pageVisible ? "Trade feed paused · page hidden" : !tickSize ? "Waiting for tick size" : tradeStatus}
        {tradeView.partial ? " · partial trade history" : ""}</span>
    </div>
    <div className="liquidity-legend">
      <span>Resting size <i className="liquidity-gradient" />Low → high</span>
      <span><i className="liquidity-bids" />Best bid</span><span><i className="liquidity-asks" />Best ask</span>
      {showTrades && <><span><i className="liquidity-trade-buy" />Buy prints</span><span><i className="liquidity-trade-sell" />Sell prints</span>
        <span><i className="liquidity-trade-unknown" />Unknown side</span></>}
      <span className="liquidity-quality">Incomplete depth · observed levels only</span>
    </div>
    <canvas ref={canvas} className="liquidity-canvas" style={{ height: expanded ? 520 : 320 }} role="img"
      aria-label="Live liquidity heatmap. Heat colors show resting size. Trade bubbles show executed volume: green buy, pink sell, gray unknown, using provider-reported side. Time horizontally in New York, price vertically on the right. Green and pink lines show best bid and ask. On wide screens, the book profile shows depth at the displayed right edge. Blank cells are unknown depth, amber bands are depth gaps."
      onPointerLeave={() => setHover(null)} onPointerMove={event => {
        const g = geometry.current, rect = event.currentTarget.getBoundingClientRect();
        // The app supports CSS zoom; pointer coordinates are viewport pixels,
        // while the canvas geometry is in its unscaled layout pixels.
        const x = (event.clientX - rect.left) * event.currentTarget.clientWidth / rect.width;
        const y = (event.clientY - rect.top) * event.currentTarget.clientHeight / rect.height;
        if (!g || !tickSize || center === null || x < g.left || x > g.right || y < g.top || y > g.bottom) { setHover(null); return; }
        // Reverse paint order picks the visible bubble when circles overlap.
        let hit;
        for (let i = (g.bubbles?.length ?? 0) - 1; i >= 0; i--) {
          const bubble = g.bubbles![i];
          if (Math.hypot(x - bubble.x, y - bubble.y) <= Math.max(5, bubble.radius)) { hit = bubble; break; }
        }
        if (hit) {
          const trade = hit.trade;
          setHover(`${heatmapTime(trade.time)} ET · ${heatmapPrice(trade.price)} · ${trade.side === "unknown" ? "Unknown side" : `Provider-reported ${trade.side}`} · ${trade.volume.toLocaleString()} contracts executed · ${trade.prints} print${trade.prints === 1 ? "" : "s"} in a 250 ms group`);
          return;
        }
        const time = g.start + (x - g.left) / (g.right - g.left) * (g.end - g.start);
        const tick = Math.round((g.low + (g.bottom - y) / (g.bottom - g.top) * (g.high - g.low)) / tickSize);
        const frame = frameAtTime(view.frames, time);
        const bid = frame && !frame.gap ? quantityAtTick(frame.bids, tick) : null;
        const ask = frame && !frame.gap ? quantityAtTick(frame.asks, tick) : null;
        setHover(`${heatmapTime(frame?.time ?? time)} ET · ${heatmapPrice(tick * tickSize)} · ${frame?.gap ?? (!frame ? "No recorded sample"
          : `Bid: ${bid === null ? "not observed" : `${bid} contracts`} · Ask: ${ask === null ? "not observed" : `${ask} contracts`}`)}`);
      }} />
    <div className="liquidity-inspector" aria-label="Heatmap hover details">
      {hover ?? (paused ? "Display paused · collection continues in the rolling buffer" : "Hover a bubble for executed volume, or the heatmap for resting quantity")}
    </div>
    <div className="liquidity-navigation">
      <div className="liquidity-actions">
        <button type="button" aria-label="Earlier heatmap history" disabled={end - duration <= first} onClick={() => setAnchor(Math.max(first + duration, end - duration / 2))}>← Earlier</button>
        <button type="button" aria-label="Later heatmap history" disabled={anchor === null || end >= view.now} onClick={() => setAnchor(Math.min(view.now, end + duration / 2))}>Later →</button>
        <button type="button" aria-label="Move heatmap price range up" disabled={center === null} onClick={() => movePrice(1)}>Price ↑</button>
        <button type="button" aria-label="Move heatmap price range down" disabled={center === null} onClick={() => movePrice(-1)}>Price ↓</button>
      </div>
      <span>{paused ? "Paused" : anchor !== null || priceCenter !== null ? "Manual view" : "Following live"} · {tickSize ? `${heatmapPrice(tickSize)} / tick` : "Tick size pending"} · ET</span>
    </div>
    <p className="liquidity-footnote">{view.recordingSince === null ? "Recording starts with the first fresh depth sample." : `Recording since ${heatmapTime(view.recordingSince)} ET · retained from ${heatmapTime(first)} ET.`}
      {` Up to ${HEATMAP_HISTORY_MS / 60_000} minutes in this page, sampled at 2 Hz; nearest 80 observed levels per side. Earlier history is unavailable.`}</p>
    <p className="liquidity-footnote">Heat colors show resting orders; bubbles show executed trades grouped by price and provider-reported side within 250 ms. Larger bubbles mean more volume (size capped). Gray = unknown side. Side is not verified as exchange-native.</p>
    <p className="liquidity-footnote">Blank = unobserved; amber = interrupted depth. Trade gaps are not backfilled. Up to 10 minutes / 20,000 bubble groups in memory; partial history is flagged. Without provider trade IDs, replayed deliveries cannot be distinguished. These displays do not change the strategy.</p>
    {metadataError && <button type="button" onClick={retry}>Retry tick-size lookup</button>}
  </section>;
}
