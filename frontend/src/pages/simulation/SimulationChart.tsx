import { memo, useEffect, useLayoutEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type PointerEvent } from 'react';
import type { SimulationCandle, Execution, Instrument } from './simulationModel';
import { simulationTime } from './funded/model';
import { anchorView, bound, defaultView, MAX_SPACING, MIN_SPACING, viewStart, wheelPixels, zoomView, type ChartView } from './simulationViewport';
import { movingAverage, retainedVwap } from './simulationIndicators';
import { readPreference, writePreference } from './simulationWorkspace';
import { createChartCursor, inspectCursor, type ChartPoint as Point } from './simulationChartCursor';
import { SimulationChartReadout, SimulationCrosshair } from './SimulationChartInspection';
import { fitPriceRange, formatChartPrice, panPriceView, priceScale, scalePriceView } from './simulationPriceScale';

const money = formatChartPrice;
const directionClass = (bar: SimulationCandle) => bar.close > bar.open ? 'simulation-up' : bar.close < bar.open ? 'simulation-down' : 'simulation-neutral';
type Mark = { time: number; price: number };
type Indicators = { sma: boolean; vwap: boolean; lookback: number };
const validIndicators = (v: unknown): v is Indicators => !!v && typeof v === 'object' && typeof (v as Indicators).sma === 'boolean'
  && typeof (v as Indicators).vwap === 'boolean' && Number.isInteger((v as Indicators).lookback) && (v as Indicators).lookback >= 2 && (v as Indicators).lookback <= 200;
