import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
} from "lightweight-charts";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { botsApi } from "../../lib/api";
import type { BotActivity, BotConfig, BotEvaluation, ProjectXMarketCandle } from "../../lib/types";
import { buildBotCandleCacheKey, mergeMarketCandles, readBotCandleCache, writeBotCandleCache } from "./botCandleCache";
import { buildBotChartQuery, buildBotLivePriceQuery, buildCandlestickData, buildSignalMarkers, buildSmaData } from "./botChartData";

const POLL_INTERVAL_MS = 30_000;
const LIVE_PRICE_POLL_INTERVAL_MS = 5_000;
const CANDLE_REQUEST_TIMEOUT_MS = 70_000;
const LIVE_PRICE_REQUEST_TIMEOUT_MS = 12_000;

const lastLoadedFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

interface ChartHandles {
  chart: IChartApi;
  candleSeries: ISeriesApi<"Candlestick">;
  fastSeries: ISeriesApi<"Line">;
  slowSeries: ISeriesApi<"Line">;
  markers: ISeriesMarkersPluginApi<Time>;
}

interface BotSignalChartProps {
  bot: BotConfig | null;
  activity: BotActivity | null;
  lastEvaluation: BotEvaluation | null;
  refreshToken: number;
}

interface LoadCandlesOptions {
  silent?: boolean;
  forceRefresh?: boolean;
}

