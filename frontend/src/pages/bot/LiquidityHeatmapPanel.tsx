import { useCallback, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { normalizeOrderBookContractId, type OrderBookStore } from "./orderBook";
import { HEATMAP_SAMPLE_MS, HEATMAP_TIMER_MS, LiquidityHeatmapBuffer, type FeedStatus, type LiquidityView } from "./liquidityHeatmap";
import { drawHeatmapOverlay, drawLiquidityHeatmap, HEAT_GRADIENT_CSS, heatmapMinute, heatmapPrice, heatmapTime, heatPosition,
  inspectHeatmap, inspectPoint, quoteExtent, visibleColumns, type BubbleHit, type HeatmapGeometry, type HeatmapRenderInput, type InspectResult } from "./liquidityHeatmapCanvas";
import { useHeatmapTickSize } from "./useHeatmapTickSize";
import { streamProjectXMarketTrades } from "../../lib/api";
import { HeatmapTradeBuffer, type TradeBubbleView } from "./heatmapTrades";
import { useStreamVisibility } from "./useStreamVisibility";
import { ServerClock } from "./heatmapClock";
import { HeatmapHistoryStore, INT32_NONE, isCovered, loadedSpans, sessionStartMs, TIER_MS, type HeatmapGap, type HistoryFetcher,
  type HistoryStatus } from "./heatmapHistory";
import { chooseTier, clampTime, defaultViewport, fitPrice, followPrice, formatSpan, niceSaturation, panPrice, panTime, PRICE_RANGES,
  recenter, TIME_SPANS, viewEnd, zoomPrice, zoomTime, type HeatmapViewport } from "./heatmapViewport";
import { HeatmapNavigator } from "./HeatmapNavigator";
import "./LiquidityHeatmapPanel.css";

interface Props {
  store: OrderBookStore;
  contractId: string | null | undefined;
  symbol?: string | null;
  tickSize?: number | null;
  demoMode?: boolean;
  /** Owned trading account used to authorize recorded session history. */
  accountId?: number | null;
  /** Test seam; production uses the authenticated history API. */
  historyFetcher?: HistoryFetcher;
}

export function LiquidityHeatmapPanel({ store, contractId, symbol, tickSize, demoMode = false, accountId = null, historyFetcher }: Props) {
  const contract = normalizeOrderBookContractId(contractId);
  const metadata = useHeatmapTickSize(demoMode ? null : contract, tickSize);
  return <HeatmapDisplay key={`${contract}:${metadata.tickSize}:${demoMode}:${accountId}`} store={store} contractId={contract}
    symbol={symbol} tickSize={metadata.tickSize} demoMode={demoMode} accountId={accountId} historyFetcher={historyFetcher}
    metadataError={metadata.error} retry={metadata.retry} />;
}

interface Settings {
  saturation: "auto" | number; showTrades: boolean; minTradeVolume: number; bubbleScale: number; profile: boolean;
  span: number; rangeTicks: number;
}
const SETTINGS_KEY = "topsignal.liquidityHeatmap.v2";
const SATURATIONS = [10, 25, 50, 100, 250, 500] as const;
const MIN_SIZES = [1, 2, 5, 10, 25, 50, 100] as const;
const DEFAULT_SETTINGS: Settings = { saturation: "auto", showTrades: true, minTradeVolume: 1, bubbleScale: 1, profile: true,
  span: 300_000, rangeTicks: 160 };
function loadSettings(): Settings {
  try {
    const value = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") as Partial<Settings>;
    return {
      saturation: value.saturation === "auto" || SATURATIONS.includes(value.saturation as never) ? value.saturation! : DEFAULT_SETTINGS.saturation,
      showTrades: typeof value.showTrades === "boolean" ? value.showTrades : DEFAULT_SETTINGS.showTrades,
      minTradeVolume: MIN_SIZES.includes(value.minTradeVolume as never) ? value.minTradeVolume! : DEFAULT_SETTINGS.minTradeVolume,
      bubbleScale: typeof value.bubbleScale === "number" && value.bubbleScale >= 0.5 && value.bubbleScale <= 2 ? value.bubbleScale : 1,
      profile: typeof value.profile === "boolean" ? value.profile : DEFAULT_SETTINGS.profile,
      span: TIME_SPANS.includes(value.span as never) ? value.span! : DEFAULT_SETTINGS.span,
      rangeTicks: PRICE_RANGES.includes(value.rangeTicks as never) ? value.rangeTicks! : DEFAULT_SETTINGS.rangeTicks,
    };
  } catch { return DEFAULT_SETTINGS; }
}
function saveSettings(settings: Settings) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* Preferences are optional. */ }
}

const GAP_LABELS: Record<string, string> = {
  disconnected: "Feed disconnected", reset: "Depth reset / reconnect", awaiting_depth: "Waiting for depth after connect",
  stale: "Stale depth (no update for over 2 s)", future: "Provider time ahead of receipt", no_levels: "No observed levels",
  crossed: "Crossed or locked book", off_grid: "Price off the contract tick grid", warming: "Rebuilding book",
  unreadable_segment: "Unreadable recording segment", contract_changed: "Contract changed", not_recorded: "Not recorded (recorder offline)",
};
const gapLabel = (reason: string) => GAP_LABELS[reason] ?? reason;

