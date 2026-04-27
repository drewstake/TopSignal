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
import { botsApi, streamProjectXMarketPrice } from "../../lib/api";
import type { BotActivity, BotConfig, BotEvaluation, BotTimeframeUnit, ProjectXMarketCandle, ProjectXMarketPrice } from "../../lib/types";
import { buildBotCandleCacheKey, mergeMarketCandles, readBotCandleCache, writeBotCandleCache } from "./botCandleCache";
import {
  buildBotChartQuery,
  buildBotLivePriceQuery,
  buildCandlestickData,
  buildLiquidityLevels,
  buildLiveCandleFromPriceUpdate,
  buildSignalMarkers,
  buildSmaData,
  type LiquidityLevel,
  type LiquiditySide,
} from "./botChartData";

const POLL_INTERVAL_MS = 30_000;
const LIVE_PRICE_POLL_INTERVAL_MS = 1_000;
const LIVE_PRICE_STREAM_THROTTLE_MS = 250;
const LIVE_PRICE_STREAM_STALE_MS = 5_000;
const CANDLE_REQUEST_TIMEOUT_MS = 70_000;
const LIVE_PRICE_REQUEST_TIMEOUT_MS = 12_000;
const LIQUIDITY_LINE_DRAG_HIT_RADIUS_PX = 8;
const CHART_TIMEFRAME_OPTIONS = [
  { id: "5m", label: "5m", unit: "minute", unitNumber: 5 },
  { id: "15m", label: "15m", unit: "minute", unitNumber: 15 },
  { id: "1h", label: "1H", unit: "hour", unitNumber: 1 },
  { id: "4h", label: "4H", unit: "hour", unitNumber: 4 },
  { id: "1d", label: "1D", unit: "day", unitNumber: 1 },
] as const;
type ChartTimeframeOption = (typeof CHART_TIMEFRAME_OPTIONS)[number];
type ChartTimeframeId = ChartTimeframeOption["id"];
const DEFAULT_CHART_TIMEFRAME_ID: ChartTimeframeId = "5m";

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

interface LoadLivePriceOptions {
  force?: boolean;
}

interface ChartTimeframeSelection {
  key: string;
  id: ChartTimeframeId;
}

type LiquidityPriceOverrides = Partial<Record<LiquiditySide, number>>;
type LiquidityPriceLineMap = Partial<Record<LiquiditySide, IPriceLine>>;

interface LiquidityDragState {
  side: LiquiditySide;
  pointerId: number;
}