export function BotSignalChart({ bot, activity, lastEvaluation, refreshToken }: BotSignalChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartHandlesRef = useRef<ChartHandles | null>(null);
  const livePriceLineRef = useRef<IPriceLine | null>(null);
  const requestSequenceRef = useRef(0);
  const requestAbortRef = useRef<AbortController | null>(null);
  const liveRequestSequenceRef = useRef(0);
  const liveRequestAbortRef = useRef<AbortController | null>(null);
  const candlesRef = useRef<ProjectXMarketCandle[]>([]);
  const [candles, setCandles] = useState<ProjectXMarketCandle[]>([]);
  const [liveCandle, setLiveCandle] = useState<ProjectXMarketCandle | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [livePriceError, setLivePriceError] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);

  const visibleCandles = useMemo(() => mergeLiveCandle(candles, liveCandle), [candles, liveCandle]);
  const chartCandles = useMemo(() => buildCandlestickData(visibleCandles), [visibleCandles]);
  const closedChartCandles = useMemo(() => buildCandlestickData(candles), [candles]);
  const fastSma = useMemo(() => buildSmaData(closedChartCandles, bot?.fast_period ?? 0), [bot?.fast_period, closedChartCandles]);
  const slowSma = useMemo(() => buildSmaData(closedChartCandles, bot?.slow_period ?? 0), [bot?.slow_period, closedChartCandles]);
  const signalMarkers = useMemo(
    () =>
      buildSignalMarkers({
        candles: closedChartCandles,
        activityDecisions: activity && activity.config.id === bot?.id ? activity.decisions : [],
        lastEvaluation: lastEvaluation?.config.id === bot?.id ? lastEvaluation : null,
      }),
    [activity, bot?.id, closedChartCandles, lastEvaluation],
  );
  const livePrice = liveCandle && Number.isFinite(liveCandle.close) ? liveCandle.close : null;

  useEffect(() => {
    candlesRef.current = candles;
  }, [candles]);

  const loadCandles = useCallback(
    async ({ silent = false, forceRefresh = false }: LoadCandlesOptions = {}) => {
      if (!bot) {
        requestSequenceRef.current += 1;
        requestAbortRef.current?.abort();
        requestAbortRef.current = null;
        setCandles([]);
        setLiveCandle(null);
        setError(null);
        setLivePriceError(null);
        setLastLoadedAt(null);
        setLoading(false);
        setRefreshing(false);
        return;
      }

      const requestId = requestSequenceRef.current + 1;
      requestSequenceRef.current = requestId;
      requestAbortRef.current?.abort();
      const controller = new AbortController();
      requestAbortRef.current = controller;
      const timeoutId = window.setTimeout(() => controller.abort(), CANDLE_REQUEST_TIMEOUT_MS);
      const queryWindow = buildBotChartQuery(bot);
      const cacheKey = buildBotCandleCacheKey({
        contractId: bot.contract_id,
        symbol: bot.symbol,
        live: false,
        unit: bot.timeframe_unit,
        unitNumber: bot.timeframe_unit_number,
      });
      const cachedEntry = forceRefresh ? null : readBotCandleCache(cacheKey);
      const cachedCandles = cachedEntry?.candles ?? [];

      if (cachedEntry && cachedCandles.length > 0) {
        setCandles(cachedCandles);
        candlesRef.current = cachedCandles;
        setLastLoadedAt(cachedEntry.savedAt);
        setLoading(false);
        if (!silent) {
          setRefreshing(true);
        }
      } else if (forceRefresh) {
        setRefreshing(true);
      } else if (!silent) {
        setLoading(true);
      }
      setError(null);

      try {
        const rows = await botsApi.getCandles({
          contractId: bot.contract_id,
          symbol: bot.symbol ?? undefined,
          start: queryWindow.start,
          end: queryWindow.end,
          live: false,
          unit: bot.timeframe_unit,
          unitNumber: bot.timeframe_unit_number,
          limit: queryWindow.limit,
          includePartialBar: false,
          refresh: forceRefresh,
        }, { signal: controller.signal });
        if (requestSequenceRef.current !== requestId) {
          return;
        }
        const nextRows = forceRefresh ? rows : mergeMarketCandles(cachedCandles, rows, queryWindow.limit);
        setCandles(nextRows);
        candlesRef.current = nextRows;
        writeBotCandleCache(cacheKey, nextRows, queryWindow.limit);
        setLastLoadedAt(new Date());
      } catch (err) {
        if (requestSequenceRef.current !== requestId) {
          return;
        }
        if (silent && !forceRefresh && candlesRef.current.length > 0) {
          return;
        }
        if (isAbortError(err)) {
          setError("Timed out loading chart candles. Try Refresh, or check the ProjectX history connection.");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load chart candles");
        }
      } finally {
        window.clearTimeout(timeoutId);
        if (requestAbortRef.current === controller) {
          requestAbortRef.current = null;
        }
        if (requestSequenceRef.current === requestId) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [bot],
  );

  const loadLivePrice = useCallback(async () => {
    if (!bot) {
      liveRequestSequenceRef.current += 1;
      liveRequestAbortRef.current?.abort();
      liveRequestAbortRef.current = null;
      setLiveCandle(null);
      setLivePriceError(null);
      return;
    }

    const requestId = liveRequestSequenceRef.current + 1;
    liveRequestSequenceRef.current = requestId;
    liveRequestAbortRef.current?.abort();
    const controller = new AbortController();
    liveRequestAbortRef.current = controller;
    const timeoutId = window.setTimeout(() => controller.abort(), LIVE_PRICE_REQUEST_TIMEOUT_MS);
    const queryWindow = buildBotLivePriceQuery(bot);

    try {
      const rows = await botsApi.getCandles({
        contractId: bot.contract_id,
        symbol: bot.symbol ?? undefined,
        start: queryWindow.start,
        end: queryWindow.end,
        live: false,
        unit: bot.timeframe_unit,
        unitNumber: bot.timeframe_unit_number,
        limit: queryWindow.limit,
        includePartialBar: true,
        refresh: true,
      }, { signal: controller.signal });
      if (liveRequestSequenceRef.current !== requestId) {
        return;
      }

      const latest = getLatestMarketCandle(rows);
      setLiveCandle(latest);
      setLivePriceError(latest ? null : "No live price was returned.");
    } catch (err) {
      if (liveRequestSequenceRef.current !== requestId) {
        return;
      }
      setLivePriceError(isAbortError(err) ? "Timed out loading live price." : err instanceof Error ? err.message : "Failed to load live price.");
    } finally {
      window.clearTimeout(timeoutId);
      if (liveRequestAbortRef.current === controller) {
        liveRequestAbortRef.current = null;
      }
    }
  }, [bot]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 320),
      height: Math.max(container.clientHeight, 320),
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "rgb(148,163,184)",
      },
      grid: {
        vertLines: { color: "rgba(51,65,85,0.35)" },
        horzLines: { color: "rgba(51,65,85,0.35)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: "rgba(71,85,105,0.55)",
        scaleMargins: {
          top: 0.08,
          bottom: 0.12,
        },
      },
      timeScale: {
        borderColor: "rgba(71,85,105,0.55)",
        timeVisible: true,
        secondsVisible: true,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "rgb(34,197,94)",
      downColor: "rgb(244,63,94)",
      borderUpColor: "rgb(34,197,94)",
      borderDownColor: "rgb(244,63,94)",
      wickUpColor: "rgb(34,197,94)",
      wickDownColor: "rgb(244,63,94)",
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const fastSeries = chart.addSeries(LineSeries, {
      color: "rgb(34,211,238)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const slowSeries = chart.addSeries(LineSeries, {
      color: "rgb(250,204,21)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const markers = createSeriesMarkers(candleSeries);
    chartHandlesRef.current = { chart, candleSeries, fastSeries, slowSeries, markers };

    const resize = () => {
      chart.resize(Math.max(container.clientWidth, 320), Math.max(container.clientHeight, 320));
    };
    resize();

    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(container);
    } else {
      window.addEventListener("resize", resize);
    }

    return () => {
      resizeObserver?.disconnect();
      if (resizeObserver === null) {
        window.removeEventListener("resize", resize);
      }
      markers.detach();
      chart.remove();
      chartHandlesRef.current = null;
      livePriceLineRef.current = null;
    };
  }, []);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    handles.chart.applyOptions({
      timeScale: {
        secondsVisible: bot?.timeframe_unit === "second",
      },
    });
    handles.candleSeries.setData(chartCandles);
    handles.fastSeries.setData(fastSma);
    handles.slowSeries.setData(slowSma);
    handles.markers.setMarkers(signalMarkers);
  }, [bot?.timeframe_unit, chartCandles, fastSma, signalMarkers, slowSma]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles || closedChartCandles.length === 0) {
      return;
    }

    handles.chart.timeScale().fitContent();
  }, [bot?.id, closedChartCandles]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    if (livePrice === null) {
      if (livePriceLineRef.current) {
        handles.candleSeries.removePriceLine(livePriceLineRef.current);
        livePriceLineRef.current = null;
      }
      return;
    }

    const priceLineOptions = {
      price: livePrice,
      color: "rgb(56,189,248)",
      lineWidth: 2 as const,
      lineStyle: LineStyle.Dashed,
      lineVisible: true,
      axisLabelVisible: true,
      title: liveCandle?.is_partial ? "Live" : "Last",
      axisLabelColor: "rgb(8,145,178)",
      axisLabelTextColor: "rgb(240,249,255)",
    };

    if (livePriceLineRef.current) {
      livePriceLineRef.current.applyOptions(priceLineOptions);
    } else {
      livePriceLineRef.current = handles.candleSeries.createPriceLine(priceLineOptions);
    }
  }, [liveCandle?.is_partial, livePrice]);

  useEffect(() => {
    setCandles([]);
    setLiveCandle(null);
    setLivePriceError(null);
    void loadCandles();
    void loadLivePrice();
  }, [loadCandles, loadLivePrice, refreshToken]);

  useEffect(() => {
    return () => {
      requestAbortRef.current?.abort();
      liveRequestAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!bot?.enabled) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadCandles({ silent: true });
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [bot?.enabled, loadCandles]);

  useEffect(() => {
    if (!bot) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadLivePrice();
    }, LIVE_PRICE_POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [bot, loadLivePrice]);

  const subtitle = bot
    ? `${bot.symbol ?? bot.contract_id} / ${bot.timeframe_unit_number} ${bot.timeframe_unit}`
    : "No bot selected";
  const lastLoadedText = lastLoadedAt ? `Loaded ${lastLoadedFormatter.format(lastLoadedAt)}` : null;
  const livePriceText = livePrice !== null ? `${liveCandle?.is_partial ? "Live" : "Last"} ${priceFormatter.format(livePrice)}` : null;
  const livePriceTitle = liveCandle ? `Price timestamp ${lastLoadedFormatter.format(new Date(liveCandle.timestamp))}` : undefined;

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <CardTitle>Signal Chart</CardTitle>
          <CardDescription>{subtitle}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {livePriceText ? (
            <span
              className="inline-flex items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-2.5 py-1 text-xs font-semibold text-cyan-100"
              title={livePriceTitle}
            >
              <span className="h-2 w-2 rounded-full bg-cyan-300" />
              {livePriceText}
            </span>
          ) : bot && livePriceError ? (
            <span className="text-xs text-amber-300" title={livePriceError}>
              Live price unavailable
            </span>
          ) : null}
          {lastLoadedText ? <span className="text-xs text-slate-500">{lastLoadedText}</span> : null}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void loadCandles({ silent: true, forceRefresh: true });
              void loadLivePrice();
            }}
            disabled={!bot || loading || refreshing}
          >
            {refreshing ? "Refreshing" : "Refresh"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-4 text-xs text-slate-400">
          <LegendDot className="bg-cyan-400" label={`Fast SMA ${bot?.fast_period ?? "-"}`} />
          <LegendDot className="bg-yellow-300" label={`Slow SMA ${bot?.slow_period ?? "-"}`} />
          <LegendDot className="bg-emerald-500" label="Buy" />
          <LegendDot className="bg-rose-500" label="Sell" />
        </div>
        <div className="relative h-[360px] overflow-hidden rounded-xl border border-slate-800 bg-slate-950/45 md:h-[430px]">
          <div ref={containerRef} className="h-full w-full" />
          {loading ? (
            <div className="absolute inset-0 grid place-items-center bg-slate-950/50 text-sm text-slate-300">
              Loading candles
            </div>
          ) : null}
          {!loading && !error && bot && chartCandles.length === 0 ? (
            <div className="absolute inset-0 grid place-items-center text-sm text-slate-400">
              <span className="block max-w-[18rem] px-4 text-center">No candles returned for this chart window.</span>
            </div>
          ) : null}
          {!bot ? (
            <div className="absolute inset-0 grid place-items-center text-sm text-slate-400">
              <span className="block max-w-[18rem] px-4 text-center">Select or save a bot to load its ProjectX candles.</span>
            </div>
          ) : null}
          {error ? (
            <div className="absolute inset-x-4 top-4 rounded-xl border border-rose-400/35 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
              {error}
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function mergeLiveCandle(candles: ProjectXMarketCandle[], liveCandle: ProjectXMarketCandle | null): ProjectXMarketCandle[] {
  if (!liveCandle || !isRenderableMarketCandle(liveCandle)) {
    return candles;
  }

  const byTimestamp = new Map<string, ProjectXMarketCandle>();
  for (const candle of candles) {
    if (isRenderableMarketCandle(candle)) {
      byTimestamp.set(candle.timestamp, candle);
    }
  }
  byTimestamp.set(liveCandle.timestamp, liveCandle);

  return Array.from(byTimestamp.values()).sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
}

function getLatestMarketCandle(candles: ProjectXMarketCandle[]): ProjectXMarketCandle | null {
  return candles
    .filter(isRenderableMarketCandle)
    .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp))[0] ?? null;
}

function isRenderableMarketCandle(candle: ProjectXMarketCandle): boolean {
  return (
    Number.isFinite(Date.parse(candle.timestamp)) &&
    [candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`h-2.5 w-2.5 rounded-full ${className}`} />
      <span>{label}</span>
    </span>
  );
}

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}