type Tone = "ok" | "warn" | "bad" | "muted";
function feedBadge(feed: FeedStatus, now: number, demo: boolean, metadataError: string | null, tickSize: number | null | undefined): { tone: Tone; label: string } {
  if (demo) return { tone: "muted", label: "Demo · stream off" };
  if (metadataError) return { tone: "warn", label: metadataError };
  if (!tickSize) return { tone: "muted", label: "Waiting for tick size" };
  switch (feed.state) {
    case "live": return { tone: "ok", label: "Feed live" };
    case "incomplete": return { tone: "warn", label: "Feed live · one side missing" };
    case "stale": return { tone: "warn", label: `Feed stale${feed.lastDepthAt ? ` · ${Math.max(2, Math.round((now - feed.lastDepthAt) / 1000))} s` : ""}` };
    case "disconnected": return { tone: "bad", label: "Feed disconnected" };
    case "reconnecting": return { tone: "warn", label: "Reconnecting…" };
    case "closed": return { tone: "muted", label: "Market closed" };
    case "unavailable": return { tone: "bad", label: "Depth unavailable" };
    case "invalid": return { tone: "warn", label: feed.detail };
    default: return { tone: "muted", label: "Connecting…" };
  }
}
function historyBadge(status: HistoryStatus | null): { tone: Tone; label: string; title: string } | null {
  if (!status) return null;
  const meta = status.meta;
  const deriver = meta?.deriver;
  if (deriver?.state === "storage_review_required") return { tone: "warn", label: "History paused · storage review", title: "The derived heatmap cache reached its storage bound. Nothing was deleted; review storage on the recording host." };
  if (deriver?.state === "paused") return { tone: "muted", label: "History paused · recording and analysis first", title: "History processing yields while trade analysis is reading the archive or the recorder needs to catch up. Live depth continues." };
  if (status.message && !meta) return { tone: "warn", label: "History unavailable", title: status.message };
  if (!meta) return { tone: "muted", label: "Loading session history…", title: "Loading recorded depth for this session" };
  if (deriver?.state === "building") return { tone: "muted", label: `Building history · ${deriver.pending_segments ?? "?"} min left`, title: "The recorder's committed segments are still being processed for this session." };
  if (!meta.coverage.length) return { tone: "muted", label: deriver ? "No recordings yet this session" : "Session history not built", title: "No committed recordings for this contract in the current session." };
  const through = meta.historyThrough ?? meta.coverage.at(-1)![1];
  return { tone: status.message ? "warn" : "muted", label: `History ${heatmapMinute(meta.coverage[0][0])}–${heatmapMinute(through)} ET`,
    title: status.message ? `Latest refresh failed: ${status.message}` : "Recorded by the independent depth recorder; sealed about once a minute." };
}

const qty = (value: number | null, average: boolean) => value === null ? "—" : average ? value.toFixed(value < 10 ? 1 : 0) : Math.round(value).toLocaleString();
function describe(result: InspectResult | null, bubble: BubbleHit | null, tickSize: number): string {
  if (!result) return "";
  const head = `${heatmapTime(result.time)} ET · ${heatmapPrice(result.price, tickSize)}`;
  if (bubble) {
    const side = bubble.side === "unknown" ? "Unknown side" : `Reported ${bubble.side}`;
    const group = bubble.groupMs > 1000 ? `${bubble.groupMs / 1000} s` : "250 ms";
    return `${heatmapTime(bubble.time)} ET · ${heatmapPrice(bubble.price, tickSize)} · ${side} · ${Math.round(bubble.volume).toLocaleString()} contracts in ${bubble.prints} print${bubble.prints === 1 ? "" : "s"} (${group} group)`;
  }
  if (result.state === "gap") return `${head} · ${gapLabel(result.gapReason ?? "")}`;
  if (result.state === "not_recorded") return `${head} · Not recorded`;
  if (result.state === "pending") return `${head} · Recorder data pending (sealed about once a minute)`;
  if (result.state === "future" || result.state === "before_session") return `${head} · Outside the session so far`;
  const average = result.bucketMs > HEATMAP_SAMPLE_MS;
  if (result.bid === null && result.ask === null) return `${head} · No resting size observed at this price${average ? ` in this ${result.bucketMs / 1000} s bucket` : ""}`;
  const basis = average ? ` · mean while observed (${result.observed ?? "?"} of ${result.samples ?? "?"} samples, ${result.bucketMs / 1000} s)` : "";
  if (result.mixed) return `${head} · Resting ${qty(result.bid, average)} (bid and ask within the bucket)${basis}`;
  return `${head} · Bid ${qty(result.bid, average)} · Ask ${qty(result.ask, average)}${basis}`;
}

interface Pin { time: number; tick: number; bubble: BubbleHit | null }

