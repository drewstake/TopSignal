import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { Button } from "../../components/ui/Button";
import { requestJson } from "../../lib/api";
import { APP_SCALE_CHANGED_EVENT, getAppScale } from "../../lib/appScale";
import { APP_THEME_CHANGED_EVENT } from "../../lib/theme";
import { readBotChartThemeColors } from "../bot/botChartTheme";
import { candleRequestWindow, lastCandleInRange, logicalToTime, timeToLogical, mergeHistoryDetail, type ChartWindow, type HistoricalCandle, type HistoryRange } from "./historyChartData";

const priceFormatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const price = (value: number) => priceFormatter.format(value);
const timeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC", month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
});
const timestamp = (time: number) => timeFormatter.format(new Date(time * 1000));

const timeframeSeconds = { "1m": 60, "5m": 300, "4h": 14400, "1d": 86400 };

export function DatabentoChart({ history, initialView = "candles", initialAnchor, onViewportChange }: {
  history: ChartWindow; initialView?: "candles" | "overview"; initialAnchor?: number; onViewportChange?: (range: HistoryRange) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chartSurface = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const fitRef = useRef<(() => void) | null>(null);
  const candlesRef = useRef<(() => void) | null>(null);
  const latestRef = useRef<(() => void) | null>(null);
  const interactionRef = useRef<(() => void) | null>(null);
  const retryRef = useRef<(() => void) | null>(null);
  const [hovered, setHovered] = useState<HistoricalCandle | null>(null);
  const [lastVisible, setLastVisible] = useState(initialView === "overview" ? history.candles.at(-1) : undefined);
  const [resolution, setResolution] = useState(initialView === "overview" ? history.aggregate_size : 1);
  const [isOverview, setIsOverview] = useState(initialView === "overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [visibleWindow, setVisibleWindow] = useState<HistoryRange | null>(null);
  const [limited, setLimited] = useState(false);
  const current = hovered ?? lastVisible;

  useEffect(() => {
    if (!container.current || !chartSurface.current || !history.candles.length) return;
    const element = container.current;
    const surface = chartSurface.current;
    // Lightweight Charts subtracts the pane's viewport origin from clientX/Y.
    // Cancel the inherited CSS zoom only on the canvas surface, so those screen
    // pixels also match its drawing coordinates (including drag/zoom anchors).
    const measureSurface = () => {
      const scale = getAppScale();
      const bounds = element.getBoundingClientRect();
      const width = Math.max(1, Math.floor(bounds.width));
      const height = Math.max(1, Math.floor(bounds.height));
      surface.style.zoom = String(1 / scale);
      surface.style.width = `${width}px`;
      surface.style.height = `${height}px`;
      return { width, height, scale };
    };
    const dimensions = measureSurface();
    const overview = history.candles;
    const bounds = { from: overview[0].time, to: overview.at(-1)!.end_time };
    let displayed = initialView === "overview" ? overview : [];
    let byTime = new Map(displayed.map((bar) => [bar.time, bar]));
    let applying = false;
    let restoredRange: HistoryRange | null = null;
    const userInteraction = () => { restoredRange = null; };
    interactionRef.current = userInteraction;
    surface.addEventListener("wheel", userInteraction, { passive: true });
    surface.addEventListener("pointerdown", userInteraction);
    surface.addEventListener("touchstart", userInteraction, { passive: true });
    let disposed = false;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let loadedRange = bounds;
    let loadedResolution = history.aggregate_size;
    let overviewMode = initialView === "overview";
    const colors = readBotChartThemeColors();
    const chart = createChart(surface, {
      width: dimensions.width,
      height: dimensions.height,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: colors.label, fontFamily: "Inter, sans-serif", fontSize: 12 * dimensions.scale },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.border, scaleMargins: { top: 0.08, bottom: 0.25 } },
      timeScale: { borderColor: colors.border, timeVisible: true, secondsVisible: false, rightOffset: 0, minBarSpacing: 0.01 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { timeFormatter: (time: number) => `${timestamp(time)} UTC` },
    });
    chartRef.current = chart;
    const series = chart.addSeries(CandlestickSeries, {
      upColor: colors.positive, downColor: colors.negative, borderVisible: false,
      wickUpColor: colors.positive, wickDownColor: colors.negative,
      priceFormat: { type: "price", precision: 2, minMove: 0.25 },
    });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.83, bottom: 0 }, visible: false });
    const volumeData = () => {
      const theme = readBotChartThemeColors();
      return displayed.map((bar) => ({ time: bar.time as UTCTimestamp, value: bar.volume, color: bar.close >= bar.open ? theme.positive : theme.negative }));
    };
    const reportViewport = (range: HistoryRange) => {
      if (!displayed.length) return;
      const visible = { from: logicalToTime(displayed, range.from), to: logicalToTime(displayed, range.to) };
      setLastVisible(lastCandleInRange(displayed, visible));
      const clipped = { from: Math.max(bounds.from, visible.from), to: Math.min(bounds.to, visible.to) };
      setVisibleWindow(clipped.to >= clipped.from ? clipped : null);
      if (clipped.to >= clipped.from) onViewportChange?.(clipped);
    };
    const paint = (candles: HistoricalCandle[], preserveViewport = false, visible?: HistoryRange) => {
      const logical = preserveViewport ? chart.timeScale().getVisibleLogicalRange() : null;
      const range = visible ? {
        from: timeToLogical(candles, visible.from), to: timeToLogical(candles, visible.to),
      } : logical ? {
        from: timeToLogical(candles, logicalToTime(displayed, logical.from)),
        to: timeToLogical(candles, logicalToTime(displayed, logical.to)),
      } : { from: 0, to: candles.length - 1 };
      applying = true;
      restoredRange = range;
      displayed = candles;
      byTime = new Map(displayed.map((bar) => [bar.time, bar]));
      series.setData(displayed.map((bar) => ({ ...bar, time: bar.time as UTCTimestamp })));
      volume.setData(volumeData());
      chart.timeScale().setVisibleLogicalRange(range);
      applying = false;
      reportViewport(range);
    };
    if (initialView === "overview") paint(overview);
    chart.subscribeCrosshairMove((event) => setHovered(typeof event.time === "number" ? byTime.get(event.time) ?? null : null));
    const reset = () => {
      overviewMode = true;
      setIsOverview(true);
      generation++;
      clearTimeout(timer);
      controller?.abort();
      retryRef.current = null;
      setLimited(false);
      loadedRange = bounds;
      loadedResolution = history.aggregate_size;
      setResolution(loadedResolution);
      setBusy(false);
      setError(null);
      setHovered(null);
      setLastVisible(overview.at(-1));
      paint(overview);
    };
    fitRef.current = reset;
    const showCandles = async (latest = false, anchor?: number) => {
      const range = chart.timeScale().getVisibleLogicalRange();
      const span = 300 * timeframeSeconds[history.timeframe];
      const center = latest && anchor !== undefined ? anchor
        : range && displayed.length ? logicalToTime(displayed, (range.from + range.to) / 2) : bounds.to;
      const end = latest && anchor === undefined ? bounds.to : Math.min(bounds.to, Math.max(bounds.from + span, center + span / 2));
      const window = { from: Math.max(bounds.from, end - span), to: end };
      const requestGeneration = ++generation;
      clearTimeout(timer);
      controller?.abort();
      controller = new AbortController();
      setBusy(true);
      setError(null);
      retryRef.current = () => { void showCandles(latest, anchor); };
      try {
        const detail = await requestJson<ChartWindow>("/api/market-data/databento/candles", {
          query: { timeframe: history.timeframe, start: new Date(window.from * 1000).toISOString(), end: new Date(window.to * 1000).toISOString(), overview: true, limit: 2000 },
          signal: controller.signal,
        });
        if (disposed || requestGeneration !== generation) return;
        if (!detail.candles.length) {
          setError("No candles in this period. Pan to a trading session and try again.");
          return;
        }
        if (detail.aggregate_size !== 1 || detail.candles.some(bar => bar.count !== 1)) throw new Error("Expected individual candles");
        overviewMode = false;
        setIsOverview(false);
        setLimited(false);
        loadedRange = window;
        loadedResolution = detail.aggregate_size;
        setResolution(loadedResolution);
        setHovered(null);
        setLastVisible(detail.candles.at(-1));
        // Never stitch coarse overview bars onto an exact candle timeline.
        // Doing so makes a small pan cross months as it reaches the seam.
        paint(detail.candles, false, {
          from: detail.candles[0].time, to: detail.candles.at(-1)!.time,
        });
      } catch {
        if (!disposed && requestGeneration === generation) setError("Could not load individual candles. Try again.");
      } finally {
        if (!disposed && requestGeneration === generation) setBusy(false);
      }
    };
    candlesRef.current = () => { void showCandles(); };
    latestRef.current = () => { void showCandles(true); };
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (applying || disposed || !range || !displayed.length) return;
      // setData can emit an intermediate range before the queued restoration.
      // Ignore that sequence until it finishes; real input cancels the guard.
      const restored = restoredRange;
      if (restored) {
        if (Math.abs(range.from - restored.from) < 1e-6 && Math.abs(range.to - restored.to) < 1e-6) restoredRange = null;
        return;
      }
      reportViewport(range);
      setHovered(null);
      const visible = { from: logicalToTime(displayed, range.from), to: logicalToTime(displayed, range.to) };
      if (visible.to <= visible.from) return;
      generation++;
      const requestGeneration = generation;
      clearTimeout(timer);
      controller?.abort();
      setBusy(false);
      setError(null);
      retryRef.current = null;
      const clipped = { from: Math.max(bounds.from, visible.from), to: Math.min(bounds.to, visible.to) };
      // Exact candles already cover this viewport, so no further request is needed.
      if (loadedResolution === 1 && clipped.from >= loadedRange.from && clipped.to <= loadedRange.to) {
        setBusy(false);
        setLimited(false);
        return;
      }
      const loadDetail = async () => {
        if (disposed) return;
        if (overviewMode && visible.from <= bounds.from && visible.to >= overview.at(-1)!.time) {
          if (displayed !== overview) paint(overview, true);
          loadedRange = bounds;
          loadedResolution = history.aggregate_size;
          setResolution(loadedResolution);
          setBusy(false);
          return;
        }
        const window = candleRequestWindow(visible, bounds, timeframeSeconds[history.timeframe], overviewMode);
        if (!window) return;
        setLimited(!overviewMode && (window.from > clipped.from || window.to < clipped.to));
        controller = new AbortController();
        setBusy(true);
        setError(null);
        try {
          const detail = await requestJson<ChartWindow>("/api/market-data/databento/candles", {
            query: { timeframe: history.timeframe, start: new Date(window.from * 1000).toISOString(), end: new Date(window.to * 1000).toISOString(), overview: true, limit: overviewMode ? 2000 : 10000 },
            signal: controller.signal,
          });
          if (disposed || requestGeneration !== generation) return;
          if (!overviewMode && (detail.aggregate_size !== 1 || detail.candles.some(bar => bar.count !== 1))) throw new Error("Expected individual candles");
          if (!detail.candles.length) {
            setError("No candles in this period. Pan to a trading session.");
            return;
          }
          loadedRange = window;
          loadedResolution = detail.aggregate_size;
          setResolution(loadedResolution);
          setHovered(null);
          paint(overviewMode ? mergeHistoryDetail(overview, detail) : detail.candles, true);
        } catch {
          if (!disposed && requestGeneration === generation) setError("Could not load this detail. Pan or zoom to retry.");
        } finally {
          if (!disposed && requestGeneration === generation) setBusy(false);
        }
      };
      retryRef.current = () => { void loadDetail(); };
      timer = setTimeout(() => { void loadDetail(); }, 250);
    });
    const updateTheme = () => {
      const theme = readBotChartThemeColors();
      chart.applyOptions({ layout: { textColor: theme.label }, grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } }, rightPriceScale: { borderColor: theme.border }, timeScale: { borderColor: theme.border } });
      series.applyOptions({ upColor: theme.positive, downColor: theme.negative, wickUpColor: theme.positive, wickDownColor: theme.negative });
      volume.setData(volumeData());
    };
    window.addEventListener(APP_THEME_CHANGED_EVENT, updateTheme);
    let measured = dimensions;
    const resize = () => {
      const size = measureSurface();
      if (size.width === measured.width && size.height === measured.height && size.scale === measured.scale) return;
      measured = size;
      const range = chart.timeScale().getVisibleLogicalRange();
      applying = true;
      restoredRange = range;
      chart.applyOptions({ layout: { fontSize: 12 * size.scale } });
      chart.resize(size.width, size.height);
      if (range) chart.timeScale().setVisibleLogicalRange(range);
      applying = false;
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    window.addEventListener(APP_SCALE_CHANGED_EVENT, resize);
    if (initialView === "candles") void showCandles(true, initialAnchor);
    return () => {
      disposed = true;
      controller?.abort();
      clearTimeout(timer);
      window.removeEventListener(APP_THEME_CHANGED_EVENT, updateTheme);
      window.removeEventListener(APP_SCALE_CHANGED_EVENT, resize);
      observer.disconnect();
      surface.removeEventListener("wheel", userInteraction);
      surface.removeEventListener("pointerdown", userInteraction);
      surface.removeEventListener("touchstart", userInteraction);
      fitRef.current = null;
      candlesRef.current = null;
      latestRef.current = null;
      interactionRef.current = null;
      retryRef.current = null;
      chartRef.current = null;
      chart.remove();
    };
  }, [history, initialView, initialAnchor, onViewportChange]);

  const zoom = (factor: number) => {
    const chart = chartRef.current;
    const range = chart?.timeScale().getVisibleLogicalRange();
    if (!chart || !range) return;
    interactionRef.current?.();
    const center = (range.from + range.to) / 2;
    const half = Math.max(0.5, (range.to - range.from + 1) * factor / 2);
    chart.timeScale().setVisibleLogicalRange({ from: center - half + 0.5, to: center + half - 0.5 });
  };

  const pan = (direction: number) => {
    const chart = chartRef.current;
    const range = chart?.timeScale().getVisibleLogicalRange();
    if (!chart || !range) return;
    interactionRef.current?.();
    const offset = direction * (range.to - range.from + 1) * 0.75;
    chart.timeScale().setVisibleLogicalRange({ from: range.from + offset, to: range.to + offset });
  };

  return <div className="overflow-hidden rounded-2xl border border-app-border bg-app-surface/50">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-app-border px-4 py-3">
      <div>
        <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-app-text"><span className="h-2 w-2 rounded-full bg-app-accent" />MNQ <span className="font-normal text-app-muted">Micro E-mini Nasdaq-100</span><span className="rounded border border-app-border px-2 py-1 text-xs">{!isOverview ? `${history.timeframe} candles` : `Grouped overview · ${history.timeframe} source`}</span></div>
        <p className="mt-1 text-xs text-app-muted">{current ? `${timestamp(current.time)} UTC · ${current.contract}${current.count > 1 ? ` · ${current.count.toLocaleString()} candles combined` : ""}` : "Historical candles"}</p>
      </div>
      <div className="flex flex-wrap gap-2">{isOverview && <Button size="sm" variant="secondary" onClick={() => candlesRef.current?.()}>Show {history.timeframe} candles</Button>}<Button size="sm" variant="ghost" onClick={() => pan(-1)}>Earlier</Button><Button size="sm" variant="ghost" onClick={() => pan(1)}>Later</Button><Button size="sm" variant="ghost" onClick={() => zoom(0.3)}>Zoom in</Button><Button size="sm" variant="ghost" onClick={() => zoom(3)}>Zoom out</Button><Button size="sm" variant="ghost" onClick={() => latestRef.current?.()}>Latest candles</Button><Button size="sm" variant="ghost" onClick={() => fitRef.current?.()}>History overview</Button></div>
    </div>
    <p role={error ? "alert" : "status"} className="px-4 pt-3 text-xs text-app-muted">{error ?? (busy ? "Loading candle detail…" : resolution > 1 ? `Overview · up to ${resolution.toLocaleString()} × ${history.timeframe} candles per bar. Zoom in to see individual candles.` : `Individual ${history.timeframe} candles · Drag to explore more history.`)}{error && <Button size="sm" variant="ghost" disabled={busy} onClick={() => retryRef.current?.()}>Retry candles</Button>}</p>
    {limited && <p className="px-4 pt-2 text-xs text-app-muted">This zoom exceeds the individual-candle window. Zoom in, select a larger timeframe, or use History overview to fill the view.</p>}
    {current && <dl className="flex flex-wrap gap-x-5 gap-y-1 px-4 pt-3 text-xs tabular-nums text-app-text" aria-label="Candle values">
      {([["Open", price(current.open)], ["High", price(current.high)], ["Low", price(current.low)], ["Close", price(current.close)], ["Volume", current.volume.toLocaleString()]] as const).map(([label, value]) => <div key={label} className="flex gap-2"><dt className="text-app-muted">{label}</dt><dd>{value}</dd></div>)}
    </dl>}
    <div ref={container} role="img" aria-label="MNQ Databento candlestick and volume chart" className="h-[520px] w-full overflow-hidden"><div ref={chartSurface} /></div>
    <div className="flex flex-wrap justify-between gap-2 border-t border-app-border px-4 py-2 text-xs text-app-muted"><span>Scroll to zoom · Drag to pan · Hover to inspect</span><span>{visibleWindow ? `Visible: ${timestamp(visibleWindow.from)} – ${timestamp(visibleWindow.to)} UTC` : "No candles in view · Use Latest candles to return"}</span></div>
  </div>;
}
