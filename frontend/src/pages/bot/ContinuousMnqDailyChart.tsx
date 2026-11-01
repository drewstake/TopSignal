import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, createChart, type IChartApi } from "lightweight-charts";
import { botsApi, requestJson } from "../../lib/api";
import { APP_SCALE_CHANGED_EVENT } from "../../lib/appScale";
import { APP_THEME_CHANGED_EVENT } from "../../lib/theme";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import type { ChartWindow } from "../backtesting/historyChartData";
import { BOT_CHART_TIMEFRAMES, type BotChartMarket, type BotChartTimeframeId } from "./botChartData";
import type { BotMarketSnapshot } from "./botMarketContext";
import { boundedBotRequest } from "./botPageRequests";
import { readBotChartThemeColors } from "./botChartTheme";
import { mergeContinuousDaily, sameMnqDelivery, type ContinuousDailyBar } from "./continuousMnqDaily";
import { usePageVisibility } from "./usePageVisibility";

export function ContinuousMnqDailyChart({ market, scope, onTimeframeChange, onMarketData }: {
  market: BotChartMarket; scope: string | null; onTimeframeChange: (id: BotChartTimeframeId) => void;
  onMarketData?: (snapshot: BotMarketSnapshot | null) => void;
}) {
  const [bars, setBars] = useState<ContinuousDailyBar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const visible = usePageVisibility();
  // Historical multi-contract bars must never masquerade as the live execution contract.
  useEffect(() => { onMarketData?.(null); }, [onMarketData]);
  useEffect(() => {
    if (!visible || !scope) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const load = async () => {
      controller = new AbortController();
      try {
        const now = new Date();
        const history = await boundedBotRequest(requestJson<ChartWindow>("/api/market-data/databento/candles", {
          query: { timeframe: "1d", start: new Date(now.getTime() - 3 * 366 * 86400000).toISOString(), end: now.toISOString(), limit: 1200 },
          signal: controller.signal,
        }), controller, "Continuous daily history timed out", 15_000);
        if (stopped) return;
        let next = mergeContinuousDaily(history, [], market.contract_id);
        let warning: string | null = null;
        const last = next.at(-1);
        // Only extend a recent archive ending on the same delivery. A rollover
        // needs an updated continuous archive, not a splice of unrelated contracts.
        if (last && sameMnqDelivery(last.contract, market.contract_id)
          && now.getTime() - Date.parse(last.date) <= 14 * 86400000) {
          try {
            const tail = await boundedBotRequest(botsApi.getCandles({ contractId: market.contract_id, symbol: market.symbol ?? undefined,
              unit: "day", unitNumber: 1, start: `${last.date}T00:00:00Z`, end: now.toISOString(), limit: 20,
              live: false, includePartialBar: false,
            }, { signal: controller.signal }), controller, "Recent daily candles timed out", 15_000);
            next = mergeContinuousDaily(history, tail, market.contract_id);
          } catch { warning = "Recent daily update unavailable; showing archived history."; }
        } else warning = "Showing archived history. The continuous dataset needs an update for recent sessions.";
        if (!stopped) {
          setBars(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
          setError(next.length ? warning : "No continuous daily history is available.");
        }
      } catch { if (!stopped) setError("Continuous daily history is unavailable. Check the local historical data connection and retry."); }
      finally {
        if (!stopped) { setLoading(false); timer = setTimeout(load, 60_000); }
      }
    };
    void load();
    return () => { stopped = true; clearTimeout(timer); controller?.abort(); };
  }, [market.contract_id, market.symbol, scope, visible, retry]);
  return <Card>
    <CardHeader><CardTitle>Signal Chart</CardTitle>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex overflow-hidden rounded-md border border-app-border" aria-label="Chart timeframe">
          {BOT_CHART_TIMEFRAMES.map(frame => <button key={frame.id} type="button" aria-label={`Show ${frame.label} candles`} aria-pressed={frame.id === "1d"}
            onClick={() => onTimeframeChange(frame.id)} className={`min-h-11 min-w-11 px-2.5 text-xs font-semibold sm:min-h-9 ${frame.id === "1d" ? "bg-app-accent/15 text-app-accent" : "text-app-muted"}`}>{frame.label}</button>)}
        </div><Button variant="secondary" onClick={() => setRetry(value => value + 1)}>Refresh</Button>
      </div>
    </CardHeader>
    <CardContent>
      {error && <p role="alert" className="mb-2 text-xs text-app-warning">{error}</p>}
      {bars.length ? <DailyCanvas bars={bars} /> : <div className="bot-chart-viewport grid place-items-center text-sm text-app-muted" role="status">{loading ? "Loading continuous MNQ daily history…" : "Daily history unavailable"}</div>}
      <p className="mt-2 text-xs text-app-muted">Daily candles use the trading date. Hover to see the source contract. All timeframes use continuous history; live prices and the order book use {market.contract_id.split(".").slice(-2).join("")}.</p>
    </CardContent>
  </Card>;
}

function DailyCanvas({ bars }: { bars: ContinuousDailyBar[] }) {
  const container = useRef<HTMLDivElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [hovered, setHovered] = useState<ContinuousDailyBar | null>(null);
  const current = hovered ?? bars.at(-1)!;
  useEffect(() => {
    if (!container.current || !surface.current) return;
    const element = container.current;
    const canvas = surface.current;
    const measure = () => {
      // The shared bot-chart-viewport already cancels the application's zoom.
      canvas.style.zoom = "1";
      const bounds = element.getBoundingClientRect();
      return { width: Math.max(1, Math.floor(bounds.width)), height: Math.max(1, Math.floor(bounds.height)) };
    };
    const colors = readBotChartThemeColors();
    const chart = createChart(canvas, { ...measure(),
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: colors.label },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.border, scaleMargins: { top: .08, bottom: .24 } },
      timeScale: { timeVisible: false, borderColor: colors.border },
      // Match the intraday chart: follow the pointer instead of snapping to a candle's close.
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;
    const series = chart.addSeries(CandlestickSeries, { upColor: colors.positive, downColor: colors.negative, wickUpColor: colors.positive,
      wickDownColor: colors.negative, borderVisible: false, priceFormat: { type: "price", precision: 2, minMove: .25 } });
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    series.setData(bars.map(bar => ({ time: bar.date, open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
    const applyTheme = () => {
      const theme = readBotChartThemeColors();
      chart.applyOptions({ layout: { textColor: theme.label }, grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } } });
      series.applyOptions({ upColor: theme.positive, downColor: theme.negative, wickUpColor: theme.positive, wickDownColor: theme.negative });
      volume.setData(bars.map(bar => ({ time: bar.date, value: bar.volume, color: bar.close >= bar.open ? theme.positive : theme.negative })));
    };
    applyTheme();
    const lookup = new Map(bars.map(bar => [bar.date, bar]));
    chart.subscribeCrosshairMove(event => {
      const t = event.time;
      const date = typeof t === "string" ? t : t && typeof t === "object" ? `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}` : null;
      setHovered(date ? lookup.get(date) ?? null : null);
    });
    // Show the requested historical range as well as the current delivery.
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 320), to: bars.length + 3 });
    const resize = () => chart.applyOptions(measure());
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    window.addEventListener(APP_SCALE_CHANGED_EVENT, resize);
    window.addEventListener(APP_THEME_CHANGED_EVENT, applyTheme);
    return () => { observer.disconnect(); window.removeEventListener(APP_SCALE_CHANGED_EVENT, resize); window.removeEventListener(APP_THEME_CHANGED_EVENT, applyTheme); chartRef.current = null; chart.remove(); };
  }, [bars]);
  const price = (value: number) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return <>
    <div className="mb-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-app-muted" aria-label="Daily candle details">
      <strong className="text-app-text">{current.date} · {current.contract}</strong><span>{current.source}</span>
      <span>O {price(current.open)}</span><span>H {price(current.high)}</span><span>L {price(current.low)}</span><span>C {price(current.close)}</span><span>Vol {current.volume.toLocaleString()}</span>
    </div>
    <div ref={container} className="bot-chart-viewport relative overflow-hidden" aria-label="Continuous MNQ daily candlestick and volume chart"><div ref={surface} className="bot-chart-canvas" /></div>
    <div className="mt-2 flex gap-2"><Button variant="secondary" onClick={() => chartRef.current?.timeScale().fitContent()}>Fit history</Button>
      <Button variant="secondary" onClick={() => chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 100), to: bars.length + 3 })}>Latest candles</Button></div>
  </>;
}