type Drag = { id: number; point: Point; view: ChartView; mode: 'pan' | 'price' | 'time' };
const CandleGlyph = memo(function CandleGlyph({ bar, px, bodyWidth, wickWidth, minimum, maximum, log, top, bottom, clip }: {
  bar: SimulationCandle; px: number; bodyWidth: number; wickWidth: number; minimum: number; maximum: number; log?: boolean; top: number; bottom: number; clip: string;
}) {
  const { y, valid } = priceScale({ minimum, maximum, log }, top, bottom);
  if (![bar.open, bar.close, bar.low, bar.high].every(valid)) return null;
  return <g className={directionClass(bar)} clipPath={clip}>
    <rect className="simulation-candle-shape" x={px - wickWidth / 2} y={y(bar.high)} width={wickWidth} height={Math.max(1, y(bar.low) - y(bar.high))} fill="currentColor" />
    <rect className="simulation-candle-body" data-candle={bar.time} data-display-close={bar.close} x={px - bodyWidth / 2} y={Math.min(y(bar.open), y(bar.close))} width={bodyWidth} height={Math.max(1.5, Math.abs(y(bar.close) - y(bar.open)))} fill="currentColor" />
  </g>;
});
// At sub-pixel candle widths, a few paths represent the same rectangles without
// thousands of DOM nodes and component profiling entries on every price update.
function denseCandlePaths(bars: SimulationCandle[], start: number, spacing: number, left: number, width: number, wick: number, minimum: number, maximum: number, top: number, bottom: number, log?: boolean) {
  const paths: Record<string, string> = {};
  const { y, valid } = priceScale({ minimum, maximum, log }, top, bottom);
  const rect = (x: number, y: number, w: number, h: number) => `M${x},${y}h${w}v${h}h${-w}Z`;
  bars.forEach((bar, i) => {
    if (![bar.open, bar.close, bar.low, bar.high].every(valid)) return;
    const color = directionClass(bar), x = left + (i - start + .5) * spacing;
    paths[color] = (paths[color] ?? '') + rect(x - wick / 2, y(bar.high), wick, Math.max(1, y(bar.low) - y(bar.high)))
      + rect(x - width / 2, Math.min(y(bar.open), y(bar.close)), width, Math.max(1.5, Math.abs(y(bar.close) - y(bar.open))));
  });
  return paths;
}
const emptyCandles: SimulationCandle[] = [], emptyTrades: Execution[] = [];
const subscribeStatic = () => () => {};
const staticSnapshot = () => null;
interface ChartSource {
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => { session: { market: { books: Record<Instrument, { candles: Record<string, SimulationCandle[]>; last: number | null }>; trades: Execution[]; time: number } | null } };
}
const validView = (v: unknown): v is ChartView => {
  if (!v || typeof v !== 'object') return false;
  const p = v as ChartView;
  return [p.time, p.offset, p.minimum, p.maximum, p.spacing].every(Number.isFinite) && p.time >= 0 && Math.abs(p.offset) < 10000
    && p.spacing >= MIN_SPACING && p.spacing <= MAX_SPACING && p.maximum > p.minimum && typeof p.follow === 'boolean' && typeof p.auto === 'boolean'
    && (p.log === undefined || typeof p.log === 'boolean') && (!p.log || p.minimum > 0);
};
export const SimulationChart = memo(function SimulationChart({ candles: suppliedCandles = emptyCandles, runtime, symbol = 'BX', tickSize = .25, lastPrice: suppliedPrice, timeframe = '1m', seconds = 60, trades: suppliedTrades = emptyTrades, height: minimumHeight = 440, fitContainer = false, jump, elapsedTime: suppliedTime }: {
  candles?: SimulationCandle[]; runtime?: ChartSource; symbol?: Instrument; tickSize?: number; lastPrice?: number; timeframe?: string;
  seconds?: number; trades?: Execution[]; height?: number; fitContainer?: boolean; jump?: { time: number; id: number; label?: string }; elapsedTime?: number;
}) {
  // Subscribe to the same immutable publication as the workspace. Passing large
  // histories as changing props makes React's development tooling serialize them.
  const snapshot = useSyncExternalStore(runtime?.subscribe ?? subscribeStatic, runtime?.getSnapshot ?? staticSnapshot);
  const market = snapshot?.session.market, selectedBook = market?.books[symbol];
  const candles = selectedBook?.candles[timeframe as keyof typeof selectedBook.candles] ?? suppliedCandles;
  const trades = market?.trades ?? suppliedTrades, elapsedTime = market?.time ?? suppliedTime;
  const lastPrice = selectedBook?.last == null ? suppliedPrice : selectedBook.last * tickSize;
  const container = useRef<HTMLDivElement>(null), plot = useRef<HTMLDivElement>(null), svg = useRef<SVGSVGElement>(null);
  const key = `chart.${symbol}.${timeframe}`;
  const [size, setSize] = useState({ width: 960, height: minimumHeight });
  const width = size.width, height = fitContainer ? size.height : Math.max(minimumHeight, size.height);
  const [view, setView] = useState(() => readPreference(key, defaultView(), validView));
  const [cursor] = useState(createChartCursor);
  const [dragging, setDragging] = useState(false);
  const [measuring, setMeasuring] = useState(false);
  const [marks, setMarks] = useState<Mark[]>([]);
  const [guide, setGuide] = useState(false);
  const [jumpText, setJumpText] = useState('');
  const [jumpNotice, setJumpNotice] = useState('');
  const [indicators, setIndicators] = useState(() => readPreference(`${key}.indicators`,
    readPreference('indicators', { sma: false, vwap: false, lookback: 20 }, validIndicators), validIndicators));
  const drag = useRef<Drag | null>(null), touches = useRef(new Map<number, Point>());
  const pinch = useRef<{ distance: number; center: Point; view: ChartView } | null>(null);
  const clipId = useId();
  const hasCandles = candles.length > 0;
  useEffect(() => {
    const target = plot.current ?? container.current;
    if (!target) return;
    // CSS allocates the extra column height to the plot. Measure that slot,
    // rather than letting the SVG's viewBox contribute an intrinsic height.
    const observer = new ResizeObserver(([entry]) => {
      const next = { width: Math.max(240, entry.contentRect.width), height: Math.max(fitContainer ? 160 : minimumHeight, hasCandles ? entry.contentRect.height || minimumHeight : minimumHeight) };
      setSize(current => current.width === next.width && current.height === next.height ? current : next);
    });
    observer.observe(target); return () => observer.disconnect();
  }, [minimumHeight, hasCandles, fitContainer]);
  // Persist once a gesture settles, and flush the last view on navigation.
  useEffect(() => {
    const save = () => writePreference(key, view);
    const timer = window.setTimeout(save, 200);
    window.addEventListener('pagehide', save);
    return () => { window.clearTimeout(timer); window.removeEventListener('pagehide', save); };
  }, [key, view]);
  const pendingPreference = useRef({ key, view });
  useEffect(() => { pendingPreference.current = { key, view }; }, [key, view]);
  useEffect(() => () => { const latest = pendingPreference.current; writePreference(latest.key, latest.view); }, []);
  useEffect(() => { writePreference(`${key}.indicators`, indicators); }, [key, indicators]);
  const plotLeft = 14, plotRight = width - 84, plotTop = 24, plotBottom = height - 33;
  const plotWidth = Math.max(1, plotRight - plotLeft);
  const start = viewStart(view, candles, plotWidth), spacing = view.spacing;
  const firstVisible = Math.max(0, Math.floor(start));
  const visible = useMemo(() => candles.slice(firstVisible, Math.max(firstVisible + 1, Math.ceil(start + plotWidth / spacing))), [candles, firstVisible, start, plotWidth, spacing]);
  const latest = candles.at(-1), last = visible.at(-1) ?? latest;
  const fitted = useMemo(() => fitPriceRange(visible, tickSize, view.log, lastPrice), [visible, tickSize, view.log, lastPrice]);
  const minimum = view.auto ? fitted.minimum : view.minimum, maximum = view.auto ? fitted.maximum : view.maximum;
  const materialized = { ...view, minimum, maximum };
  const xIndex = (index: number) => plotLeft + (index - start + .5) * spacing;
  const x = (index: number) => xIndex(firstVisible + index);
  const { y, price: priceAt, valid: validPrice } = priceScale({ minimum, maximum, log: view.log }, plotTop, plotBottom);
  const toggleLog = () => {
    const log = !view.log;
    setView({ ...materialized, ...(log && minimum <= 0 ? fitPriceRange(visible, tickSize, true, lastPrice) : {}), log });
  };
  const indexAt = (px: number) => Math.floor((px - plotLeft) / spacing + start);
  const clearSelection = cursor.clear;
  const locate = (clientX: number, clientY: number): Point | null => {
    const bounds = svg.current?.getBoundingClientRect();
    if (!bounds) return null;
    const scale = Math.min(bounds.width / width, bounds.height / height);
    if (scale <= 0) return null;
    return { x: (clientX - bounds.left - (bounds.width - width * scale) / 2) / scale,
      y: (clientY - bounds.top - (bounds.height - height * scale) / 2) / scale };
  };
  const zoom = (factor: number, px = plotWidth / 2) => {
    setView(zoomView(materialized, candles, plotWidth, bound(px, 0, plotWidth), factor));
  };
  // React's delegated wheel listener can be passive. Own this listener so the chart
  // consumes both trackpad and wheel scrolling; outside the SVG the page scrolls normally.
  const onWheel = useRef<((event: WheelEvent) => void) | null>(null);
  useLayoutEffect(() => { onWheel.current = (event: WheelEvent) => {
    const point = locate(event.clientX, event.clientY);
    if (!point || point.x < 0 || point.x > width || point.y < 0 || point.y > height) return;
    event.preventDefault();
    const factor = Math.exp(-wheelPixels(event.deltaY, event.deltaMode, height) * .0025);
    if (point.x > plotRight) setView(scalePriceView(materialized, 1 / factor, tickSize, bound((plotBottom - point.y) / (plotBottom - plotTop), 0, 1)));
    else if (candles.length) zoom(factor, point.x - plotLeft);
  }; });
  useEffect(() => {
    const target = svg.current;
    if (!target) return;
    const wheel = (event: WheelEvent) => onWheel.current?.(event);
    target.addEventListener('wheel', wheel, { passive: false });
    return () => target.removeEventListener('wheel', wheel);
  }, [hasCandles]);
  useEffect(() => {
    if (!jump || !candles.length) return;
    const found = candles.findIndex(b => b.time + seconds > jump.time), index = found < 0 ? candles.length - 1 : found;
    setView(current => anchorView(current, candles, index - plotWidth / current.spacing / 2, plotWidth));
    cursor.inspect(candles[index].time);
    // A jump is a navigation command, not a live-state subscription.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump]);
  const sma = useMemo(() => indicators.sma ? movingAverage(candles, indicators.lookback) : [], [candles, indicators.lookback, indicators.sma]);
  const vwap = useMemo(() => indicators.vwap ? retainedVwap(candles, trades, symbol, tickSize, seconds) : [], [candles, trades, symbol, tickSize, seconds, indicators.vwap]);
  const follow = () => { setView({ ...materialized, follow: true }); clearSelection(); };
  const reset = () => { setView(defaultView()); clearSelection(); setMarks([]); setMeasuring(false); };
  const fit = () => { setView(anchorView({ ...materialized, spacing: bound(plotWidth / Math.max(1, candles.length), MIN_SPACING, MAX_SPACING), auto: true }, candles, 0, plotWidth)); clearSelection(); };
  const finish = (event: PointerEvent<SVGSVGElement>) => {
    touches.current.delete(event.pointerId); pinch.current = null;
    if (drag.current?.id === event.pointerId || touches.current.size < 2) { drag.current = null; setDragging(false); }
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    // Remaining touch must lift before a new gesture; cancellation never leaves a stuck drag.
  };
  const addMark = (mark: Mark) => setMarks(previous => previous.length === 1 ? [...previous, mark] : [mark]);
  const measureFromPointer = (point: Point) => { const bar = candles[bound(indexAt(point.x), 0, candles.length - 1)]; if (bar) addMark({ time: bar.time, price: Math.round(priceAt(bound(point.y, plotTop, plotBottom)) / tickSize) * tickSize }); };
  const scalePrice = (factor: number, held = materialized) => scalePriceView(held, factor, tickSize);
  const candleWidth = Math.max(.4, spacing * .72), wickWidth = bound(spacing / 12, .5, 2);
  const glyphs = useMemo(() => spacing < 3
    ? Object.entries(denseCandlePaths(visible, start - firstVisible, spacing, plotLeft, candleWidth, wickWidth, minimum, maximum, plotTop, plotBottom, view.log)).map(([color, path]) =>
      <path key={color} className={`simulation-dense-candles ${color}`} d={path} fill="currentColor" clipPath={`url(#${clipId}-price)`} />)
    : visible.map((bar, i) => <CandleGlyph key={bar.time} bar={bar} px={plotLeft + (firstVisible + i - start + .5) * spacing} bodyWidth={candleWidth} wickWidth={wickWidth} minimum={minimum} maximum={maximum} log={view.log} top={plotTop} bottom={plotBottom} clip={`url(#${clipId}-price)`} />),
  [visible, start, firstVisible, spacing, candleWidth, wickWidth, minimum, maximum, view.log, plotBottom, clipId]);
  if (!latest || !last) return <div className="simulation-chart-wrap simulation-waiting" ref={container} role="status">Waiting for the first executed trade…</div>;
  const inspectionView = { visible, last, firstVisible, fallbackIndex: visible.length ? firstVisible + visible.length - 1 : candles.length - 1,
    start, spacing, minimum, maximum, log: view.log, plotLeft, plotRight, plotTop, plotBottom, height };
  const displayClose = lastPrice ?? latest.close, liveClass = directionClass(latest);
  const markerY = bound(y(displayClose), plotTop, plotBottom);
  const markerLabelOffset = bound(markerY, plotTop + 11, plotBottom - 11) - markerY;
  const logUnavailable = !!view.log && !visible.some(bar => [bar.open, bar.high, bar.low, bar.close].every(validPrice));
  const labelEvery = Math.max(1, Math.ceil(100 / spacing));
  const change = marks.length === 2 ? marks[1].price - marks[0].price : 0;
  const marksRetained = marks.every(mark => candles.some(bar => bar.time === mark.time));
  const measurement = !marksRetained ? 'An endpoint is no longer retained. Select two new points.' : marks.length === 2 ? `${change >= 0 ? '+' : ''}${money(change)} · ${marks[0].price ? (change / marks[0].price * 100).toFixed(2) : '—'}% · ${Math.round(change / tickSize)} ticks · ${Math.abs(candles.findIndex(b => b.time === marks[1].time) - candles.findIndex(b => b.time === marks[0].time)) + 1} candles (inclusive) · ${Math.abs(marks[1].time - marks[0].time)} simulated seconds` : 'Select two points; prices snap to ticks.';
  const path = (values: (number | null)[]) => visible.map((_, i) => {
    const index = firstVisible + i, value = values[index];
    return value == null || !validPrice(value) ? '' : `${i === 0 || values[index - 1] == null || !validPrice(values[index - 1]!) ? 'M' : 'L'}${x(i)},${y(value)}`;
  }).join(' ');
  return <div className="simulation-chart-wrap" ref={container}>
    <div className="simulation-chart-navigation" aria-label="Chart navigation">
      <button aria-label="Zoom In" onClick={() => zoom(1.25)} title="Zoom in (+)">{fitContainer ? '+' : 'Zoom In'}</button><button aria-label="Zoom Out" onClick={() => zoom(.8)} title="Zoom out (−)">{fitContainer ? '−' : 'Zoom Out'}</button>
      <button aria-pressed={view.follow} onClick={follow} title="Home · preserves zoom">Follow Latest</button>
      <details className={fitContainer ? 'simulation-chart-tool-menu' : 'simulation-chart-tool-menu is-inline'} open={fitContainer ? undefined : true}
        onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); e.currentTarget.open = false; e.currentTarget.querySelector('summary')?.focus(); } }}>
        <summary>Chart tools</summary>
        <div className="simulation-chart-tool-content">
          <div className="simulation-activity-controls"><button onClick={fit} title="F">Fit History</button><button onClick={reset} title="R">Reset View</button>
            <button aria-pressed={measuring} onClick={() => { setMeasuring(!measuring); setMarks([]); }} title="M">Measure</button>
            <button aria-expanded={guide} onClick={() => setGuide(!guide)}>Shortcuts</button>
          </div>
          {guide && <p className="simulation-chart-help">Wheel / pinch: zoom at pointer · Drag: pan · Drag right price axis: price scale · Drag bottom time axis: time scale · + / −: zoom · Shift + / −: price scale · Page Up / Down: pan · ← / →: inspect · ↑ / ↓: price pan · Home: latest (keeps zoom) · F: fit · R / double-click plot: reset · Double-click price axis: auto price · A: auto price · L: logarithmic price · M then Enter at two inspected candles: measure · Escape: clear.</p>}
          <div className="simulation-chart-options">
            <label><input type="checkbox" checked={indicators.sma} onChange={e => setIndicators({ ...indicators, sma: e.target.checked })} /> <span className="simulation-sma-legend">SMA</span></label>
            <label>Lookback <select aria-label="SMA lookback" value={indicators.lookback} onChange={e => setIndicators({ ...indicators, lookback: Number(e.target.value) })}>{[5, 10, 20, 50, 100, 200].map(n => <option key={n}>{n}</option>)}</select></label>
            <label><input type="checkbox" checked={indicators.vwap} onChange={e => setIndicators({ ...indicators, vwap: e.target.checked })} /> <span className="simulation-vwap-legend">Retained-tape VWAP</span></label>
            <form onSubmit={e => { e.preventDefault(); const time = Number(jumpText), end = elapsedTime ?? latest.time + seconds - .25; if (!jumpText.trim() || !Number.isFinite(time) || time < candles[0].time || time > end) { setJumpNotice(`Use retained seconds ${candles[0].time}–${end}.`); return; }
              const found = candles.findIndex(b => b.time + seconds > time), index = found < 0 ? candles.length - 1 : found; setView(anchorView(materialized, candles, index - plotWidth / spacing / 2, plotWidth)); cursor.inspect(candles[index].time); setJumpNotice(`Inspecting ${simulationTime(time)}; engine time unchanged.${time < candles[index].time || time >= candles[index].time + seconds ? ' Empty window: nearest retained candle shown.' : ''}`); }}>
              <input aria-label="Jump to simulated seconds" placeholder="Sim seconds" value={jumpText} onChange={e => setJumpText(e.target.value)} inputMode="decimal" /><button>Jump</button>
            </form>
          </div>
          {(indicators.sma || indicators.vwap) && <p className="simulation-chart-help">{indicators.sma && `SMA needs ${indicators.lookback} actual candles; unavailable before that. `}{indicators.vwap && `VWAP uses only retained ${symbol} executions${trades.find(t => t.contract === symbol) ? ' from ' + simulationTime(trades.find(t => t.contract === symbol)!.time) : ' (none available)'}, not full-session volume.`}</p>}
          <button className="simulation-close-chart-tools" onClick={e => { const menu = e.currentTarget.closest('details'); if (menu) { menu.open = false; menu.querySelector('summary')?.focus(); } }}>Close tools</button>
        </div>
      </details>
    </div>
    {jumpNotice && <p className="simulation-chart-help">{jumpNotice}</p>}
    {jump && <p className="simulation-event-context">{simulationTime(jump.time)} · {jump.label ?? 'Selected event'} · chart inspection only</p>}
    <SimulationChartReadout cursor={cursor} view={inspectionView} sma={indicators.sma ? sma : undefined} vwap={indicators.vwap ? vwap : undefined} />
    <div className="simulation-view-status"><span>{view.follow ? 'Following latest' : 'Browsing history'} · {view.auto ? 'Auto price' : 'Manual price'}</span><span>Earliest retained: {simulationTime(candles[0].time)}</span></div>
    {measuring && <p className="simulation-measurement" aria-live="polite">Measure: {measurement}</p>}
    <div ref={plot} className="simulation-chart-plot" style={{ minHeight: fitContainer ? undefined : minimumHeight }}>
    <svg ref={svg} className={`simulation-chart${dragging ? ' is-dragging' : ''}`} viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
      data-spacing={spacing} data-start={start} data-price-scale={view.log ? 'log' : 'linear'} data-minimum={minimum} data-maximum={maximum} data-rendered-candles={visible.length} aria-label={`Simulated ${symbol} candlestick chart. Last trade price $${money(displayClose)}. Wheel to zoom; drag to pan. Shortcuts available above.`}
      onPointerDown={event => {
        if (event.button !== 0 || (event.isPrimary === false && event.pointerType !== 'touch')) return;
        const point = locate(event.clientX, event.clientY); if (!point || point.x < plotLeft || point.y < plotTop) return;
        event.preventDefault(); event.currentTarget.focus({ preventScroll: true }); event.currentTarget.setPointerCapture?.(event.pointerId);
        if (event.pointerType === 'touch') {
          touches.current.set(event.pointerId, point);
          if (touches.current.size === 2) { const [a, b] = [...touches.current.values()]; pinch.current = { distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, view: materialized }; drag.current = null; return; }
          if (touches.current.size > 2) return;
        }
        if (measuring && point.x <= plotRight && point.y <= plotBottom) { measureFromPointer(point); return; }
        drag.current = { id: event.pointerId, point, view: anchorView(materialized, candles, start, plotWidth), mode: point.x > plotRight ? 'price' : point.y > plotBottom ? 'time' : 'pan' };
        setDragging(true); clearSelection();
      }}
      onPointerMove={event => {
        const point = locate(event.clientX, event.clientY); if (!point) return;
        if (touches.current.has(event.pointerId)) touches.current.set(event.pointerId, point);
        if (pinch.current && touches.current.size === 2) {
          const [a, b] = [...touches.current.values()], held = pinch.current, center = (a.x + b.x) / 2;
          const next = zoomView(held.view, candles, plotWidth, held.center.x - plotLeft, Math.hypot(a.x - b.x, a.y - b.y) / held.distance);
          setView(anchorView(next, candles, viewStart(next, candles, plotWidth) - (center - held.center.x) / next.spacing, plotWidth)); return;
        }
        const held = drag.current;
        if (held) {
          if (held.id !== event.pointerId) return;
          if (held.mode === 'price') { setView({ ...scalePrice(Math.exp((point.y - held.point.y) * .008), held.view), follow: view.follow }); return; }
          if (held.mode === 'time') { setView(zoomView(held.view, candles, plotWidth, held.point.x - plotLeft, Math.exp((point.x - held.point.x) * .008))); return; }
          setView(anchorView(panPriceView(held.view, (point.y - held.point.y) / (plotBottom - plotTop)), candles,
            viewStart(held.view, candles, plotWidth) - (point.x - held.point.x) / held.view.spacing, plotWidth)); return;
        }
        if (point.x >= plotLeft && point.x <= plotRight && point.y >= plotTop && point.y <= plotBottom) cursor.move(point); else clearSelection();
      }} onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={finish}
      onPointerLeave={() => { if (!drag.current && !pinch.current) clearSelection(); }} onBlur={clearSelection} onDoubleClick={event => {
        const point = locate(event.clientX, event.clientY);
        if (point && point.x > plotRight) setView({ ...materialized, auto: true }); else reset();
      }}
      onKeyDown={event => {
        const key = event.key.toLowerCase();
        if (!['escape', 'home', 'pageup', 'pagedown', 'arrowleft', 'arrowright', 'arrowup', 'arrowdown', '+', '=', '-', 'f', 'r', 'a', 'l', 'm', 'enter'].includes(key)) return;
        event.preventDefault();
        const { index: selectedIndex, selected } = inspectCursor(cursor.getSnapshot(), inspectionView);
        if (key === 'escape') { clearSelection(); setMarks([]); setMeasuring(false); touches.current.clear(); pinch.current = null; drag.current = null; setDragging(false); }
        if (key === 'home') follow(); if (key === 'r') reset(); if (key === 'f') fit();
        if (key === 'a') setView({ ...materialized, auto: !view.auto });
        if (key === 'l') toggleLog();
        if (key === 'm') { setMeasuring(!measuring); setMarks([]); }
        if (key === 'enter' && measuring) addMark({ time: selected.time, price: selected.close });
        if (['+', '=', '-'].includes(key)) { const factor = key === '-' ? .8 : 1.25; if (event.shiftKey) setView(scalePrice(1 / factor)); else zoom(factor); }
        if (key === 'arrowup' || key === 'arrowdown') setView(panPriceView(materialized, key === 'arrowup' ? .1 : -.1));
        if (key === 'pageup' || key === 'pagedown') { setView(anchorView(materialized, candles, start + (key === 'pageup' ? -1 : 1) * plotWidth / spacing * .8, plotWidth)); clearSelection(); }
        if (key === 'arrowleft' || key === 'arrowright') { const current = bound(!cursor.getSnapshot().pointer && selectedIndex < 0 ? visible.length - 1 : selectedIndex, 0, visible.length - 1); const index = bound(current + (key === 'arrowleft' ? -1 : 1), 0, visible.length - 1); cursor.inspect(visible[index].time); }
      }}>
      <title>{symbol} trade prices</title>
      <defs><clipPath id={`${clipId}-price`}><rect x={plotLeft} y={plotTop} width={plotWidth} height={plotBottom - plotTop} /></clipPath></defs>
      {Array.from({ length: Math.max(3, Math.floor((plotBottom - plotTop) / 60)) + 1 }, (_, index) => { const py = plotTop + index / Math.max(3, Math.floor((plotBottom - plotTop) / 60)) * (plotBottom - plotTop); return <g key={index} className="simulation-grid"><line x1={plotLeft} x2={plotRight} y1={py} y2={py} /><text x={plotRight + 8} y={py + 4}>{!logUnavailable && money(priceAt(py))}</text></g>; })}
      {visible.map((bar, i) => (firstVisible + i) % labelEvery === 0 && x(i) >= plotLeft && x(i) <= plotRight - 75 && <g key={bar.time} className="simulation-grid"><line x1={x(i)} x2={x(i)} y1={plotTop} y2={plotBottom} /><text x={x(i)} y={height - 10}>{simulationTime(bar.time)}</text></g>)}
      <text x={plotLeft + 8} y={plotTop + 30} className="simulation-watermark">{symbol} / SIM</text>
      {glyphs}
      <g clipPath={`url(#${clipId}-price)`}>{indicators.sma && <path className="simulation-sma-line" d={path(sma)} />}{indicators.vwap && <path className="simulation-vwap-line" d={path(vwap)} />}
        {measuring && marksRetained && marks.length === 2 && marks.every(mark => validPrice(mark.price)) && <line className="simulation-measure-line" x1={xIndex(candles.findIndex(b => b.time === marks[0].time))} y1={y(marks[0].price)} x2={xIndex(candles.findIndex(b => b.time === marks[1].time))} y2={y(marks[1].price)} />}
      </g>
      {validPrice(displayClose) && <g className={`simulation-price-marker ${liveClass}`} style={{ transform: `translateY(${markerY}px)` }}>
        <title>Last executed price ${money(displayClose)}.</title>
        {y(displayClose) >= plotTop && y(displayClose) <= plotBottom && <line x1={plotLeft} x2={plotRight} y1={0} y2={0} stroke="currentColor" strokeDasharray="4 4" opacity={.6} />}
        <rect x={plotRight + 3} y={markerLabelOffset - 11} width={78} height={22} rx={4} fill="currentColor" /><text x={plotRight + 42} y={markerLabelOffset + 4} textAnchor="middle" className="simulation-price-label">{displayClose > maximum ? '↑ ' : displayClose < minimum ? '↓ ' : ''}{money(displayClose)}</text>
      </g>
      }
      <rect x={plotRight} y={plotTop} width={84} height={plotBottom - plotTop} fill="transparent" style={{ cursor: 'ns-resize' }}><title>Drag to scale price; double-click to auto fit</title></rect>
      <rect x={plotLeft} y={plotBottom + 1} width={plotWidth} height={32} fill="transparent" style={{ cursor: 'ew-resize' }}><title>Drag to scale time</title></rect>
      {!logUnavailable && <SimulationCrosshair cursor={cursor} view={inspectionView} />}
    </svg>
    {view.log && visible.some(bar => bar.low <= 0) && <span className="simulation-log-notice">Log scale omits candles at or below zero.</span>}
    <div className="simulation-axis-controls" aria-label="Price scale controls">
      <button aria-label="Auto Price Scale" aria-pressed={view.auto} onClick={() => setView({ ...materialized, auto: !view.auto })} title="Auto price scale (A) · fit visible candles">A</button>
      <button aria-label="Logarithmic Price Scale" aria-pressed={!!view.log} onClick={toggleLog} title="Logarithmic price scale (L) · equal percentage moves">L</button>
    </div>
    </div>
  </div>;
});