function HeatmapDisplay({ store, contractId, symbol, tickSize, demoMode = false, accountId, historyFetcher, metadataError, retry }: Props & {
  metadataError: string | null; retry: () => void;
}) {
  const [clock] = useState(() => new ServerClock());
  const [buffer] = useState(() => new LiquidityHeatmapBuffer(contractId, tickSize ?? null));
  const [tradeBuffer] = useState(() => new HeatmapTradeBuffer(contractId, tickSize));
  // Created and disposed in one effect, so a StrictMode/dev remount gets a working store.
  const [history, setHistory] = useState<HeatmapHistoryStore | null>(null);
  useEffect(() => {
    if (demoMode || !accountId || !contractId || !tickSize) { setHistory(null); return; }
    const store = new HeatmapHistoryStore({ accountId, contractId, tickSize }, clock, historyFetcher);
    setHistory(store);
    return () => store.dispose();
  }, [demoMode, accountId, contractId, tickSize, clock, historyFetcher]);
  const noHistory = useCallback(() => () => {}, []);
  const historyStatus = useSyncExternalStore(history?.subscribe ?? noHistory, history?.getStatus ?? (() => null));

  const [live, setLive] = useState<LiquidityView>(() => buffer.view(clock.now()));
  const [trades, setTrades] = useState<TradeBubbleView>(() => tradeBuffer.view(clock.now()));
  const [tradeStatus, setTradeStatus] = useState("Waiting for trade feed");
  const [settings, setSettingsState] = useState<Settings>(loadSettings);
  const setSettings = (patch: Partial<Settings>) => setSettingsState(previous => { const next = { ...previous, ...patch }; saveSettings(next); return next; });
  const [viewport, setViewport] = useState<HeatmapViewport>(() => ({ ...defaultViewport(), span: settings.span, rangeTicks: settings.rangeTicks }));
  const [expanded, setExpanded] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tab, setTab] = useState<"display" | "help">("display");
  const [gapsOpen, setGapsOpen] = useState(false);
  const [size, setSize] = useState({ width: 960, height: 520 });
  const [hover, setHover] = useState<{ x: number; y: number; result: InspectResult | null; bubble: BubbleHit | null } | null>(null);
  const [keyboardCursor, setKeyboardCursor] = useState<{ time: number; tick: number } | null>(null);
  const [pin, setPin] = useState<Pin | null>(null);
  const [autoSaturation, setAutoSaturation] = useState(25);
  // Prints keep flowing through brief tab switches (no artificial gap).
  const pageVisible = useStreamVisibility();
  const hoverRef = useRef(hover);
  hoverRef.current = hover;
  const chartRef = useRef<HTMLDivElement>(null);
  const baseCanvas = useRef<HTMLCanvasElement>(null);
  const overlayCanvas = useRef<HTMLCanvasElement>(null);
  const geometry = useRef<HeatmapGeometry | null>(null);
  const inputRef = useRef<HeatmapRenderInput | null>(null);
  const frame = useRef(0);
  const drag = useRef<{ x: number; y: number; viewport: HeatmapViewport; moved: boolean; pointer: string } | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ distance: number; viewport: HeatmapViewport } | null>(null);
  const mountedAt = useRef(Date.now());
  // Wait for the server clock before recording live samples or prints, so a skewed
  // browser clock cannot misplace or mislabel them (local-clock fallback after 4 s).
  // A ref, not a dependency: creating the history store must not resubscribe the live streams.
  const clockReadyRef = useRef<() => boolean>(() => true);
  clockReadyRef.current = () => !history || clock.synced || history.getStatus().state === "unavailable"
    || Date.now() - mountedAt.current > 4_000;
  const settingsId = useId(), helpId = useId(), gapsId = useId();

  const now = live.now;
  const sessionStart = historyStatus?.meta?.session.start ?? sessionStartMs(now);
  const end = viewEnd(viewport, now);
  const start = end - viewport.span;
  const tier = chooseTier(viewport.span);
  const meta = historyStatus?.meta ?? null;
  const tiles = useMemo(() => history?.tilesFor(tier, start, end) ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [history, tier, Math.floor(start / 1000), Math.floor(end / 1000), historyStatus?.version]);

  // Latest observed mid: live sample first, else the newest recorded frame/bucket.
  const midTick = useMemo(() => {
    if (!tickSize) return null;
    for (let i = live.frames.length - 1; i >= 0; i--) {
      const f = live.frames[i];
      if (f.gap) continue;
      if (f.bestBid !== null && f.bestAsk !== null) return (f.bestBid + f.bestAsk) / 2 / tickSize;
      if (f.bestBid !== null || f.bestAsk !== null) return (f.bestBid ?? f.bestAsk)! / tickSize;
    }
    const sources = [...tiles].reverse().concat(historyStatus?.summary ? [historyStatus.summary] : []);  // Newest first.
    for (const tile of sources) {
      const f = tile.frames;
      for (let i = (f?.time.length ?? 0) - 1; f && i >= 0; i--) {
        if (f.bestBid[i] !== INT32_NONE && f.bestAsk[i] !== INT32_NONE) return (f.bestBid[i] + f.bestAsk[i]) / 2;
      }
    }
    return null;
  }, [live, tiles, historyStatus?.summary, tickSize]);
  useEffect(() => { setViewport(view => followPrice(view, midTick)); }, [midTick]);

  // Live trade prints (display-only subscription; paused after the page stays hidden).
  useEffect(() => {
    if (demoMode || !contractId || !tickSize || !pageVisible) return;
    let closed = false;
    const stop = streamProjectXMarketTrades(contractId, {
      onTrades: batch => {
        if (closed || !clockReadyRef.current()) return;
        tradeBuffer.add(batch, clock.now());
        setTradeStatus("Receiving trade prints");
      },
      onState: state => {
        if (closed) return;
        if (state.state === "disconnected" || state.state === "unavailable") tradeBuffer.markGap(clock.now(), "Trade feed disconnected");
        if (state.state === "connected") tradeBuffer.resume(clock.now());
        setTradeStatus(state.state === "connected" ? "Trade feed connected" : state.state === "market_closed" ? "Trade market closed"
          : state.state === "unavailable" ? "Trade feed unavailable" : state.state === "disconnected" ? "Trade feed disconnected" : "Connecting trade feed…");
      },
    });
    return () => { closed = true; stop(); tradeBuffer.markGap(clock.now(), "Trade feed paused (page hidden or closed)"); };
  }, [tradeBuffer, contractId, tickSize, demoMode, pageVisible, clock]);

  // 2 Hz sampling of the shared book, on the server-aligned clock.
  useEffect(() => {
    if (demoMode) return;
    let lastFrame: unknown = null, lastFeed = "";
    const timer = setInterval(() => {
      if (!clockReadyRef.current()) return;
      const at = clock.now();
      buffer.sample(store, at);
      const view = buffer.view(at);
      // Re-render only when a new bucket was sampled (or the feed state changed).
      if (view.frames.at(-1) === lastFrame && view.feed.state === lastFeed) return;
      lastFrame = view.frames.at(-1); lastFeed = view.feed.state;
      setLive(view);
      setTrades(tradeBuffer.view(at));
    }, HEATMAP_TIMER_MS);
    return () => clearInterval(timer);
  }, [buffer, store, tradeBuffer, demoMode, clock]);

  // Also refresh on a timer: retries failed tiles and keeps a paused view's status and gaps current.
  const viewRef = useRef({ tier, start, end });
  viewRef.current = { tier, start, end };
  useEffect(() => {
    if (!history) return;
    const timer = setInterval(() => { const view = viewRef.current; history.ensure(view.tier, view.start, view.end); }, 5_000);
    return () => clearInterval(timer);
  }, [history]);

  // Bounded history tiles for the visible window (and the session navigator summary).
  useEffect(() => { history?.ensure(tier, start, end); },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [history, tier, Math.floor(start / 2000), Math.floor(end / 2000)]);

  useEffect(() => {
    const element = chartRef.current;
    if (!element) return;
    const measure = () => {
      const width = Math.round(element.clientWidth), height = Math.round(element.clientHeight);
      if (width > 0 && height > 0) setSize(previous => previous.width === width && previous.height === height ? previous : { width, height });
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [expanded]);

  const historyShown = useMemo(() => loadedSpans(tiles, meta?.coverage ?? []), [tiles, meta?.coverage]);
  const lowTick = (viewport.centerTick ?? midTick ?? 0) - viewport.rangeTicks / 2;
  const highTick = lowTick + viewport.rangeTicks;
  const ready = Boolean(tickSize && (viewport.centerTick !== null || midTick !== null));
  const saturation = settings.saturation === "auto" ? autoSaturation : settings.saturation;
  const input: HeatmapRenderInput | null = tickSize ? {
    width: size.width, height: size.height, start, end, now, lowTick, highTick, tickSize,
    history: ready ? tiles : [], live: ready ? live.frames : [], coverage: meta?.coverage ?? [], historyShown, gaps: meta?.gaps ?? [],
    historyThrough: meta?.historyThrough ?? null, sessionStart, liveTrades: trades.bubbles,
    showTrades: settings.showTrades, minTradeVolume: settings.minTradeVolume, bubbleScale: settings.bubbleScale,
    saturation, profile: settings.profile, profileTime: hover?.result && !hover.bubble ? hover.result.time : keyboardCursor?.time ?? null,
  } : null;
  inputRef.current = input;

  // Stable automatic saturation: recomputed on view changes, at most every 20 s while live.
  const saturationKey = `${settings.saturation}:${tier}:${viewport.rangeTicks}:${Math.round((viewport.centerTick ?? 0) / Math.max(1, viewport.rangeTicks / 4))}:${viewport.end === null ? Math.floor(now / 20_000) : Math.round(start / 1000)}:${tiles.length}`;
  useEffect(() => {
    const input = inputRef.current;
    if (!input || settings.saturation !== "auto") return;
    const values: number[] = [];
    visibleColumns(input, column => {
      for (let k = column.from; k < column.from + column.count; k++) {
        const tick = column.ticks[k];
        if (tick >= input.lowTick && tick <= input.highTick) values.push(column.qty[k]);
      }
    });
    if (values.length > 200) setAutoSaturation(niceSaturation(values));
  }, [saturationKey, settings.saturation]);

  // Wide windows usually span more price than the default range: fit once per window
  // (also for a window restored from preferences) while auto-recentering is on.
  const fittedSpan = useRef<number | null>(null);
  useEffect(() => {
    const current = inputRef.current;
    if (viewport.span <= 10 * 60_000) { fittedSpan.current = null; return; }
    if (!current || !viewport.priceFollow || fittedSpan.current === viewport.span) return;
    // Fit on the session overview when history is available, not on a few seconds of live data.
    if (history && !historyStatus?.summary && historyStatus?.state !== "unavailable") return;
    const extent = quoteExtent(current, current.start, current.end, historyStatus?.summary ? [historyStatus.summary] : []);
    if (!extent) return;
    fittedSpan.current = viewport.span;
    setViewport(view => fitPrice(view, extent, midTick));
  }, [viewport.span, viewport.priceFollow, historyStatus?.summary, historyStatus?.state, history, midTick]);

  const drawOverlay = useCallback(() => {
    const canvas = overlayCanvas.current, current = inputRef.current;
    if (!canvas || !current) return;
    const g = geometry.current;
    let cursor: { x: number; y: number } | null = hover ? { x: hover.x, y: hover.y } : null;
    if (!cursor && keyboardCursor && g) cursor = {
      x: g.left + (keyboardCursor.time - g.start) / (g.end - g.start) * (g.right - g.left),
      y: g.top + (g.highTick - keyboardCursor.tick) / (g.highTick - g.lowTick) * (g.bottom - g.top),
    };
    drawHeatmapOverlay(canvas, g, current.width, current.height, cursor, pin, current.tickSize);
  }, [hover, keyboardCursor, pin]);

  // Draw at most once per animation frame.
  useEffect(() => {
    if (frame.current) return;
    const schedule = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (callback: FrameRequestCallback) => setTimeout(() => callback(0), 16) as unknown as number;
    frame.current = schedule(() => {
      frame.current = 0;
      const canvas = baseCanvas.current, current = inputRef.current;
      if (!canvas || !current) return;
      geometry.current = drawLiquidityHeatmap(canvas, current);
      canvas.dataset.drawMs = String(geometry.current.drawMs);
      drawOverlay();
      // Data under a still cursor changes as the view moves: refresh the tooltip if its text changed.
      const hovered = hoverRef.current;
      if (hovered) {
        const result = inspectHeatmap(current, geometry.current, hovered.x, hovered.y);
        const text = describe(result, result?.bubble ?? null, current.tickSize);
        if (text !== describe(hovered.result, hovered.bubble, current.tickSize)) setHover({ ...hovered, result, bubble: result?.bubble ?? null });
      }
    });
  });
  useEffect(() => () => { if (frame.current && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame.current); }, []);
  useEffect(() => { drawOverlay(); }, [drawOverlay]);

  useEffect(() => {
    if (!expanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    overlayCanvas.current?.focus();
    return () => { document.body.style.overflow = previous; };
  }, [expanded]);

  const goLive = () => { setViewport(view => ({ ...view, end: null })); setKeyboardCursor(null); };
  const doRecenter = () => setViewport(view => recenter(view, midTick));
  const setSpan = (span: number) => {
    if (TIME_SPANS.includes(span as never)) setSettings({ span });
    setViewport(view => {
      const next = clampTime({ ...view, span }, now, sessionStart);
      // Narrowing to a short window returns to the preferred range around the current price.
      return next.priceFollow && span < view.span && span <= 10 * 60_000
        ? recenter({ ...next, rangeTicks: Math.min(next.rangeTicks, settings.rangeTicks) }, midTick) : next;
    });
  };
  const wholeSession = () => setSpan(Math.min(24 * 3_600_000, Math.max(60_000, Math.ceil((now - sessionStart) / 600_000) * 600_000)));
  const setRange = (rangeTicks: number) => { setSettings({ rangeTicks }); setViewport(view => ({ ...view, rangeTicks })); };
  const jumpTo = (time: number, span?: number) => setViewport(view => {
    const width = span ?? view.span;
    return clampTime({ ...view, span: width, end: time + width / 2 }, now, sessionStart);
  });

  const local = (event: { clientX: number; clientY: number; currentTarget: HTMLElement }) => {
    const rect = event.currentTarget.getBoundingClientRect();
    // The app supports CSS zoom: convert viewport pixels into layout pixels.
    return { x: (event.clientX - rect.left) * event.currentTarget.clientWidth / (rect.width || 1),
      y: (event.clientY - rect.top) * event.currentTarget.clientHeight / (rect.height || 1) };
  };
  const inspect = (x: number, y: number) => {
    const g = geometry.current, current = inputRef.current;
    if (!g || !current) return { result: null, bubble: null };
    const result = inspectHeatmap(current, g, x, y);
    return { result, bubble: result?.bubble ?? null };
  };

  const onPointerDown = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const point = local(event);
    pointers.current.set(event.pointerId, point);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { distance: Math.hypot(a.x - b.x, a.y - b.y), viewport };
      drag.current = null;
      return;
    }
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { ...point, viewport, moved: false, pointer: event.pointerType };
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const point = local(event);
    if (pointers.current.has(event.pointerId)) pointers.current.set(event.pointerId, point);
    const g = geometry.current;
    if (pinch.current && pointers.current.size === 2 && g) {
      const [a, b] = [...pointers.current.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (distance > 10) setViewport(zoomTime(pinch.current.viewport, pinch.current.distance / distance, null, now, sessionStart));
      return;
    }
    const active = drag.current;
    if (active && g) {
      const dx = point.x - active.x, dy = point.y - active.y;
      if (!active.moved && Math.hypot(dx, dy) < 5) return;
      active.moved = true;
      const msPerPx = (g.end - g.start) / (g.right - g.left);
      const ticksPerPx = (g.highTick - g.lowTick) / (g.bottom - g.top);
      let next = Math.abs(dx) >= 2 ? panTime(active.viewport, -dx * msPerPx, now, sessionStart) : active.viewport;
      // Touch drags only pan time unless expanded, so the page can still scroll vertically.
      if ((active.pointer !== "touch" || expanded) && Math.abs(dy) >= 3) next = panPrice(next, dy * ticksPerPx);
      setViewport(next);
      setHover(null);
      return;
    }
    if (event.pointerType === "touch") return;
    const found = inspect(point.x, point.y);
    setHover({ ...point, ...found });
    setKeyboardCursor(null);
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    const active = drag.current;
    drag.current = null;
    if (!active || active.moved) return;
    // Click / tap: pin details for this point (tap the same point again to clear).
    const point = local(event);
    const { result, bubble } = inspect(point.x, point.y);
    if (!result) return;
    setPin(previous => previous && Math.abs(previous.time - result.time) < (end - start) / 200 && previous.tick === result.tick
      ? null : { time: bubble?.time ?? result.time, tick: bubble ? Math.round(bubble.price / tickSize!) : result.tick, bubble });
  };

  // Ctrl/⌘ + wheel (and trackpad pinch) zooms time; Shift adds price zoom. Plain wheel scrolls the page unless expanded.
  useEffect(() => {
    const element = overlayCanvas.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey || expanded)) return;
      event.preventDefault();
      const g = geometry.current;
      const rect = element.getBoundingClientRect();
      const x = (event.clientX - rect.left) * element.clientWidth / (rect.width || 1);
      const y = (event.clientY - rect.top) * element.clientHeight / (rect.height || 1);
      const factor = Math.exp(Math.max(-1, Math.min(1, event.deltaY / 300)));
      setViewport(view => {
        if (event.shiftKey && g) {
          const anchor = g.highTick - (y - g.top) / (g.bottom - g.top) * (g.highTick - g.lowTick);
          return zoomPrice(view, factor, anchor);
        }
        const anchor = g ? g.start + (x - g.left) / (g.right - g.left) * (g.end - g.start) : null;
        return zoomTime(view, factor, anchor, clock.now(), sessionStart);
      });
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [expanded, sessionStart, clock]);

  const onKeyDown = (event: ReactKeyboardEvent<HTMLCanvasElement>) => {
    const g = geometry.current;
    const columnMs = Math.max(TIER_MS[tier], (end - start) / 200);
    const cursor = keyboardCursor ?? { time: (start + Math.min(end, now)) / 2, tick: Math.round(midTick ?? (lowTick + highTick) / 2) };
    const moveCursor = (dt: number, dTick: number) => {
      const next = { time: Math.min(Math.min(end, now), Math.max(start, cursor.time + dt)), tick: Math.round(cursor.tick + dTick) };
      setKeyboardCursor(next);
      setHover(null);
    };
    let handled = true;
    switch (event.key) {
      case "ArrowLeft": case "ArrowRight": {
        const direction = event.key === "ArrowLeft" ? -1 : 1;
        if (event.shiftKey) setViewport(view => panTime(view, direction * view.span / 4, now, sessionStart));
        else moveCursor(direction * columnMs, 0);
        break;
      }
      case "ArrowUp": case "ArrowDown": {
        const direction = event.key === "ArrowUp" ? 1 : -1;
        if (event.shiftKey) setViewport(view => panPrice(view, direction * view.rangeTicks / 4));
        else moveCursor(0, direction);
        break;
      }
      case "+": case "=": setViewport(view => zoomTime(view, 0.5, keyboardCursor?.time ?? null, now, sessionStart)); break;
      case "-": case "_": setViewport(view => zoomTime(view, 2, keyboardCursor?.time ?? null, now, sessionStart)); break;
      case "Enter": case " ":
        if (!g) break;
        setPin(previous => previous && previous.time === cursor.time && previous.tick === cursor.tick ? null : { ...cursor, bubble: null });
        if (!keyboardCursor) setKeyboardCursor(cursor);
        break;
      case "Escape": if (pin) setPin(null); else if (settingsOpen) setSettingsOpen(false); else if (expanded) setExpanded(false); else handled = false; break;
      case "l": case "L": case "End": goLive(); break;
      case "c": case "C": doRecenter(); break;
      case "Home": setViewport(view => clampTime({ ...view, end: sessionStart + view.span }, now, sessionStart)); break;
      default: handled = false;
    }
    if (handled) event.preventDefault();
  };

  const pinResult = pin && input ? inspectPoint(input, pin.time, pin.tick) : null;
  const keyboardResult = keyboardCursor && input ? inspectPoint(input, keyboardCursor.time, keyboardCursor.tick) : null;
  const announcement = pin ? describe(pinResult, pin.bubble, tickSize ?? 0.25) : keyboardCursor ? describe(keyboardResult, null, tickSize ?? 0.25) : "";
  const feed = feedBadge(live.feed, now, demoMode, metadataError, tickSize);
  const historyInfo = history ? historyBadge(historyStatus) : null;
  const following = viewport.end === null;

  // Timestamped coverage problems for this session (newest first), no latched warning.
  const gapList = useMemo(() => {
    const items: Array<HeatmapGap & { source: string }> = [];
    if (meta) {
      const from = meta.session.start, to = Math.min(now, meta.historyThrough ?? now);
      let cursor = from;
      for (const [s, e] of meta.coverage) {
        if (s - cursor > 5_000) items.push({ start: cursor, end: s, reason: "not_recorded", source: "Recorder" });
        cursor = Math.max(cursor, e);
      }
      if (meta.coverage.length && to - cursor > 5_000) items.push({ start: cursor, end: to, reason: "not_recorded", source: "Recorder" });
      for (const gap of meta.gaps) if (gap.end - gap.start >= 5_000) items.push({ ...gap, source: "Depth" });
    }
    for (const gap of trades.gaps) {
      const gapEnd = gap.end ?? now;
      if (!meta || !isCovered(meta.coverage, gap.start)) items.push({ start: gap.start, end: gapEnd, reason: gap.reason, source: "Live trades" });
    }
    return items.sort((a, b) => b.start - a.start);
  }, [meta, trades.gaps, now]);
  const shortGaps = (meta?.gaps.filter(gap => gap.end - gap.start < 5_000).length ?? 0);
  const legendTicks = [0, saturation / 2, saturation].map(value => ({ value, left: heatPosition(value, saturation) * 100 }));
  const truncated = tiles.some(tile => tile.tradesTruncated);
  const capped = geometry.current?.bubbleMinVolume ?? tiles.reduce<number | null>((v, tile) => tile.tradesMinVolume ?? v, null);
  const hiddenBelow = capped === null ? null : Math.round(capped);
  const bubbleNote = !settings.showTrades || !(geometry.current?.bubblesCapped || truncated) ? null
    : hiddenBelow !== null && hiddenBelow > settings.minTradeVolume ? `Prints under ${hiddenBelow.toLocaleString()} contracts hidden at this zoom`
    : "Some smaller prints hidden at this zoom";

  return <section className={`topbot-panel liquidity-heatmap${expanded ? " is-expanded" : ""}`} aria-label="Liquidity heatmap">
    <header className="lh-head">
      <div className="lh-title">
        <h2>Liquidity heatmap</h2>
        <span className="lh-symbol" title={contractId ?? undefined}>{symbol?.split(".").at(-1) ?? contractId?.split(".").at(-1) ?? ""}</span>
      </div>
      <div className="lh-badges" aria-live="polite">
        <span className={`lh-badge tone-${feed.tone}`} title={live.feed.detail}><i aria-hidden="true" />{feed.label}</span>
        {!following && <span className="lh-badge tone-paused" title="The display is paused on an earlier window. Data collection continues."><b aria-hidden="true">❚❚</b>Paused · {heatmapTime(end)} ET</span>}
        {historyInfo && <span className={`lh-badge lh-history tone-${historyInfo.tone}`} title={historyInfo.title}>{historyInfo.label}</span>}
      </div>
      <div className="lh-actions">
        <button type="button" className={`lh-live${following ? " is-on" : ""}`} aria-pressed={following} onClick={goLive}
          title={following ? "Following live time" : "Return to the live edge"}><span aria-hidden="true" />{following ? "Live" : "Go live"}</button>
        <button type="button" onClick={doRecenter} disabled={midTick === null} aria-pressed={viewport.priceFollow}
          title={viewport.priceFollow ? "Price auto-recenters when the market leaves the middle of the range" : "Recenter price and resume auto-recentering"}>Recenter</button>
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "Exit full view" : "Expand"}</button>
        <button type="button" aria-expanded={settingsOpen} aria-controls={settingsId} onClick={() => setSettingsOpen(value => !value)}>Settings</button>
      </div>
    </header>

    {settingsOpen && <div id={settingsId} className="lh-settings" role="dialog" aria-label="Heatmap settings"
      onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); setSettingsOpen(false); } }}>
      <div className="lh-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "display"} onClick={() => setTab("display")} autoFocus>Display</button>
        <button type="button" role="tab" aria-selected={tab === "help"} aria-controls={helpId} onClick={() => setTab("help")}>Help</button>
        <button type="button" className="lh-close" aria-label="Close settings" onClick={() => setSettingsOpen(false)}>×</button>
      </div>
      {tab === "display" ? <div className="lh-form">
        <label>Time window<select aria-label="Heatmap time window" value={TIME_SPANS.includes(viewport.span as never) ? viewport.span : ""}
          onChange={event => setSpan(Number(event.target.value))}>
          {!TIME_SPANS.includes(viewport.span as never) && <option value="">{formatSpan(viewport.span)} (custom)</option>}
          {TIME_SPANS.map(span => <option key={span} value={span}>{formatSpan(span)}</option>)}
        </select></label>
        <button type="button" className="lh-session" onClick={wholeSession}>Show whole session</button>
        <label>Price range<select aria-label="Heatmap price range" value={PRICE_RANGES.includes(viewport.rangeTicks as never) ? viewport.rangeTicks : ""}
          onChange={event => setRange(Number(event.target.value))}>
          {!PRICE_RANGES.includes(viewport.rangeTicks as never) && <option value="">{viewport.rangeTicks} ticks (custom)</option>}
          {PRICE_RANGES.map(ticks => <option key={ticks} value={ticks}>{ticks} ticks{tickSize ? ` · ${heatmapPrice(ticks * tickSize, tickSize)} pts` : ""}</option>)}
        </select></label>
        <label className="lh-check"><input type="checkbox" checked={viewport.priceFollow}
          onChange={event => setViewport(view => event.target.checked ? recenter(view, midTick) : { ...view, priceFollow: false })} />Auto-recenter price</label>
        <label>Heat saturates at<select aria-label="Heat saturation threshold" value={String(settings.saturation)}
          onChange={event => setSettings({ saturation: event.target.value === "auto" ? "auto" : Number(event.target.value) })}>
          <option value="auto">Auto ({autoSaturation} contracts)</option>
          {SATURATIONS.map(value => <option key={value} value={value}>{value} contracts</option>)}
        </select></label>
        <label className="lh-check"><input type="checkbox" checked={settings.showTrades} onChange={event => setSettings({ showTrades: event.target.checked })} />Trade bubbles</label>
        <label>Minimum print size<select aria-label="Minimum trade bubble volume" value={settings.minTradeVolume} disabled={!settings.showTrades}
          onChange={event => setSettings({ minTradeVolume: Number(event.target.value) })}>
          {MIN_SIZES.map(value => <option key={value} value={value}>{value} contract{value === 1 ? "" : "s"}</option>)}
        </select></label>
        <label>Bubble size<input aria-label="Trade bubble size" type="range" min="0.5" max="2" step="0.1" value={settings.bubbleScale}
          disabled={!settings.showTrades} onChange={event => setSettings({ bubbleScale: Number(event.target.value) })} /></label>
        <label className="lh-check"><input type="checkbox" checked={settings.profile} onChange={event => setSettings({ profile: event.target.checked })} />Book profile (wide screens)</label>
        <p className="lh-meta">{tickSize ? `${heatmapPrice(tickSize, tickSize)} per tick` : "Tick size pending"} · {tradeStatus}{trades.omitted ? ` · ${trades.omitted} invalid prints omitted` : ""}
          {clock.skewMs !== null && Math.abs(clock.skewMs) > 500 ? ` · Browser clock ${clock.skewMs > 0 ? "ahead" : "behind"} by ${(Math.abs(clock.skewMs) / 1000).toFixed(1)} s (corrected)` : ""}</p>
      </div> : <HeatmapHelp id={helpId} />}
    </div>}

    <div className="lh-chart" ref={chartRef}>
      <canvas ref={baseCanvas} className="lh-canvas" role="img"
        aria-label="Liquidity heatmap: resting size by price over time, with executed trade bubbles. Use the focusable chart below for keyboard inspection." />
      <canvas ref={overlayCanvas} className="lh-overlay" tabIndex={0} role="application" aria-roledescription="interactive heatmap"
        aria-describedby={helpId + "-keys"}
        aria-label={`Heatmap navigation, ${formatSpan(viewport.span)} window ${following ? "following live" : `ending ${heatmapTime(end)} ET`}`}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
        onPointerCancel={event => { pointers.current.delete(event.pointerId); drag.current = null; pinch.current = null; }}
        onPointerLeave={() => { if (!drag.current) setHover(null); }} onKeyDown={onKeyDown} onBlur={() => setKeyboardCursor(null)} />
      <span id={helpId + "-keys"} className="sr-only">Arrow keys move the inspection cursor; Shift with arrows pans; plus and minus zoom time; Enter pins details; L returns live; C recenters price; Home jumps to the session open.</span>
      {!ready && tickSize && <div className="lh-empty">{demoMode ? "Demo mode · live depth is off" : live.feed.state === "loading" ? "Waiting for observed depth…" : feed.label}</div>}
      {hover?.result && <div className="lh-tooltip" style={{ left: Math.min(size.width - 250, hover.x + 14), top: Math.max(4, hover.y - 44) }}>
        {describe(hover.result, hover.bubble, tickSize ?? 0.25)}</div>}
      {pin && pinResult && <div className="lh-pin" role="status">
        <span>{describe(pinResult, pin.bubble, tickSize ?? 0.25)}</span>
        {(pin.time < start || pin.time > end) && <button type="button" onClick={() => jumpTo(pin.time)}>Show</button>}
        <button type="button" aria-label="Clear pinned details" onClick={() => setPin(null)}>×</button>
      </div>}
      <span className="sr-only" aria-live="polite">{announcement}</span>
    </div>

    <HeatmapNavigator sessionStart={meta?.session.start ?? sessionStart} sessionEnd={meta?.session.end ?? sessionStart + 23 * 3600_000}
      now={now} coverage={meta?.coverage ?? []} gaps={meta?.gaps ?? []} summary={historyStatus?.summary ?? null}
      liveFrames={live.frames} viewStart={start} viewEnd={end} following={following}
      onJump={time => jumpTo(time)} />

    <footer className="lh-foot">
      <div className="lh-legend" aria-label="Legend">
        <span className="lh-scale" title={`Linear color scale in contracts; sizes at or above ${saturation} use the top color.`}>
          <span className="lh-scale-label">Resting size</span>
          <span className="lh-scale-bar" style={{ background: HEAT_GRADIENT_CSS }}>
            {legendTicks.map(tick => <i key={tick.left} style={{ left: `${tick.left}%` }} />)}
          </span>
          <span className="lh-scale-ticks">{legendTicks.map((tick, i) => <span key={i} style={{ left: `${tick.left}%` }}>
            {i === 2 ? `≥${saturation}` : +tick.value.toFixed(tick.value < 10 ? 1 : 0)}</span>)}</span>
        </span>
        <span><i className="lh-line bid" />Bid</span><span><i className="lh-line ask" />Ask</span>
        {settings.showTrades && <><span><i className="lh-dot buy" />Buy</span><span><i className="lh-dot sell" />Sell</span><span><i className="lh-dot unknown" />Unknown</span></>}
        <span><i className="lh-swatch gap" />Gap</span><span><i className="lh-swatch none" />Not recorded</span>
      </div>
      <div className="lh-coverage">
        {bubbleNote && <span className="lh-note">{bubbleNote}</span>}
        <button type="button" className="lh-link" aria-expanded={gapsOpen} aria-controls={gapsId} onClick={() => setGapsOpen(value => !value)}>
          {gapList.length ? `${gapList.length} coverage gap${gapList.length === 1 ? "" : "s"}` : "No coverage gaps"}{shortGaps ? ` · ${shortGaps} brief` : ""}
        </button>
      </div>
    </footer>
    {gapsOpen && <ul id={gapsId} className="lh-gaps">
      {gapList.slice(0, 60).map(gap => <li key={`${gap.source}:${gap.start}:${gap.reason}`}>
        <button type="button" onClick={() => { jumpTo((gap.start + gap.end) / 2, Math.min(Math.max(viewport.span, (gap.end - gap.start) * 3), 4 * 3600_000)); }}>
          <time>{heatmapTime(gap.start)}–{heatmapTime(gap.end)} ET</time>
          <span>{gap.source} · {gapLabel(gap.reason)} · {formatSpan(Math.max(1000, gap.end - gap.start))}</span>
        </button>
      </li>)}
      {gapList.length > 60 && <li className="lh-meta">{gapList.length - 60} older gaps not listed</li>}
      {!gapList.length && <li className="lh-meta">{meta ? "No recorder outages or depth interruptions of 5 s or more this session." : "Session coverage appears once history loads."}</li>}
      {shortGaps > 0 && <li className="lh-meta">{shortGaps} brief depth interruption{shortGaps === 1 ? "" : "s"} under 5 s are marked on the chart (amber) but not listed.</li>}
      {meta?.notes.some(([, kind]) => kind === "book_rebuilt_mid_capture") && <li className="lh-meta">The recorded book was rebuilt mid-capture at {meta.notes.filter(([, k]) => k === "book_rebuilt_mid_capture").map(([at]) => heatmapTime(at)).join(", ")} ET; levels unchanged since then may be missing (shown blank).</li>}
    </ul>}
    {metadataError && <button type="button" className="lh-retry" onClick={retry}>Retry tick-size lookup</button>}
  </section>;
}