export function BotSignalChart({ bot, activity, lastEvaluation, refreshToken }: BotSignalChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartHandlesRef = useRef<ChartHandles | null>(null);
  const livePriceLineRef = useRef<IPriceLine | null>(null);
  const liquidityPriceLinesRef = useRef<LiquidityPriceLineMap>({});
  const liquidityLevelsRef = useRef<LiquidityLevel[]>([]);
  const liquidityDragStateRef = useRef<LiquidityDragState | null>(null);
  const requestSequenceRef = useRef(0);
  const requestAbortRef = useRef<AbortController | null>(null);
  const liveRequestSequenceRef = useRef(0);
  const liveRequestAbortRef = useRef<AbortController | null>(null);
  const lastLiveStreamEventAtRef = useRef(0);
  const pendingLiveStreamPriceRef = useRef<ProjectXMarketPrice | null>(null);
  const liveStreamRenderTimeoutRef = useRef<number | null>(null);
  const candlesRef = useRef<ProjectXMarketCandle[]>([]);
  const [candles, setCandles] = useState<ProjectXMarketCandle[]>([]);
  const [liveCandle, setLiveCandle] = useState<ProjectXMarketCandle | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [livePriceError, setLivePriceError] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);
  const [liquidityPriceOverrides, setLiquidityPriceOverrides] = useState<LiquidityPriceOverrides>({});
  const botTimeframeSelectionKey = buildBotTimeframeSelectionKey(bot);
  const [timeframeSelection, setTimeframeSelection] = useState<ChartTimeframeSelection>(() => ({
    key: buildBotTimeframeSelectionKey(bot),
    id: defaultChartTimeframeIdForBot(bot),
  }));
  const selectedTimeframeId =
    timeframeSelection.key === botTimeframeSelectionKey ? timeframeSelection.id : defaultChartTimeframeIdForBot(bot);
  const chartTimeframe = CHART_TIMEFRAME_OPTIONS.find((option) => option.id === selectedTimeframeId) ?? CHART_TIMEFRAME_OPTIONS[0];
  const chartConfig = useMemo<BotConfig | null>(() => {
    if (!bot) {
      return null;
    }
    return {
      ...bot,
      timeframe_unit: chartTimeframe.unit,
      timeframe_unit_number: chartTimeframe.unitNumber,
    };
  }, [bot, chartTimeframe]);

  const visibleCandles = useMemo(() => mergeLiveCandle(candles, liveCandle), [candles, liveCandle]);
  const chartCandles = useMemo(() => buildCandlestickData(visibleCandles), [visibleCandles]);
  const closedChartCandles = useMemo(() => buildCandlestickData(candles), [candles]);
  const fastSma = useMemo(() => buildSmaData(chartCandles, bot?.fast_period ?? 0), [bot?.fast_period, chartCandles]);
  const slowSma = useMemo(() => buildSmaData(chartCandles, bot?.slow_period ?? 0), [bot?.slow_period, chartCandles]);
  const liquidityLevels = useMemo(() => buildLiquidityLevels(closedChartCandles), [closedChartCandles]);
  const displayedLiquidityLevels = useMemo(
    () =>
      liquidityLevels.map((level) => ({
        ...level,
        price: liquidityPriceOverrides[level.side] ?? level.price,
      })),
    [liquidityLevels, liquidityPriceOverrides],
  );
  const signalMarkers = useMemo(
    () =>
      buildSignalMarkers({
        candles: closedChartCandles,
        activityDecisions: activity && activity.config.id === bot?.id ? activity.decisions : [],
        lastEvaluation: lastEvaluation?.config.id === bot?.id ? lastEvaluation : null,
        timeframeUnit: chartTimeframe.unit,
        timeframeUnitNumber: chartTimeframe.unitNumber,
      }),
    [activity, bot?.id, chartTimeframe, closedChartCandles, lastEvaluation],
  );
  const livePrice = liveCandle && Number.isFinite(liveCandle.close) ? liveCandle.close : null;
  const liquidityDragContextKey = `${bot?.id ?? "none"}:${bot?.contract_id ?? ""}:${selectedTimeframeId}`;

  useEffect(() => {
    candlesRef.current = candles;
  }, [candles]);

  useEffect(() => {
    liquidityLevelsRef.current = displayedLiquidityLevels;
  }, [displayedLiquidityLevels]);

  useEffect(() => {
    setLiquidityPriceOverrides({});
    if (liquidityDragStateRef.current) {
      chartHandlesRef.current?.chart.applyOptions({ handleScroll: true, handleScale: true });
      if (containerRef.current) {
        containerRef.current.style.cursor = "";
      }
    }
    liquidityDragStateRef.current = null;
  }, [liquidityDragContextKey]);

  const loadCandles = useCallback(
    async ({ silent = false, forceRefresh = false }: LoadCandlesOptions = {}) => {
      if (!chartConfig) {
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
      const queryWindow = buildBotChartQuery(chartConfig);
      const cacheKey = buildBotCandleCacheKey({
        contractId: chartConfig.contract_id,
        symbol: chartConfig.symbol,
        live: false,
        unit: chartConfig.timeframe_unit,
        unitNumber: chartConfig.timeframe_unit_number,
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
          contractId: chartConfig.contract_id,
          symbol: chartConfig.symbol ?? undefined,
          start: queryWindow.start,
          end: queryWindow.end,
          live: false,
          unit: chartConfig.timeframe_unit,
          unitNumber: chartConfig.timeframe_unit_number,
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
    [chartConfig],
  );

  const loadLivePrice = useCallback(async ({ force = false }: LoadLivePriceOptions = {}) => {
    if (!chartConfig) {
      liveRequestSequenceRef.current += 1;
      liveRequestAbortRef.current?.abort();
      liveRequestAbortRef.current = null;
      setLiveCandle(null);
      setLivePriceError(null);
      return;
    }

    if (liveRequestAbortRef.current) {
      if (!force) {
        return;
      }
      liveRequestSequenceRef.current += 1;
      liveRequestAbortRef.current.abort();
    }

    const requestId = liveRequestSequenceRef.current + 1;
    liveRequestSequenceRef.current = requestId;
    const controller = new AbortController();
    liveRequestAbortRef.current = controller;
    const timeoutId = window.setTimeout(() => controller.abort(), LIVE_PRICE_REQUEST_TIMEOUT_MS);
    const queryWindow = buildBotLivePriceQuery(chartConfig);

    try {
      const rows = await botsApi.getCandles({
        contractId: chartConfig.contract_id,
        symbol: chartConfig.symbol ?? undefined,
        start: queryWindow.start,
        end: queryWindow.end,
        live: false,
        unit: chartConfig.timeframe_unit,
        unitNumber: chartConfig.timeframe_unit_number,
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
  }, [chartConfig]);

  const applyLiveStreamPrice = useCallback(
    (price: ProjectXMarketPrice) => {
      if (!chartConfig) {
        return;
      }

      setLiveCandle((current) =>
        buildLiveCandleFromPriceUpdate({
          config: chartConfig,
          price,
          closedCandles: candlesRef.current,
          currentLiveCandle: current,
        }),
      );
      setLivePriceError(null);
    },
    [chartConfig],
  );

  const flushPendingLiveStreamPrice = useCallback(() => {
    const nextPrice = pendingLiveStreamPriceRef.current;
    pendingLiveStreamPriceRef.current = null;
    if (!nextPrice) {
      return;
    }

    applyLiveStreamPrice(nextPrice);
  }, [applyLiveStreamPrice]);

  const scheduleLiveStreamPrice = useCallback(
    (price: ProjectXMarketPrice) => {
      pendingLiveStreamPriceRef.current = price;
      if (liveStreamRenderTimeoutRef.current !== null) {
        return;
      }

      liveStreamRenderTimeoutRef.current = window.setTimeout(() => {
        liveStreamRenderTimeoutRef.current = null;
        flushPendingLiveStreamPrice();
      }, LIVE_PRICE_STREAM_THROTTLE_MS);
    },
    [flushPendingLiveStreamPrice],
  );

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

    const applyDraggedLiquidityPrice = (side: LiquiditySide, rawPrice: number) => {
      const price = normalizeDraggedLiquidityPrice(rawPrice);
      const currentLevel = liquidityLevelsRef.current.find((level) => level.side === side);
      if (!currentLevel) {
        return;
      }

      liquidityPriceLinesRef.current[side]?.applyOptions(liquidityLevelToPriceLineOptions({ ...currentLevel, price }));
      setLiquidityPriceOverrides((current) => (current[side] === price ? current : { ...current, [side]: price }));
    };

    const priceFromPointerEvent = (event: PointerEvent): number | null => {
      const y = chartPaneYFromPointerEvent(event, container, chart);
      if (y === null) {
        return null;
      }

      const price = candleSeries.coordinateToPrice(y);
      return typeof price === "number" && Number.isFinite(price) ? price : null;
    };

    const setDragCursor = (active: boolean) => {
      container.style.cursor = active ? "ns-resize" : "";
    };

    const findLiquidityLineAtPointer = (event: PointerEvent): LiquiditySide | null => {
      const y = chartPaneYFromPointerEvent(event, container, chart);
      if (y === null) {
        return null;
      }

      let closestSide: LiquiditySide | null = null;
      let closestDistance = LIQUIDITY_LINE_DRAG_HIT_RADIUS_PX;
      for (const level of liquidityLevelsRef.current) {
        const lineY = candleSeries.priceToCoordinate(level.price);
        if (lineY === null) {
          continue;
        }

        const distance = Math.abs(lineY - y);
        if (distance <= closestDistance) {
          closestSide = level.side;
          closestDistance = distance;
        }
      }

      return closestSide;
    };

    const endLiquidityDrag = (event?: PointerEvent) => {
      const dragState = liquidityDragStateRef.current;
      if (!dragState) {
        return;
      }

      if (event && container.hasPointerCapture(event.pointerId)) {
        container.releasePointerCapture(event.pointerId);
      }
      liquidityDragStateRef.current = null;
      chart.applyOptions({ handleScroll: true, handleScale: true });
      setDragCursor(false);
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) {
        return;
      }

      const side = findLiquidityLineAtPointer(event);
      if (!side) {
        return;
      }

      const price = priceFromPointerEvent(event);
      if (price === null) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      container.setPointerCapture(event.pointerId);
      liquidityDragStateRef.current = { side, pointerId: event.pointerId };
      chart.applyOptions({ handleScroll: false, handleScale: false });
      setDragCursor(true);
      applyDraggedLiquidityPrice(side, price);
    };

    const handlePointerMove = (event: PointerEvent) => {
      const dragState = liquidityDragStateRef.current;
      if (!dragState) {
        setDragCursor(findLiquidityLineAtPointer(event) !== null);
        return;
      }
      if (event.pointerId !== dragState.pointerId) {
        return;
      }

      const price = priceFromPointerEvent(event);
      if (price === null) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();
      applyDraggedLiquidityPrice(dragState.side, price);
    };

    const handlePointerUp = (event: PointerEvent) => {
      if (liquidityDragStateRef.current?.pointerId !== event.pointerId) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      endLiquidityDrag(event);
    };

    const handlePointerCancel = (event: PointerEvent) => {
      if (liquidityDragStateRef.current?.pointerId === event.pointerId) {
        endLiquidityDrag(event);
      }
    };

    const handlePointerLeave = () => {
      if (!liquidityDragStateRef.current) {
        setDragCursor(false);
      }
    };

    const pointerListenerOptions: AddEventListenerOptions = { capture: true };
    container.addEventListener("pointerdown", handlePointerDown, pointerListenerOptions);
    container.addEventListener("pointermove", handlePointerMove, pointerListenerOptions);
    container.addEventListener("pointerup", handlePointerUp, pointerListenerOptions);
    container.addEventListener("pointercancel", handlePointerCancel, pointerListenerOptions);
    container.addEventListener("pointerleave", handlePointerLeave, pointerListenerOptions);

    return () => {
      container.removeEventListener("pointerdown", handlePointerDown, pointerListenerOptions);
      container.removeEventListener("pointermove", handlePointerMove, pointerListenerOptions);
      container.removeEventListener("pointerup", handlePointerUp, pointerListenerOptions);
      container.removeEventListener("pointercancel", handlePointerCancel, pointerListenerOptions);
      container.removeEventListener("pointerleave", handlePointerLeave, pointerListenerOptions);
      setDragCursor(false);
      resizeObserver?.disconnect();
      if (resizeObserver === null) {
        window.removeEventListener("resize", resize);
      }
      markers.detach();
      chart.remove();
      chartHandlesRef.current = null;
      livePriceLineRef.current = null;
      liquidityPriceLinesRef.current = {};
      liquidityLevelsRef.current = [];
      liquidityDragStateRef.current = null;
    };
  }, []);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    handles.chart.applyOptions({
      timeScale: {
        secondsVisible: chartConfig?.timeframe_unit === "second",
      },
    });
    handles.candleSeries.setData(chartCandles);
    handles.fastSeries.setData(fastSma);
    handles.slowSeries.setData(slowSma);
    handles.markers.setMarkers(signalMarkers);
  }, [chartConfig?.timeframe_unit, chartCandles, fastSma, signalMarkers, slowSma]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    const nextSides = new Set(displayedLiquidityLevels.map((level) => level.side));
    for (const side of ["buy", "sell"] as const) {
      const priceLine = liquidityPriceLinesRef.current[side];
      if (priceLine && !nextSides.has(side)) {
        handles.candleSeries.removePriceLine(priceLine);
        delete liquidityPriceLinesRef.current[side];
      }
    }

    for (const level of displayedLiquidityLevels) {
      const options = liquidityLevelToPriceLineOptions(level);
      const existingLine = liquidityPriceLinesRef.current[level.side];
      if (existingLine) {
        existingLine.applyOptions(options);
      } else {
        liquidityPriceLinesRef.current[level.side] = handles.candleSeries.createPriceLine(options);
      }
    }
  }, [displayedLiquidityLevels]);

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
    void loadLivePrice({ force: true });
  }, [loadCandles, loadLivePrice, refreshToken]);

  useEffect(() => {
    return () => {
      requestAbortRef.current?.abort();
      liveRequestAbortRef.current?.abort();
      if (liveStreamRenderTimeoutRef.current !== null) {
        window.clearTimeout(liveStreamRenderTimeoutRef.current);
        liveStreamRenderTimeoutRef.current = null;
      }
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
      lastLiveStreamEventAtRef.current = 0;
      pendingLiveStreamPriceRef.current = null;
      return;
    }

    lastLiveStreamEventAtRef.current = 0;
    pendingLiveStreamPriceRef.current = null;
    if (liveStreamRenderTimeoutRef.current !== null) {
      window.clearTimeout(liveStreamRenderTimeoutRef.current);
      liveStreamRenderTimeoutRef.current = null;
    }

    const stopStreaming = streamProjectXMarketPrice(
      {
        contractId: bot.contract_id,
        symbol: bot.symbol ?? undefined,
        throttleMs: LIVE_PRICE_STREAM_THROTTLE_MS,
      },
      {
        onPrice: (price) => {
          lastLiveStreamEventAtRef.current = Date.now();
          scheduleLiveStreamPrice(price);
        },
        onError: () => {
          lastLiveStreamEventAtRef.current = 0;
        },
      },
    );

    return () => {
      stopStreaming();
      lastLiveStreamEventAtRef.current = 0;
      pendingLiveStreamPriceRef.current = null;
      if (liveStreamRenderTimeoutRef.current !== null) {
        window.clearTimeout(liveStreamRenderTimeoutRef.current);
        liveStreamRenderTimeoutRef.current = null;
      }
    };
  }, [bot, scheduleLiveStreamPrice]);

  useEffect(() => {
    if (!bot) {
      return;
    }

    const intervalId = window.setInterval(() => {
      if (Date.now() - lastLiveStreamEventAtRef.current > LIVE_PRICE_STREAM_STALE_MS) {
        void loadLivePrice();
      }
    }, LIVE_PRICE_POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [bot, loadLivePrice]);

  const subtitle = bot
    ? buildChartSubtitle(bot, chartTimeframe)
    : "No bot selected";
  const lastLoadedText = lastLoadedAt ? `Loaded ${lastLoadedFormatter.format(lastLoadedAt)}` : null;
  const livePriceText = livePrice !== null ? `${liveCandle?.is_partial ? "Live" : "Last"} ${priceFormatter.format(livePrice)}` : null;
  const livePriceTitle = liveCandle ? `Price timestamp ${lastLoadedFormatter.format(new Date(liveCandle.timestamp))}` : undefined;
  const computedLiquidityButtonTitle =
    liquidityLevels.length > 0
      ? "Move Buy liq and Sell liq to their computed swing liquidity levels"
      : "No computed liquidity levels are available yet";

  return (
    <Card className="flex h-full min-h-[620px] flex-col">
      <CardHeader className="shrink-0 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <CardTitle>Signal Chart</CardTitle>
          <CardDescription>{subtitle}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex h-8 overflow-hidden rounded-md border border-slate-800 bg-slate-950/70" aria-label="Chart timeframe">
            {CHART_TIMEFRAME_OPTIONS.map((option) => {
              const active = option.id === selectedTimeframeId;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setTimeframeSelection({ key: botTimeframeSelectionKey, id: option.id })}
                  disabled={!bot}
                  className={`min-w-11 border-r border-slate-800 px-2.5 text-xs font-semibold transition last:border-r-0 disabled:cursor-not-allowed disabled:opacity-50 ${
                    active
                      ? "bg-cyan-400/15 text-cyan-100"
                      : "text-slate-400 hover:bg-slate-900/80 hover:text-slate-100"
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
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
            onClick={() => setLiquidityPriceOverrides({})}
            disabled={!bot || loading || liquidityLevels.length === 0}
            title={computedLiquidityButtonTitle}
          >
            Compute liq
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void loadCandles({ silent: true, forceRefresh: true });
              void loadLivePrice({ force: true });
            }}
            disabled={!bot || loading || refreshing}
          >
            {refreshing ? "Refreshing" : "Refresh"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-wrap gap-4 text-xs text-slate-400">
          <LegendDot className="bg-cyan-400" label={`Fast SMA ${bot?.fast_period ?? "-"}`} />
          <LegendDot className="bg-yellow-300" label={`Slow SMA ${bot?.slow_period ?? "-"}`} />
          <LegendDot className="bg-emerald-500" label="Buy" />
          <LegendDot className="bg-rose-500" label="Sell" />
          <LegendLine className="border-emerald-500" label="Buy-side liquidity" />
          <LegendLine className="border-rose-500" label="Sell-side liquidity" />
        </div>
        <div className="relative min-h-[420px] flex-1 overflow-hidden rounded-xl border border-slate-800 bg-slate-950/45 md:min-h-[560px] xl:min-h-0">
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

function findMatchingTimeframeId(unit: BotTimeframeUnit, unitNumber: number): ChartTimeframeId | null {
  const normalizedNumber = Math.max(1, Math.trunc(unitNumber));
  return (
    CHART_TIMEFRAME_OPTIONS.find((option) => option.unit === unit && option.unitNumber === normalizedNumber)?.id ?? null
  );
}

function defaultChartTimeframeIdForBot(bot: BotConfig | null): ChartTimeframeId {
  if (!bot) {
    return DEFAULT_CHART_TIMEFRAME_ID;
  }
  return findMatchingTimeframeId(bot.timeframe_unit, bot.timeframe_unit_number) ?? DEFAULT_CHART_TIMEFRAME_ID;
}

function buildBotTimeframeSelectionKey(bot: BotConfig | null): string {
  if (!bot) {
    return "none";
  }
  return `${bot.id}:${bot.timeframe_unit}:${Math.max(1, Math.trunc(bot.timeframe_unit_number))}`;
}

function chartPaneYFromPointerEvent(event: PointerEvent, container: HTMLDivElement, chart: IChartApi): number | null {
  const paneHeight = chart.paneSize().height;
  if (paneHeight <= 0) {
    return null;
  }

  const y = event.clientY - container.getBoundingClientRect().top;
  if (y < 0 || y > paneHeight) {
    return null;
  }

  return y;
}

function normalizeDraggedLiquidityPrice(price: number): number {
  const roundedPrice = Math.round(price * 10_000) / 10_000;
  return Object.is(roundedPrice, -0) ? 0 : roundedPrice;
}

function buildChartSubtitle(bot: BotConfig, chartTimeframe: ChartTimeframeOption): string {
  const market = bot.symbol ?? bot.contract_id;
  const botTimeframeLabel = formatTimeframeLabel(bot.timeframe_unit, bot.timeframe_unit_number);
  if (botTimeframeLabel === chartTimeframe.label) {
    return `${market} / ${chartTimeframe.label}`;
  }
  return `${market} / ${chartTimeframe.label} chart / ${botTimeframeLabel} bot`;
}

function formatTimeframeLabel(unit: BotTimeframeUnit, unitNumber: number): string {
  const normalizedNumber = Math.max(1, Math.trunc(unitNumber));
  const preset = CHART_TIMEFRAME_OPTIONS.find((option) => option.unit === unit && option.unitNumber === normalizedNumber);
  if (preset) {
    return preset.label;
  }

  const unitSuffix: Record<BotTimeframeUnit, string> = {
    second: "s",
    minute: "m",
    hour: "H",
    day: "D",
    week: "W",
    month: "M",
  };
  return `${normalizedNumber}${unitSuffix[unit]}`;
}

function liquidityLevelToPriceLineOptions(level: LiquidityLevel) {
  const isBuySide = level.side === "buy";
  const color = isBuySide ? "rgb(34,197,94)" : "rgb(244,63,94)";
  const axisLabelColor = isBuySide ? "rgb(22,163,74)" : "rgb(225,29,72)";

  return {
    id: `liquidity-${level.side}`,
    price: level.price,
    color,
    lineWidth: 2 as const,
    lineStyle: LineStyle.Dotted,
    lineVisible: true,
    axisLabelVisible: true,
    title: isBuySide ? "Buy liq" : "Sell liq",
    axisLabelColor,
    axisLabelTextColor: "rgb(255,255,255)",
  };
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

function LegendLine({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`h-0 w-5 border-t-2 border-dotted ${className}`} />
      <span>{label}</span>
    </span>
  );
}

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}