function HeatmapHelp({ id }: { id: string }) {
  return <div id={id} className="lh-help" role="tabpanel">
    <h3>Reading the map</h3>
    <ul>
      <li><b>Heat</b> is resting limit-order size at each price (nearest 80 observed levels per side). Color is linear in contracts up to the saturation threshold shown in the legend; larger sizes share the top color. Zoomed out, a cell is the mean of the observed 500 ms samples in its 5 s or 60 s bucket.</li>
      <li><b>Lines</b> are best bid (green) and best ask (pink). <b>Bubbles</b> are executed prints grouped by price and provider-reported side (250 ms, or the bucket when zoomed out); area grows with volume. The side label is ProjectX&apos;s, not a verified exchange aggressor flag.</li>
      <li><b>Blank</b> means not observed — never zero. <b>Amber</b> bands are interrupted depth (disconnect, reset, stale or crossed book). <b>Hatched gray</b> means nothing was recorded; <b>hatched blue</b> at the right edge is recorder data not yet sealed (about once a minute).</li>
    </ul>
    <h3>History</h3>
    <p>Session history comes from the independent depth recorder, which runs whether or not this page is open. It starts at 6 p.m. New York and never extends before recording began. Gaps are listed with times under the chart. Depth is an observed book; full-exchange completeness is not certified. Nothing here changes TopBot&apos;s strategy or orders.</p>
    <h3>Controls</h3>
    <ul>
      <li><b>Live</b> follows the current time; dragging, zooming or picking a moment pauses the view (data keeps arriving). <b>Recenter</b> re-centers price and resumes auto-recentering, which only moves the range when price leaves its middle.</li>
      <li>Drag to pan (touch: horizontal). Ctrl/⌘ + scroll or pinch zooms time; add Shift for price. In full view, plain scroll zooms. Click or tap to pin details.</li>
      <li>Keyboard (focus the chart): arrows move the cursor, Shift + arrows pan, + / − zoom, Enter pins, L live, C recenter, Home session open, Esc clears.</li>
    </ul>
  </div>;
}
