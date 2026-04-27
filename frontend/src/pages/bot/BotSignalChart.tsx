import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  TickMarkType,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Logical,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
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
  buildVwapData,
  toUtcTimestamp,
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
const MIN_DRAWING_SIZE_PX = 4;
const DRAWING_HIT_RADIUS_PX = 8;
const DRAWING_ENDPOINT_HIT_RADIUS_PX = 14;
const RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX = 22;
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
const EASTERN_TIME_ZONE = "America/New_York";

const lastLoadedFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
});
const chartAxisTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});
const chartAxisTimeWithSecondsFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
});
const chartAxisDayFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  month: "short",
  day: "numeric",
});
const chartAxisMonthFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  month: "short",
});
const chartAxisYearFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  year: "numeric",
});
const chartCrosshairTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  hour12: true,
});
const priceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const signedPriceFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
  signDisplay: "exceptZero",
});
const signedPercentFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});
const volumeFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

interface ChartHandles {
  chart: IChartApi;
  candleSeries: ISeriesApi<"Candlestick">;
  fastSeries: ISeriesApi<"Line">;
  slowSeries: ISeriesApi<"Line">;
  vwapSeries: ISeriesApi<"Line">;
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
type DrawingTool = "cursor" | "line" | "rectangle";
type DrawingKind = Exclude<DrawingTool, "cursor">;
type DrawingEditMode = "start" | "end" | "left" | "right" | "body";

interface ChartPanePoint {
  x: number;
  y: number;
}

interface DrawingPoint {
  logical: Logical;
  time: UTCTimestamp | null;
  price: number;
}

interface DrawingShape {
  id: string;
  kind: DrawingKind;
  start: DrawingPoint;
  end: DrawingPoint;
}

type DrawingDraft = DrawingShape;

interface DrawingPlacementState {
  id: string;
  kind: DrawingKind;
  start: DrawingPoint;
  lastPanePoint: ChartPanePoint;
}

interface DrawingEditState {
  id: string;
  mode: DrawingEditMode;
  pointerId: number;
  originPanePoint: ChartPanePoint;
  originalDrawing: DrawingShape;
}

interface DrawingHitTarget {
  id: string;
  mode: DrawingEditMode;
}

interface DrawingModifiers {
  ctrlKey: boolean;
  shiftKey: boolean;
}

interface RenderableDrawing {
  id: string;
  kind: DrawingKind;
  isDraft: boolean;
  isSelected: boolean;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface RenderableDrawingAnchor {
  x: number;
  y: number;
}

interface RenderableLivePriceLine {
  x1: number;
  x2: number;
  y: number;
}

interface DrawingAnchorPreview {
  point: DrawingPoint;
}

interface DrawingOverlayState {
  width: number;
  height: number;
  items: RenderableDrawing[];
  anchor: RenderableDrawingAnchor | null;
  livePriceLine: RenderableLivePriceLine | null;
}

interface HoveredCandle {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  previousClose: number | null;
  volume: number;
  isPartial: boolean;
}

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
  const chartCandlesRef = useRef<CandlestickData<UTCTimestamp>[]>([]);
  const hoverCandlesByTimeRef = useRef<Map<number, HoveredCandle>>(new Map());
  const hoveredCandleTimeRef = useRef<number | null>(null);
  const drawingsRef = useRef<DrawingShape[]>([]);
  const drawingToolRef = useRef<DrawingTool>("cursor");
  const drawingPlacementStateRef = useRef<DrawingPlacementState | null>(null);
  const drawingEditStateRef = useRef<DrawingEditState | null>(null);
  const drawingDraftRef = useRef<DrawingDraft | null>(null);
  const drawingAnchorPreviewRef = useRef<DrawingAnchorPreview | null>(null);
  const lastDrawingPanePointRef = useRef<ChartPanePoint | null>(null);
  const selectedDrawingIdRef = useRef<string | null>(null);
  const drawingSequenceRef = useRef(0);
  const fittedViewportKeyRef = useRef<string | null>(null);
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
  const [drawingTool, setDrawingTool] = useState<DrawingTool>("cursor");
  const [drawings, setDrawings] = useState<DrawingShape[]>([]);
  const [drawingDraft, setDrawingDraft] = useState<DrawingDraft | null>(null);
  const [drawingAnchorPreview, setDrawingAnchorPreview] = useState<DrawingAnchorPreview | null>(null);
  const [selectedDrawingId, setSelectedDrawingId] = useState<string | null>(null);
  const [drawingOverlayRevision, setDrawingOverlayRevision] = useState(0);
  const [hoveredCandle, setHoveredCandle] = useState<HoveredCandle | null>(null);
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
  const hoverCandlesByTime = useMemo(() => buildHoverCandleMap(visibleCandles), [visibleCandles]);
  const latestOhlcCandle = useMemo(() => getLatestHoveredCandle(hoverCandlesByTime), [hoverCandlesByTime]);
  const chartCandles = useMemo(() => buildCandlestickData(visibleCandles), [visibleCandles]);
  const closedChartCandles = useMemo(() => buildCandlestickData(candles, { bridgeConsecutiveGaps: false }), [candles]);
  const fastSma = useMemo(() => buildSmaData(chartCandles, bot?.fast_period ?? 0), [bot?.fast_period, chartCandles]);
  const slowSma = useMemo(() => buildSmaData(chartCandles, bot?.slow_period ?? 0), [bot?.slow_period, chartCandles]);
  const vwap = useMemo(
    () => buildVwapData(visibleCandles, { sessionStartTime: chartConfig?.trading_start_time, sessionTimeZone: EASTERN_TIME_ZONE }),
    [chartConfig?.trading_start_time, visibleCandles],
  );
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
  const chartViewportKey = `${bot?.id ?? "none"}:${bot?.contract_id ?? ""}:${selectedTimeframeId}`;

  useEffect(() => {
    candlesRef.current = candles;
  }, [candles]);

  useEffect(() => {
    hoverCandlesByTimeRef.current = hoverCandlesByTime;
    if (hoveredCandleTimeRef.current === null) {
      return;
    }

    const nextCandle = hoverCandlesByTime.get(hoveredCandleTimeRef.current) ?? null;
    if (!nextCandle) {
      hoveredCandleTimeRef.current = null;
      setHoveredCandle(null);
      return;
    }

    setHoveredCandle(nextCandle);
  }, [hoverCandlesByTime]);

  useEffect(() => {
    chartCandlesRef.current = chartCandles;
    setDrawingOverlayRevision((current) => current + 1);
  }, [chartCandles]);

  useEffect(() => {
    drawingsRef.current = drawings;
    if (selectedDrawingId && !drawings.some((drawing) => drawing.id === selectedDrawingId)) {
      selectedDrawingIdRef.current = null;
      setSelectedDrawingId(null);
    }
  }, [drawings, selectedDrawingId]);

  useEffect(() => {
    selectedDrawingIdRef.current = selectedDrawingId;
    setDrawingOverlayRevision((current) => current + 1);
  }, [selectedDrawingId]);

  useEffect(() => {
    drawingToolRef.current = drawingTool;
    const placementState = drawingPlacementStateRef.current;
    if (placementState && (drawingTool === "cursor" || placementState.kind !== drawingTool)) {
      drawingPlacementStateRef.current = null;
      drawingEditStateRef.current = null;
      drawingDraftRef.current = null;
      drawingAnchorPreviewRef.current = null;
      setDrawingDraft(null);
      setDrawingAnchorPreview(null);
      chartHandlesRef.current?.chart.applyOptions({ handleScroll: true, handleScale: true });
      chartHandlesRef.current?.chart.clearCrosshairPosition();
    } else if (drawingTool === "cursor") {
      drawingAnchorPreviewRef.current = null;
      lastDrawingPanePointRef.current = null;
      setDrawingAnchorPreview(null);
      chartHandlesRef.current?.chart.clearCrosshairPosition();
    }
    if (containerRef.current && !liquidityDragStateRef.current && !drawingPlacementStateRef.current) {
      containerRef.current.style.cursor = drawingTool === "cursor" ? "" : "crosshair";
    }
  }, [drawingTool]);

  useEffect(() => {
    if ((!bot || chartCandles.length === 0) && drawingTool !== "cursor") {
      setDrawingTool("cursor");
    }
  }, [bot, chartCandles.length, drawingTool]);

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
    if (drawingPlacementStateRef.current) {
      chartHandlesRef.current?.chart.applyOptions({ handleScroll: true, handleScale: true });
    }
    if (drawingEditStateRef.current) {
      const pointerId = drawingEditStateRef.current.pointerId;
      chartHandlesRef.current?.chart.applyOptions({ handleScroll: true, handleScale: true });
      releaseChartPointerCapture(containerRef.current, pointerId);
    }
    liquidityDragStateRef.current = null;
    drawingPlacementStateRef.current = null;
    drawingEditStateRef.current = null;
    drawingDraftRef.current = null;
    drawingAnchorPreviewRef.current = null;
    lastDrawingPanePointRef.current = null;
    selectedDrawingIdRef.current = null;
    setDrawingDraft(null);
    setDrawingAnchorPreview(null);
    setSelectedDrawingId(null);
    drawingsRef.current = [];
    setDrawings([]);
    chartHandlesRef.current?.chart.clearCrosshairPosition();
    setDrawingOverlayRevision((current) => current + 1);
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
    const eventSurface = container.parentElement instanceof HTMLElement ? container.parentElement : container;

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
      localization: {
        locale: "en-US",
        timeFormatter: formatEasternCrosshairTime,
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
        tickMarkFormatter: formatEasternTickMark,
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
    const vwapSeries = chart.addSeries(LineSeries, {
      color: "rgb(244,114,182)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const markers = createSeriesMarkers(candleSeries);
    chartHandlesRef.current = { chart, candleSeries, fastSeries, slowSeries, vwapSeries, markers };

    const clearHoveredCandle = () => {
      if (hoveredCandleTimeRef.current !== null) {
        hoveredCandleTimeRef.current = null;
        setHoveredCandle(null);
      }
    };

    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param.point) {
        clearHoveredCandle();
        return;
      }

      const candleData = param.seriesData.get(candleSeries);
      if (!isCrosshairCandlestickData(candleData)) {
        clearHoveredCandle();
        return;
      }

      const nextCandle = hoverCandleFromCandlestickData(candleData, hoverCandlesByTimeRef.current);
      if (!nextCandle) {
        clearHoveredCandle();
        return;
      }

      const nextTime = Number(nextCandle.time);
      if (hoveredCandleTimeRef.current === nextTime) {
        return;
      }

      hoveredCandleTimeRef.current = nextTime;
      setHoveredCandle(nextCandle);
    };

    const requestDrawingOverlayUpdate = () => {
      setDrawingOverlayRevision((current) => current + 1);
    };

    const resize = () => {
      chart.resize(Math.max(container.clientWidth, 320), Math.max(container.clientHeight, 320));
      requestDrawingOverlayUpdate();
    };
    resize();
    chart.subscribeCrosshairMove(handleCrosshairMove);
    chart.timeScale().subscribeVisibleLogicalRangeChange(requestDrawingOverlayUpdate);

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

    const setDragCursor = (active: boolean) => {
      container.style.cursor = active ? "ns-resize" : drawingToolRef.current === "cursor" ? "" : "crosshair";
    };

    const cursorForDrawingHitTarget = (hitTarget: DrawingHitTarget) => {
      if (hitTarget.mode === "body") {
        return "move";
      }
      if (hitTarget.mode === "left" || hitTarget.mode === "right") {
        return "ew-resize";
      }
      return "grab";
    };

    const setIdleCursor = (event?: PointerEvent) => {
      if (liquidityDragStateRef.current || drawingPlacementStateRef.current || drawingEditStateRef.current) {
        return;
      }

      if (drawingToolRef.current !== "cursor") {
        container.style.cursor = "crosshair";
        return;
      }

      if (event) {
        const drawingHitTarget = findDrawingHitTargetAtPointer(event);
        if (drawingHitTarget) {
          container.style.cursor = cursorForDrawingHitTarget(drawingHitTarget);
          return;
        }
      }

      container.style.cursor = event && findLiquidityLineAtPointer(event) !== null ? "ns-resize" : "";
    };

    const createDrawingId = () => {
      drawingSequenceRef.current += 1;
      return `chart-drawing-${Date.now()}-${drawingSequenceRef.current}`;
    };

    const drawingPointFromPanePointForChart = (panePoint: ChartPanePoint): DrawingPoint | null =>
      drawingPointFromPanePoint(panePoint, chart, candleSeries);

    const resolveDrawingPointFromPanePoint = (panePoint: ChartPanePoint, ctrlKey: boolean): DrawingPoint | null => {
      const point = ctrlKey
        ? snapDrawingPointToCandle(panePoint, chart, candleSeries, chartCandlesRef.current) ?? drawingPointFromPanePointForChart(panePoint)
        : drawingPointFromPanePointForChart(panePoint);
      return point ? normalizeDrawingPoint(point) : null;
    };

    const clearDrawingAnchorPreview = () => {
      if (drawingAnchorPreviewRef.current) {
        drawingAnchorPreviewRef.current = null;
        setDrawingAnchorPreview(null);
      }
      chart.clearCrosshairPosition();
    };

    const updateDrawingAnchorPreview = (panePoint: ChartPanePoint, ctrlKey: boolean) => {
      if (drawingToolRef.current === "cursor" || !ctrlKey) {
        clearDrawingAnchorPreview();
        return;
      }

      const snappedPoint = snapDrawingPointToCandle(panePoint, chart, candleSeries, chartCandlesRef.current);
      if (!snappedPoint || snappedPoint.time === null) {
        clearDrawingAnchorPreview();
        return;
      }
      const snappedTime = snappedPoint.time;

      const preview = { point: snappedPoint };
      if (!drawingAnchorPreviewRef.current || !isSameDrawingPoint(drawingAnchorPreviewRef.current.point, snappedPoint)) {
        drawingAnchorPreviewRef.current = preview;
        setDrawingAnchorPreview(preview);
      }
      chart.setCrosshairPosition(snappedPoint.price, snappedTime, candleSeries);
      window.requestAnimationFrame(() => {
        if (drawingAnchorPreviewRef.current && isSameDrawingPoint(drawingAnchorPreviewRef.current.point, snappedPoint)) {
          chart.setCrosshairPosition(snappedPoint.price, snappedTime, candleSeries);
        }
      });
    };

    const findDrawingHitTargetAtPointer = (event: PointerEvent): DrawingHitTarget | null => {
      const panePoint = chartPanePointFromPointerEvent(event, container, chart);
      if (!panePoint) {
        return null;
      }

      return findDrawingHitTargetAtPanePoint(panePoint, chartHandlesRef.current, drawingsRef.current);
    };

    const updateDrawing = (drawing: DrawingShape) => {
      setDrawings((current) => {
        const nextDrawings = current.map((currentDrawing) => (currentDrawing.id === drawing.id ? drawing : currentDrawing));
        drawingsRef.current = nextDrawings;
        return nextDrawings;
      });
    };

    const pointFromShiftedDrawingPoint = (point: DrawingPoint, deltaX: number, deltaY: number): DrawingPoint | null => {
      const panePoint = drawingPointToPanePoint(point, chart, candleSeries);
      if (!panePoint) {
        return null;
      }

      return drawingPointFromPanePointForChart({ x: panePoint.x + deltaX, y: panePoint.y + deltaY });
    };

    const resolveEditedEndpoint = (
      drawing: DrawingShape,
      fixedPoint: DrawingPoint,
      rawMovingPoint: DrawingPoint,
      modifiers: DrawingModifiers,
    ): DrawingPoint => {
      if (!modifiers.shiftKey) {
        return rawMovingPoint;
      }

      const fixedPanePoint = drawingPointToPanePoint(fixedPoint, chart, candleSeries);
      const movingPanePoint = drawingPointToPanePoint(rawMovingPoint, chart, candleSeries);
      if (!fixedPanePoint || !movingPanePoint) {
        return rawMovingPoint;
      }

      const constrainedPanePoint = constrainDrawingEndPoint(drawing.kind, fixedPanePoint, movingPanePoint);
      return normalizeDrawingPoint(drawingPointFromPanePointForChart(constrainedPanePoint) ?? rawMovingPoint);
    };

    const resizeRectangleSide = (
      drawing: DrawingShape,
      mode: Extract<DrawingEditMode, "left" | "right">,
      panePoint: ChartPanePoint,
      ctrlKey: boolean,
    ): DrawingShape | null => {
      const startPanePoint = drawingPointToPanePoint(drawing.start, chart, candleSeries);
      const endPanePoint = drawingPointToPanePoint(drawing.end, chart, candleSeries);
      const rawMovingPoint = resolveDrawingPointFromPanePoint(panePoint, ctrlKey);
      if (!startPanePoint || !endPanePoint || !rawMovingPoint) {
        return null;
      }

      const startControlsMovingSide =
        mode === "left" ? startPanePoint.x <= endPanePoint.x : startPanePoint.x > endPanePoint.x;
      const originalMovingPoint = startControlsMovingSide ? drawing.start : drawing.end;
      const movingPoint = normalizeDrawingPoint({
        logical: rawMovingPoint.logical,
        time: rawMovingPoint.time,
        price: originalMovingPoint.price,
      });

      return startControlsMovingSide ? { ...drawing, start: movingPoint } : { ...drawing, end: movingPoint };
    };

    const applyDrawingEdit = (editState: DrawingEditState, panePoint: ChartPanePoint, modifiers: DrawingModifiers) => {
      if (editState.mode === "body") {
        const deltaX = panePoint.x - editState.originPanePoint.x;
        const deltaY = panePoint.y - editState.originPanePoint.y;
        const start = pointFromShiftedDrawingPoint(editState.originalDrawing.start, deltaX, deltaY);
        const end = pointFromShiftedDrawingPoint(editState.originalDrawing.end, deltaX, deltaY);
        if (!start || !end) {
          return;
        }
        updateDrawing({ ...editState.originalDrawing, start, end });
        return;
      }

      if (editState.originalDrawing.kind === "rectangle" && (editState.mode === "left" || editState.mode === "right")) {
        const resizedDrawing = resizeRectangleSide(editState.originalDrawing, editState.mode, panePoint, modifiers.ctrlKey);
        if (resizedDrawing) {
          updateDrawing(resizedDrawing);
        }
        return;
      }

      const rawMovingPoint = resolveDrawingPointFromPanePoint(panePoint, modifiers.ctrlKey);
      if (!rawMovingPoint) {
        return;
      }

      if (editState.mode === "start") {
        const start = resolveEditedEndpoint(editState.originalDrawing, editState.originalDrawing.end, rawMovingPoint, modifiers);
        updateDrawing({ ...editState.originalDrawing, start });
        return;
      }

      const end = resolveEditedEndpoint(editState.originalDrawing, editState.originalDrawing.start, rawMovingPoint, modifiers);
      updateDrawing({ ...editState.originalDrawing, end });
    };

    const beginDrawingEdit = (event: PointerEvent, hitTarget: DrawingHitTarget): boolean => {
      const panePoint = chartPanePointFromPointerEvent(event, container, chart);
      const drawing = drawingsRef.current.find((currentDrawing) => currentDrawing.id === hitTarget.id);
      if (!panePoint || !drawing) {
        return false;
      }

      event.preventDefault();
      event.stopPropagation();
      eventSurface.setPointerCapture(event.pointerId);
      drawingEditStateRef.current = {
        id: hitTarget.id,
        mode: hitTarget.mode,
        pointerId: event.pointerId,
        originPanePoint: panePoint,
        originalDrawing: drawing,
      };
      selectedDrawingIdRef.current = hitTarget.id;
      setSelectedDrawingId(hitTarget.id);
      chart.applyOptions({ handleScroll: false, handleScale: false });
      container.style.cursor = cursorForDrawingHitTarget(hitTarget);
      return true;
    };

    const endDrawingEdit = (event?: PointerEvent) => {
      const editState = drawingEditStateRef.current;
      if (!editState) {
        return;
      }

      const pointerId = event?.pointerId ?? editState.pointerId;
      releaseChartPointerCapture(container, pointerId);
      drawingEditStateRef.current = null;
      chart.applyOptions({ handleScroll: true, handleScale: true });
      setIdleCursor(event);
    };

    const resolveDrawingEndPoint = (placementState: DrawingPlacementState, modifiers: DrawingModifiers): DrawingPoint | null => {
      const basePoint = resolveDrawingPointFromPanePoint(placementState.lastPanePoint, modifiers.ctrlKey);
      if (!basePoint) {
        return null;
      }

      if (!modifiers.shiftKey) {
        return basePoint;
      }

      const startPanePoint = drawingPointToPanePoint(placementState.start, chart, candleSeries);
      const endPanePoint = drawingPointToPanePoint(basePoint, chart, candleSeries);
      if (!startPanePoint || !endPanePoint) {
        return basePoint;
      }

      const constrainedPanePoint = constrainDrawingEndPoint(placementState.kind, startPanePoint, endPanePoint);
      return normalizeDrawingPoint(drawingPointFromPanePointForChart(constrainedPanePoint) ?? basePoint);
    };

    const updateDrawingDraft = (placementState: DrawingPlacementState, modifiers: DrawingModifiers) => {
      const end = resolveDrawingEndPoint(placementState, modifiers);
      if (!end) {
        return;
      }

      const draft = {
        id: placementState.id,
        kind: placementState.kind,
        start: placementState.start,
        end,
      };
      drawingDraftRef.current = draft;
      setDrawingDraft(draft);
    };

    const finishDrawingPlacement = (event: PointerEvent | undefined, commit: boolean) => {
      const placementState = drawingPlacementStateRef.current;
      if (!placementState) {
        return;
      }

      const draft = drawingDraftRef.current;
      drawingPlacementStateRef.current = null;
      drawingDraftRef.current = null;
      setDrawingDraft(null);
      chart.applyOptions({ handleScroll: true, handleScale: true });
      clearDrawingAnchorPreview();

      if (commit && draft && isMeaningfulDrawing(draft, chart, candleSeries)) {
        const drawing = {
          id: draft.id,
          kind: draft.kind,
          start: draft.start,
          end: draft.end,
        };
        setDrawings((current) => {
          const nextDrawings = [...current, drawing];
          drawingsRef.current = nextDrawings;
          return nextDrawings;
        });
        selectedDrawingIdRef.current = drawing.id;
        setSelectedDrawingId(drawing.id);
        drawingToolRef.current = "cursor";
        setDrawingTool("cursor");
      }

      setIdleCursor(event);
    };

    const beginDrawingPlacement = (event: PointerEvent, kind: DrawingKind): boolean => {
      const panePoint = chartPanePointFromPointerEvent(event, container, chart);
      if (!panePoint) {
        return false;
      }

      lastDrawingPanePointRef.current = panePoint;
      updateDrawingAnchorPreview(panePoint, event.ctrlKey);
      const start = resolveDrawingPointFromPanePoint(panePoint, event.ctrlKey);
      if (!start) {
        return false;
      }

      event.preventDefault();
      event.stopPropagation();
      selectedDrawingIdRef.current = null;
      setSelectedDrawingId(null);
      const placementState = {
        id: createDrawingId(),
        kind,
        start,
        lastPanePoint: panePoint,
      };
      drawingPlacementStateRef.current = placementState;
      chart.applyOptions({ handleScroll: false, handleScale: false });
      container.style.cursor = "crosshair";
      updateDrawingDraft(placementState, { ctrlKey: event.ctrlKey, shiftKey: event.shiftKey });
      return true;
    };

    const endLiquidityDrag = (event?: PointerEvent) => {
      const dragState = liquidityDragStateRef.current;
      if (!dragState) {
        return;
      }

      if (event) {
        releaseChartPointerCapture(container, event.pointerId);
      }
      liquidityDragStateRef.current = null;
      chart.applyOptions({ handleScroll: true, handleScale: true });
      setIdleCursor(event);
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (isChartOverlayControlEventTarget(event.target)) {
        return;
      }

      if (event.button !== 0) {
        return;
      }

      const drawingTool = drawingToolRef.current;
      if (drawingTool !== "cursor") {
        const placementState = drawingPlacementStateRef.current;
        if (placementState) {
          const panePoint = chartPanePointFromPointerEvent(event, container, chart);
          if (!panePoint) {
            return;
          }
          event.preventDefault();
          event.stopPropagation();
          lastDrawingPanePointRef.current = panePoint;
          updateDrawingAnchorPreview(panePoint, event.ctrlKey);
          placementState.lastPanePoint = panePoint;
          updateDrawingDraft(placementState, { ctrlKey: event.ctrlKey, shiftKey: event.shiftKey });
          finishDrawingPlacement(event, true);
          return;
        }

        if (chartCandlesRef.current.length > 0 && beginDrawingPlacement(event, drawingTool)) {
          return;
        }
      }

      const drawingHitTarget = findDrawingHitTargetAtPointer(event);
      if (drawingHitTarget && beginDrawingEdit(event, drawingHitTarget)) {
        return;
      }

      if (selectedDrawingIdRef.current) {
        selectedDrawingIdRef.current = null;
        setSelectedDrawingId(null);
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
      eventSurface.setPointerCapture(event.pointerId);
      liquidityDragStateRef.current = { side, pointerId: event.pointerId };
      chart.applyOptions({ handleScroll: false, handleScale: false });
      setDragCursor(true);
      applyDraggedLiquidityPrice(side, price);
    };

    const handlePointerMove = (event: PointerEvent) => {
      const editState = drawingEditStateRef.current;
      if (editState) {
        if (event.pointerId !== editState.pointerId) {
          return;
        }

        const panePoint = chartPanePointFromPointerEvent(event, container, chart, true);
        if (!panePoint) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();
        applyDrawingEdit(editState, panePoint, { ctrlKey: event.ctrlKey, shiftKey: event.shiftKey });
        return;
      }

      const placementState = drawingPlacementStateRef.current;
      if (placementState) {
        const panePoint = chartPanePointFromPointerEvent(event, container, chart, true);
        if (!panePoint) {
          return;
        }

        event.preventDefault();
        event.stopPropagation();
        lastDrawingPanePointRef.current = panePoint;
        updateDrawingAnchorPreview(panePoint, event.ctrlKey);
        placementState.lastPanePoint = panePoint;
        updateDrawingDraft(placementState, { ctrlKey: event.ctrlKey, shiftKey: event.shiftKey });
        return;
      }

      if (drawingToolRef.current !== "cursor") {
        const panePoint = chartPanePointFromPointerEvent(event, container, chart);
        if (panePoint) {
          lastDrawingPanePointRef.current = panePoint;
          updateDrawingAnchorPreview(panePoint, event.ctrlKey);
        } else {
          lastDrawingPanePointRef.current = null;
          clearDrawingAnchorPreview();
        }
      }

      const dragState = liquidityDragStateRef.current;
      if (!dragState) {
        setIdleCursor(event);
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
      if (drawingEditStateRef.current?.pointerId === event.pointerId) {
        event.preventDefault();
        event.stopPropagation();
        endDrawingEdit(event);
        return;
      }

      if (drawingPlacementStateRef.current) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }

      if (liquidityDragStateRef.current?.pointerId !== event.pointerId) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      endLiquidityDrag(event);
    };

    const handlePointerCancel = (event: PointerEvent) => {
      if (drawingEditStateRef.current?.pointerId === event.pointerId) {
        endDrawingEdit(event);
        return;
      }

      if (drawingPlacementStateRef.current) {
        finishDrawingPlacement(event, false);
        return;
      }

      if (liquidityDragStateRef.current?.pointerId === event.pointerId) {
        endLiquidityDrag(event);
      }
    };

    const handlePointerLeave = () => {
      clearHoveredCandle();
      lastDrawingPanePointRef.current = null;
      clearDrawingAnchorPreview();
      if (!liquidityDragStateRef.current && !drawingPlacementStateRef.current && !drawingEditStateRef.current) {
        setIdleCursor();
      }
    };

    const handleModifierKeyChange = (event: KeyboardEvent) => {
      if ((event.key === "Backspace" || event.key === "Delete") && selectedDrawingIdRef.current && !isEditableEventTarget(event.target)) {
        event.preventDefault();
        const selectedDrawingId = selectedDrawingIdRef.current;
        selectedDrawingIdRef.current = null;
        setSelectedDrawingId(null);
        setDrawings((current) => {
          const nextDrawings = current.filter((drawing) => drawing.id !== selectedDrawingId);
          drawingsRef.current = nextDrawings;
          return nextDrawings;
        });
        return;
      }

      const editState = drawingEditStateRef.current;
      if (editState) {
        if (event.key === "Escape") {
          event.preventDefault();
          updateDrawing(editState.originalDrawing);
          endDrawingEdit(undefined);
        }
        return;
      }

      const placementState = drawingPlacementStateRef.current;
      if (!placementState) {
        if ((event.key === "Control" || event.key === "Shift") && lastDrawingPanePointRef.current) {
          updateDrawingAnchorPreview(lastDrawingPanePointRef.current, event.ctrlKey);
        }
        return;
      }

      if (event.key === "Escape") {
        finishDrawingPlacement(undefined, false);
        return;
      }

      if (event.key === "Shift" || event.key === "Control") {
        updateDrawingDraft(placementState, { ctrlKey: event.ctrlKey, shiftKey: event.shiftKey });
      }
    };

    const pointerListenerOptions: AddEventListenerOptions = { capture: true };
    eventSurface.addEventListener("pointerdown", handlePointerDown, pointerListenerOptions);
    eventSurface.addEventListener("pointermove", handlePointerMove, pointerListenerOptions);
    eventSurface.addEventListener("pointerup", handlePointerUp, pointerListenerOptions);
    eventSurface.addEventListener("pointercancel", handlePointerCancel, pointerListenerOptions);
    eventSurface.addEventListener("pointerleave", handlePointerLeave, pointerListenerOptions);
    window.addEventListener("keydown", handleModifierKeyChange);
    window.addEventListener("keyup", handleModifierKeyChange);

    return () => {
      eventSurface.removeEventListener("pointerdown", handlePointerDown, pointerListenerOptions);
      eventSurface.removeEventListener("pointermove", handlePointerMove, pointerListenerOptions);
      eventSurface.removeEventListener("pointerup", handlePointerUp, pointerListenerOptions);
      eventSurface.removeEventListener("pointercancel", handlePointerCancel, pointerListenerOptions);
      eventSurface.removeEventListener("pointerleave", handlePointerLeave, pointerListenerOptions);
      window.removeEventListener("keydown", handleModifierKeyChange);
      window.removeEventListener("keyup", handleModifierKeyChange);
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(requestDrawingOverlayUpdate);
      container.style.cursor = "";
      resizeObserver?.disconnect();
      if (resizeObserver === null) {
        window.removeEventListener("resize", resize);
      }
      const editState = drawingEditStateRef.current;
      if (editState) {
        releaseChartPointerCapture(container, editState.pointerId);
      }
      markers.detach();
      chart.remove();
      chartHandlesRef.current = null;
      livePriceLineRef.current = null;
      liquidityPriceLinesRef.current = {};
      liquidityLevelsRef.current = [];
      liquidityDragStateRef.current = null;
      drawingPlacementStateRef.current = null;
      drawingEditStateRef.current = null;
      drawingDraftRef.current = null;
      drawingAnchorPreviewRef.current = null;
      lastDrawingPanePointRef.current = null;
      hoveredCandleTimeRef.current = null;
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
    handles.vwapSeries.setData(vwap);
    handles.markers.setMarkers(signalMarkers);
    setDrawingOverlayRevision((current) => current + 1);
  }, [chartConfig?.timeframe_unit, chartCandles, fastSma, signalMarkers, slowSma, vwap]);

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
    if (!handles || !bot || closedChartCandles.length === 0) {
      return;
    }

    if (fittedViewportKeyRef.current === chartViewportKey) {
      return;
    }

    handles.chart.timeScale().fitContent();
    fittedViewportKeyRef.current = chartViewportKey;
  }, [bot, chartViewportKey, closedChartCandles.length]);

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
      lineVisible: false,
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
  const lastLoadedText = lastLoadedAt ? `Loaded ${lastLoadedFormatter.format(lastLoadedAt)} ET` : null;
  const livePriceText = livePrice !== null ? `${liveCandle?.is_partial ? "Live" : "Last"} ${priceFormatter.format(livePrice)}` : null;
  const livePriceTitle = liveCandle ? `Price timestamp ${lastLoadedFormatter.format(new Date(liveCandle.timestamp))} ET` : undefined;
  const computedLiquidityButtonTitle =
    liquidityLevels.length > 0
      ? "Move Buy liq and Sell liq to their computed swing liquidity levels"
      : "No computed liquidity levels are available yet";
  const drawingToolsDisabled = !bot || chartCandles.length === 0;
  const fitPriceScaleToVisibleRange = useCallback(() => {
    const handles = chartHandlesRef.current;
    if (!handles || chartCandlesRef.current.length === 0) {
      return;
    }

    handles.candleSeries.priceScale().setAutoScale(true);
    setDrawingOverlayRevision((current) => current + 1);
  }, []);
  const activeOhlcCandle = hoveredCandle ?? latestOhlcCandle;
  const drawingOverlay = useMemo(
    () =>
      buildDrawingOverlayState(
        chartHandlesRef.current,
        drawings,
        drawingDraft,
        drawingAnchorPreview,
        selectedDrawingId,
        drawingOverlayRevision,
        liveCandle,
      ),
    [drawingAnchorPreview, drawingDraft, drawingOverlayRevision, drawings, liveCandle, selectedDrawingId],
  );

  return (
    <Card className="flex h-full min-h-[620px] flex-col">
      <CardHeader className="shrink-0 !space-y-0">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <CardTitle>Signal Chart</CardTitle>
            <CardDescription className="mt-1">{subtitle}</CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2 md:justify-end">
            {livePriceText ? (
              <span
                className="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-md border border-cyan-400/30 bg-cyan-400/10 px-2.5 text-xs font-semibold text-cyan-100"
                title={livePriceTitle}
              >
                <span className="h-2 w-2 rounded-full bg-cyan-300" />
                {livePriceText}
              </span>
            ) : bot && livePriceError ? (
              <span
                className="inline-flex h-8 items-center rounded-md border border-amber-400/30 bg-amber-500/10 px-2.5 text-xs font-semibold text-amber-200"
                title={livePriceError}
              >
                Live price unavailable
              </span>
            ) : null}
            {lastLoadedText ? (
              <span className="inline-flex h-8 items-center whitespace-nowrap rounded-md border border-slate-800 bg-slate-950/65 px-2.5 text-xs text-slate-400">
                {lastLoadedText}
              </span>
            ) : null}
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950/45 p-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
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
            <div className="inline-flex h-8 overflow-hidden rounded-md border border-slate-800 bg-slate-950/70" aria-label="Chart drawing tools">
              <ChartToolButton
                label="Cursor"
                active={drawingTool === "cursor"}
                onClick={() => setDrawingTool("cursor")}
              >
                <CursorToolIcon />
              </ChartToolButton>
              <ChartToolButton
                label="Draw line"
                active={drawingTool === "line"}
                disabled={drawingToolsDisabled}
                onClick={() => setDrawingTool((current) => (current === "line" ? "cursor" : "line"))}
              >
                <LineToolIcon />
              </ChartToolButton>
              <ChartToolButton
                label="Draw rectangle"
                active={drawingTool === "rectangle"}
                disabled={drawingToolsDisabled}
                onClick={() => setDrawingTool((current) => (current === "rectangle" ? "cursor" : "rectangle"))}
              >
                <RectangleToolIcon />
              </ChartToolButton>
              <ChartToolButton
                label="Clear drawings"
                disabled={drawings.length === 0 && !drawingDraft}
                onClick={() => {
                  const editState = drawingEditStateRef.current;
                  if (editState) {
                    releaseChartPointerCapture(containerRef.current, editState.pointerId);
                  }
                  drawingPlacementStateRef.current = null;
                  drawingEditStateRef.current = null;
                  drawingDraftRef.current = null;
                  drawingAnchorPreviewRef.current = null;
                  selectedDrawingIdRef.current = null;
                  setDrawingDraft(null);
                  setDrawingAnchorPreview(null);
                  setSelectedDrawingId(null);
                  drawingsRef.current = [];
                  setDrawings([]);
                  chartHandlesRef.current?.chart.applyOptions({ handleScroll: true, handleScale: true });
                  chartHandlesRef.current?.chart.clearCrosshairPosition();
                  setDrawingOverlayRevision((current) => current + 1);
                }}
              >
                <ClearDrawingsIcon />
              </ChartToolButton>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:justify-end">
            <Button
              variant="secondary"
              size="sm"
              className="disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => setLiquidityPriceOverrides({})}
              disabled={!bot || loading || liquidityLevels.length === 0}
              title={computedLiquidityButtonTitle}
            >
              <ComputeLiquidityIcon />
              <span>Compute liq</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => {
                void loadCandles({ silent: true, forceRefresh: true });
                void loadLivePrice({ force: true });
              }}
              disabled={!bot || loading || refreshing}
            >
              <RefreshIcon />
              <span>{refreshing ? "Refreshing" : "Refresh"}</span>
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-slate-800 bg-slate-950/35 px-3 py-2 text-xs text-slate-400">
          <LegendDot className="bg-cyan-400" label={`Fast SMA ${bot?.fast_period ?? "-"}`} />
          <LegendDot className="bg-yellow-300" label={`Slow SMA ${bot?.slow_period ?? "-"}`} />
          <LegendDot className="bg-pink-400" label="VWAP" />
          <LegendDot className="bg-emerald-500" label="Buy" />
          <LegendDot className="bg-rose-500" label="Sell" />
          <LegendLine className="border-emerald-500" label="Buy-side liquidity" />
          <LegendLine className="border-rose-500" label="Sell-side liquidity" />
        </div>
        <div className="relative min-h-[420px] flex-1 overflow-hidden rounded-xl border border-slate-800 bg-slate-950/45 md:min-h-[560px] xl:min-h-0">
          <div ref={containerRef} className="h-full w-full" />
          <OhlcReadout candle={activeOhlcCandle} />
          <DrawingOverlay overlay={drawingOverlay} />
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
          <button
            type="button"
            data-chart-overlay-control="true"
            aria-label="Fit y-axis to visible candles"
            title="Fit y-axis to visible candles"
            disabled={!bot || chartCandles.length === 0}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              fitPriceScaleToVisibleRange();
            }}
            className="absolute bottom-3 right-3 z-30 inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-700 bg-slate-950/85 px-2.5 text-xs font-semibold text-slate-200 shadow-lg shadow-slate-950/30 backdrop-blur transition hover:border-cyan-400/50 hover:bg-slate-900 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-45"
          >
            <FitChartIcon />
            <span>Fit</span>
          </button>
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

function formatEasternCrosshairTime(time: Time): string {
  const date = dateFromChartTime(time);
  return date ? `${chartCrosshairTimeFormatter.format(date)} ET` : String(time);
}

function formatEasternTickMark(time: Time, tickMarkType: TickMarkType): string | null {
  const date = dateFromChartTime(time);
  if (!date) {
    return null;
  }

  switch (tickMarkType) {
    case TickMarkType.Year:
      return chartAxisYearFormatter.format(date);
    case TickMarkType.Month:
      return chartAxisMonthFormatter.format(date);
    case TickMarkType.DayOfMonth:
      return chartAxisDayFormatter.format(date);
    case TickMarkType.TimeWithSeconds:
      return compactMeridiem(chartAxisTimeWithSecondsFormatter.format(date));
    case TickMarkType.Time:
    default:
      return compactMeridiem(chartAxisTimeFormatter.format(date));
  }
}

function dateFromChartTime(time: Time): Date | null {
  if (typeof time === "number") {
    return new Date(Number(time) * 1000);
  }

  if (typeof time === "string") {
    const timestampMs = Date.parse(time);
    return Number.isFinite(timestampMs) ? new Date(timestampMs) : null;
  }

  const timestampMs = Date.UTC(time.year, time.month - 1, time.day);
  return Number.isFinite(timestampMs) ? new Date(timestampMs) : null;
}

function compactMeridiem(value: string): string {
  return value.replace(/\s(AM|PM)$/, "$1");
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

function buildHoverCandleMap(candles: ProjectXMarketCandle[]): Map<number, HoveredCandle> {
  const sourceCandlesByTime = new Map<number, ProjectXMarketCandle>();
  const byTime = new Map<number, HoveredCandle>();

  for (const candle of candles) {
    if (!isRenderableMarketCandle(candle)) {
      continue;
    }

    const time = toUtcTimestamp(candle.timestamp);
    if (time === null) {
      continue;
    }

    sourceCandlesByTime.set(Number(time), candle);
  }

  const chartCandles = buildCandlestickData(candles);

  chartCandles.forEach((candle, index) => {
    const sourceCandle = sourceCandlesByTime.get(Number(candle.time)) ?? null;
    const previousCandle = index > 0 ? chartCandles[index - 1] : null;
    byTime.set(Number(candle.time), {
      time: candle.time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      previousClose: previousCandle?.close ?? null,
      volume: sourceCandle && Number.isFinite(sourceCandle.volume) ? sourceCandle.volume : 0,
      isPartial: sourceCandle?.is_partial ?? false,
    });
  });

  return byTime;
}

function getLatestHoveredCandle(candlesByTime: Map<number, HoveredCandle>): HoveredCandle | null {
  let latestCandle: HoveredCandle | null = null;
  for (const candle of candlesByTime.values()) {
    if (!latestCandle || Number(candle.time) > Number(latestCandle.time)) {
      latestCandle = candle;
    }
  }
  return latestCandle;
}

function hoverCandleFromCandlestickData(
  candleData: CandlestickData<UTCTimestamp>,
  candlesByTime: Map<number, HoveredCandle>,
): HoveredCandle | null {
  const time = Number(candleData.time);
  if (!Number.isFinite(time)) {
    return null;
  }

  return (
    candlesByTime.get(time) ?? {
      time: candleData.time,
      open: candleData.open,
      high: candleData.high,
      low: candleData.low,
      close: candleData.close,
      previousClose: null,
      volume: 0,
      isPartial: false,
    }
  );
}

function isCrosshairCandlestickData(data: unknown): data is CandlestickData<UTCTimestamp> {
  if (!data || typeof data !== "object") {
    return false;
  }

  const candle = data as {
    time?: unknown;
    open?: unknown;
    high?: unknown;
    low?: unknown;
    close?: unknown;
  };
  return (
    Number.isFinite(Number(candle.time)) &&
    [candle.open, candle.high, candle.low, candle.close].every(
      (value) => typeof value === "number" && Number.isFinite(value),
    )
  );
}

function chartPanePointFromPointerEvent(
  event: PointerEvent,
  container: HTMLDivElement,
  chart: IChartApi,
  clamp = false,
): ChartPanePoint | null {
  const paneSize = chart.paneSize();
  if (paneSize.width <= 0 || paneSize.height <= 0) {
    return null;
  }

  const rect = container.getBoundingClientRect();
  const rawX = event.clientX - rect.left;
  const rawY = event.clientY - rect.top;
  if (!clamp && (rawX < 0 || rawX > paneSize.width || rawY < 0 || rawY > paneSize.height)) {
    return null;
  }

  return {
    x: clampNumber(rawX, 0, paneSize.width),
    y: clampNumber(rawY, 0, paneSize.height),
  };
}

function drawingPointFromPanePoint(
  panePoint: ChartPanePoint,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
): DrawingPoint | null {
  const logical = chart.timeScale().coordinateToLogical(panePoint.x);
  const price = candleSeries.coordinateToPrice(panePoint.y);
  if (logical === null || typeof price !== "number" || !Number.isFinite(price)) {
    return null;
  }

  return normalizeDrawingPoint({ logical, time: null, price });
}

function drawingPointToPanePoint(
  point: DrawingPoint,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
): ChartPanePoint | null {
  const x = drawingPointXToPaneCoordinate(point, chart);
  const y = candleSeries.priceToCoordinate(point.price);
  if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }

  return { x, y };
}

function drawingPointXToPaneCoordinate(point: DrawingPoint, chart: IChartApi): number | null {
  if (point.time !== null) {
    const timeCoordinate = chart.timeScale().timeToCoordinate(point.time);
    if (timeCoordinate !== null && Number.isFinite(timeCoordinate)) {
      return timeCoordinate;
    }
  }

  const logical = Number(point.logical);
  if (!Number.isFinite(logical)) {
    return null;
  }

  const logicalCoordinate = chart.timeScale().logicalToCoordinate(logical as Logical);
  return logicalCoordinate !== null && Number.isFinite(logicalCoordinate) ? logicalCoordinate : null;
}

function snapDrawingPointToCandle(
  panePoint: ChartPanePoint,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
  candles: CandlestickData<UTCTimestamp>[],
): DrawingPoint | null {
  let closestPoint: DrawingPoint | null = null;
  let closestDistance = Number.POSITIVE_INFINITY;

  for (const candle of candles) {
    const x = chart.timeScale().timeToCoordinate(candle.time);
    if (x === null || !Number.isFinite(x)) {
      continue;
    }
    const logical = chart.timeScale().coordinateToLogical(x);
    if (logical === null) {
      continue;
    }

    for (const price of [candle.high, candle.low, candle.open, candle.close]) {
      if (!Number.isFinite(price)) {
        continue;
      }
      const y = candleSeries.priceToCoordinate(price);
      if (y === null || !Number.isFinite(y)) {
        continue;
      }

      const distance = Math.hypot(x - panePoint.x, y - panePoint.y);
      if (distance < closestDistance) {
        closestDistance = distance;
        closestPoint = normalizeDrawingPoint({ logical, time: candle.time, price });
      }
    }
  }

  return closestPoint;
}

function constrainDrawingEndPoint(kind: DrawingKind, start: ChartPanePoint, end: ChartPanePoint): ChartPanePoint {
  if (kind === "rectangle") {
    return constrainRectangleEndPoint(start, end);
  }
  return constrainLineEndPoint(start, end);
}

function constrainLineEndPoint(start: ChartPanePoint, end: ChartPanePoint): ChartPanePoint {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.hypot(dx, dy);
  if (length === 0) {
    return end;
  }

  const snappedAngle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
  return {
    x: start.x + Math.cos(snappedAngle) * length,
    y: start.y + Math.sin(snappedAngle) * length,
  };
}

function constrainRectangleEndPoint(start: ChartPanePoint, end: ChartPanePoint): ChartPanePoint {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const size = Math.max(Math.abs(dx), Math.abs(dy));
  return {
    x: start.x + Math.sign(dx || 1) * size,
    y: start.y + Math.sign(dy || 1) * size,
  };
}

function isMeaningfulDrawing(
  drawing: DrawingShape,
  chart: IChartApi,
  candleSeries: ISeriesApi<"Candlestick">,
): boolean {
  const start = drawingPointToPanePoint(drawing.start, chart, candleSeries);
  const end = drawingPointToPanePoint(drawing.end, chart, candleSeries);
  if (!start || !end) {
    return false;
  }

  if (drawing.kind === "line") {
    return Math.hypot(end.x - start.x, end.y - start.y) >= MIN_DRAWING_SIZE_PX;
  }

  return Math.abs(end.x - start.x) >= MIN_DRAWING_SIZE_PX && Math.abs(end.y - start.y) >= MIN_DRAWING_SIZE_PX;
}

function buildDrawingOverlayState(
  handles: ChartHandles | null,
  drawings: DrawingShape[],
  draft: DrawingDraft | null,
  anchorPreview: DrawingAnchorPreview | null,
  selectedDrawingId: string | null,
  revision: number,
  liveCandle: ProjectXMarketCandle | null,
): DrawingOverlayState {
  void revision;
  if (!handles) {
    return { width: 0, height: 0, items: [], anchor: null, livePriceLine: null };
  }

  const paneSize = handles.chart.paneSize();
  if (paneSize.width <= 0 || paneSize.height <= 0) {
    return { width: 0, height: 0, items: [], anchor: null, livePriceLine: null };
  }

  const items: RenderableDrawing[] = [];
  for (const drawing of drawings) {
    const item = toRenderableDrawing(drawing, handles, false, drawing.id === selectedDrawingId);
    if (item) {
      items.push(item);
    }
  }
  if (draft) {
    const item = toRenderableDrawing(draft, handles, true, false);
    if (item) {
      items.push(item);
    }
  }

  const anchor = anchorPreview ? drawingPointToPanePoint(anchorPreview.point, handles.chart, handles.candleSeries) : null;
  const livePriceLine = toRenderableLivePriceLine(liveCandle, handles, paneSize);
  return { width: paneSize.width, height: paneSize.height, items, anchor, livePriceLine };
}

function toRenderableLivePriceLine(
  liveCandle: ProjectXMarketCandle | null,
  handles: ChartHandles,
  paneSize: { width: number; height: number },
): RenderableLivePriceLine | null {
  if (!liveCandle || !Number.isFinite(liveCandle.close)) {
    return null;
  }

  const time = toUtcTimestamp(liveCandle.timestamp);
  if (time === null) {
    return null;
  }

  const x = handles.chart.timeScale().timeToCoordinate(time);
  const y = handles.candleSeries.priceToCoordinate(liveCandle.close);
  if (x === null || y === null || !Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }

  if (x < 0 || x >= paneSize.width - 1 || y < 0 || y > paneSize.height) {
    return null;
  }

  return {
    x1: x,
    x2: paneSize.width,
    y,
  };
}

function toRenderableDrawing(
  drawing: DrawingShape,
  handles: ChartHandles,
  isDraft: boolean,
  isSelected: boolean,
): RenderableDrawing | null {
  const start = drawingPointToPanePoint(drawing.start, handles.chart, handles.candleSeries);
  const end = drawingPointToPanePoint(drawing.end, handles.chart, handles.candleSeries);
  if (!start || !end) {
    return null;
  }

  return {
    id: drawing.id,
    kind: drawing.kind,
    isDraft,
    isSelected,
    x1: start.x,
    y1: start.y,
    x2: end.x,
    y2: end.y,
  };
}

function findDrawingHitTargetAtPanePoint(
  panePoint: ChartPanePoint,
  handles: ChartHandles | null,
  drawings: DrawingShape[],
): DrawingHitTarget | null {
  if (!handles) {
    return null;
  }

  for (let index = drawings.length - 1; index >= 0; index -= 1) {
    const item = toRenderableDrawing(drawings[index], handles, false, false);
    if (!item) {
      continue;
    }

    if (Math.hypot(panePoint.x - item.x1, panePoint.y - item.y1) <= DRAWING_ENDPOINT_HIT_RADIUS_PX) {
      return { id: item.id, mode: "start" };
    }
    if (Math.hypot(panePoint.x - item.x2, panePoint.y - item.y2) <= DRAWING_ENDPOINT_HIT_RADIUS_PX) {
      return { id: item.id, mode: "end" };
    }
    const rectangleSideMode = findRectangleSideResizeMode(panePoint, item);
    if (rectangleSideMode) {
      return { id: item.id, mode: rectangleSideMode };
    }
    if (isPointOnRenderableDrawing(panePoint, item)) {
      return { id: item.id, mode: "body" };
    }
  }

  return null;
}

function findRectangleSideResizeMode(point: ChartPanePoint, item: RenderableDrawing): Extract<DrawingEditMode, "left" | "right"> | null {
  if (item.kind !== "rectangle") {
    return null;
  }

  const left = Math.min(item.x1, item.x2);
  const right = Math.max(item.x1, item.x2);
  const top = Math.min(item.y1, item.y2);
  const bottom = Math.max(item.y1, item.y2);
  const middleY = top + (bottom - top) / 2;
  const leftHandleDistance = Math.hypot(point.x - left, point.y - middleY);
  const rightHandleDistance = Math.hypot(point.x - right, point.y - middleY);

  if (leftHandleDistance <= DRAWING_ENDPOINT_HIT_RADIUS_PX || rightHandleDistance <= DRAWING_ENDPOINT_HIT_RADIUS_PX) {
    return leftHandleDistance <= rightHandleDistance ? "left" : "right";
  }

  if (point.y < top - RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX || point.y > bottom + RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX) {
    return null;
  }

  const leftEdgeDistance = Math.abs(point.x - left);
  const rightEdgeDistance = Math.abs(point.x - right);
  if (leftEdgeDistance <= RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX || rightEdgeDistance <= RECTANGLE_SIDE_RESIZE_HIT_RADIUS_PX) {
    return leftEdgeDistance <= rightEdgeDistance ? "left" : "right";
  }

  return null;
}

function isPointOnRenderableDrawing(point: ChartPanePoint, item: RenderableDrawing): boolean {
  if (item.kind === "line") {
    return distanceToLineSegment(point, { x: item.x1, y: item.y1 }, { x: item.x2, y: item.y2 }) <= DRAWING_HIT_RADIUS_PX;
  }

  const left = Math.min(item.x1, item.x2);
  const right = Math.max(item.x1, item.x2);
  const top = Math.min(item.y1, item.y2);
  const bottom = Math.max(item.y1, item.y2);
  const nearVerticalEdge =
    point.y >= top - DRAWING_HIT_RADIUS_PX &&
    point.y <= bottom + DRAWING_HIT_RADIUS_PX &&
    (Math.abs(point.x - left) <= DRAWING_HIT_RADIUS_PX || Math.abs(point.x - right) <= DRAWING_HIT_RADIUS_PX);
  const nearHorizontalEdge =
    point.x >= left - DRAWING_HIT_RADIUS_PX &&
    point.x <= right + DRAWING_HIT_RADIUS_PX &&
    (Math.abs(point.y - top) <= DRAWING_HIT_RADIUS_PX || Math.abs(point.y - bottom) <= DRAWING_HIT_RADIUS_PX);
  return nearVerticalEdge || nearHorizontalEdge;
}

function distanceToLineSegment(point: ChartPanePoint, start: ChartPanePoint, end: ChartPanePoint): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) {
    return Math.hypot(point.x - start.x, point.y - start.y);
  }

  const segmentPosition = clampNumber(((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared, 0, 1);
  const projectedX = start.x + segmentPosition * dx;
  const projectedY = start.y + segmentPosition * dy;
  return Math.hypot(point.x - projectedX, point.y - projectedY);
}

function normalizeDrawingPoint(point: DrawingPoint): DrawingPoint {
  return {
    logical: point.logical,
    time: point.time,
    price: normalizeDraggedLiquidityPrice(point.price),
  };
}

function isSameDrawingPoint(left: DrawingPoint, right: DrawingPoint): boolean {
  return Number(left.logical) === Number(right.logical) && left.price === right.price;
}

function releaseChartPointerCapture(container: HTMLElement | null, pointerId: number) {
  if (!container) {
    return;
  }

  if (container.hasPointerCapture(pointerId)) {
    container.releasePointerCapture(pointerId);
  }

  const eventSurface = container.parentElement;
  if (eventSurface?.hasPointerCapture(pointerId)) {
    eventSurface.releasePointerCapture(pointerId);
  }
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isEditableEventTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }

  const tagName = target.tagName.toLowerCase();
  return tagName === "input" || tagName === "textarea" || tagName === "select" || target.isContentEditable;
}

function isChartOverlayControlEventTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest("[data-chart-overlay-control='true']") !== null;
}

function OhlcReadout({ candle }: { candle: HoveredCandle | null }) {
  if (!candle) {
    return null;
  }

  const valueClassName = candle.close >= candle.open ? "text-emerald-300" : "text-rose-300";
  const previousClose = candle.previousClose;
  const change = previousClose !== null ? candle.close - previousClose : null;
  const changePercent = previousClose !== null && previousClose !== 0 ? ((candle.close - previousClose) / Math.abs(previousClose)) * 100 : null;
  const changeClassName = change === null || change === 0 ? "text-slate-200" : change > 0 ? "text-emerald-300" : "text-rose-300";
  const changeText =
    change === null
      ? null
      : changePercent === null
        ? signedPriceFormatter.format(change)
        : `${signedPriceFormatter.format(change)} (${signedPercentFormatter.format(changePercent)}%)`;

  return (
    <div className="pointer-events-none absolute left-3 top-3 z-20 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-slate-800/80 bg-slate-950/80 px-2.5 py-1.5 text-[11px] font-medium shadow-lg shadow-slate-950/30 backdrop-blur">
      <OhlcField label="O" value={priceFormatter.format(candle.open)} valueClassName={valueClassName} />
      <OhlcField label="H" value={priceFormatter.format(candle.high)} valueClassName={valueClassName} />
      <OhlcField label="L" value={priceFormatter.format(candle.low)} valueClassName={valueClassName} />
      <OhlcField label="C" value={priceFormatter.format(candle.close)} valueClassName={valueClassName} />
      {changeText ? <OhlcField value={changeText} valueClassName={changeClassName} /> : null}
      <OhlcField label="Vol" value={volumeFormatter.format(candle.volume)} />
      {candle.isPartial ? <span className="text-cyan-200">Live</span> : null}
    </div>
  );
}

function OhlcField({
  label,
  value,
  valueClassName = "text-slate-200",
}: {
  label?: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <span className="inline-flex items-baseline gap-1 tabular-nums">
      {label ? <span className="text-slate-500">{label}</span> : null}
      <span className={valueClassName}>{value}</span>
    </span>
  );
}

function DrawingOverlay({ overlay }: { overlay: DrawingOverlayState }) {
  if (overlay.width <= 0 || overlay.height <= 0 || (overlay.items.length === 0 && !overlay.anchor && !overlay.livePriceLine)) {
    return null;
  }

  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute left-0 top-0"
      width={overlay.width}
      height={overlay.height}
      viewBox={`0 0 ${overlay.width} ${overlay.height}`}
    >
      {overlay.livePriceLine ? <LivePriceOverlayLine line={overlay.livePriceLine} /> : null}
      {overlay.items.map((item) => (
        <DrawingOverlayItem key={`${item.id}:${item.isDraft ? "draft" : "final"}`} item={item} />
      ))}
      {overlay.anchor ? <DrawingAnchorPreviewMark anchor={overlay.anchor} /> : null}
    </svg>
  );
}

function LivePriceOverlayLine({ line }: { line: RenderableLivePriceLine }) {
  return (
    <line
      x1={line.x1}
      y1={line.y}
      x2={line.x2}
      y2={line.y}
      stroke="rgb(56,189,248)"
      strokeWidth="2"
      strokeLinecap="round"
      strokeDasharray="6 5"
      vectorEffect="non-scaling-stroke"
    />
  );
}

function DrawingOverlayItem({ item }: { item: RenderableDrawing }) {
  const stroke = item.isDraft ? "rgba(125,211,252,0.85)" : item.isSelected ? "rgb(34,211,238)" : "rgba(226,232,240,0.88)";
  const strokeDasharray = item.isDraft ? "6 4" : undefined;
  const strokeWidth = item.isSelected ? "3" : "2";

  if (item.kind === "rectangle") {
    const x = Math.min(item.x1, item.x2);
    const y = Math.min(item.y1, item.y2);
    const width = Math.abs(item.x2 - item.x1);
    const height = Math.abs(item.y2 - item.y1);
    const middleY = y + height / 2;
    return (
      <g>
        <rect
          x={x}
          y={y}
          width={width}
          height={height}
          rx="2"
          fill={item.isDraft ? "rgba(56,189,248,0.08)" : "rgba(148,163,184,0.08)"}
          stroke={stroke}
          strokeWidth={strokeWidth}
          strokeDasharray={strokeDasharray}
          vectorEffect="non-scaling-stroke"
        />
        <DrawingEndpoint x={item.x1} y={item.y1} draft={item.isDraft} selected={item.isSelected} />
        <DrawingEndpoint x={item.x2} y={item.y2} draft={item.isDraft} selected={item.isSelected} />
        <DrawingEndpoint x={x} y={middleY} draft={item.isDraft} selected={item.isSelected} />
        <DrawingEndpoint x={x + width} y={middleY} draft={item.isDraft} selected={item.isSelected} />
      </g>
    );
  }

  return (
    <g>
      <line
        x1={item.x1}
        y1={item.y1}
        x2={item.x2}
        y2={item.y2}
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={strokeDasharray}
        vectorEffect="non-scaling-stroke"
      />
      <DrawingEndpoint x={item.x1} y={item.y1} draft={item.isDraft} selected={item.isSelected} />
      <DrawingEndpoint x={item.x2} y={item.y2} draft={item.isDraft} selected={item.isSelected} />
    </g>
  );
}

function DrawingAnchorPreviewMark({ anchor }: { anchor: RenderableDrawingAnchor }) {
  return (
    <g>
      <circle
        cx={anchor.x}
        cy={anchor.y}
        r="5"
        fill="rgba(34,211,238,0.18)"
        stroke="rgb(125,211,252)"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d={`M ${anchor.x - 8} ${anchor.y} L ${anchor.x + 8} ${anchor.y} M ${anchor.x} ${anchor.y - 8} L ${anchor.x} ${anchor.y + 8}`}
        stroke="rgba(240,249,255,0.9)"
        strokeWidth="1.5"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </g>
  );
}

function DrawingEndpoint({ x, y, draft, selected }: { x: number; y: number; draft: boolean; selected: boolean }) {
  return (
    <circle
      cx={x}
      cy={y}
      r={selected ? "4" : "3"}
      fill={draft || selected ? "rgb(125,211,252)" : "rgb(226,232,240)"}
      stroke="rgb(15,23,42)"
      strokeWidth="1.5"
      vectorEffect="non-scaling-stroke"
    />
  );
}

function ChartToolButton({
  active = false,
  disabled = false,
  label,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={`grid h-8 w-9 place-items-center border-r border-slate-800 text-slate-400 transition last:border-r-0 disabled:cursor-not-allowed disabled:opacity-45 ${
        active ? "bg-cyan-400/15 text-cyan-100" : "hover:bg-slate-900/80 hover:text-slate-100"
      }`}
    >
      {children}
    </button>
  );
}

function CursorToolIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 3l11 10-6 1.5L8 21 6 3z" strokeLinejoin="round" />
    </svg>
  );
}

function LineToolIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M5 18 19 6" strokeLinecap="round" />
      <circle cx="5" cy="18" r="1.7" fill="currentColor" stroke="none" />
      <circle cx="19" cy="6" r="1.7" fill="currentColor" stroke="none" />
    </svg>
  );
}

function RectangleToolIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="5" y="6" width="14" height="12" rx="1.5" />
    </svg>
  );
}

function ClearDrawingsIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 7h12" strokeLinecap="round" />
      <path d="M9 7V5h6v2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M9 10v7" strokeLinecap="round" />
      <path d="M15 10v7" strokeLinecap="round" />
      <path d="M8 7l1 13h6l1-13" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FitChartIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M8 4H4v4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 4l5 5" strokeLinecap="round" />
      <path d="M16 4h4v4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20 4l-5 5" strokeLinecap="round" />
      <path d="M8 20H4v-4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 20l5-5" strokeLinecap="round" />
      <path d="M16 20h4v-4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20 20l-5-5" strokeLinecap="round" />
    </svg>
  );
}

function ComputeLiquidityIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M5 7h14" strokeLinecap="round" />
      <path d="M5 17h14" strokeLinecap="round" />
      <path d="M8 4v6" strokeLinecap="round" />
      <path d="M16 14v6" strokeLinecap="round" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M20 11a8 8 0 0 0-14.4-4.8L4 8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 4v4h4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 13a8 8 0 0 0 14.4 4.8L20 16" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20 20v-4h-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function LegendDot({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap">
      <span className={`h-2.5 w-2.5 rounded-full ${className}`} />
      <span>{label}</span>
    </span>
  );
}

function LegendLine({ className, label }: { className: string; label: string }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-2 whitespace-nowrap">
      <span className={`h-0 w-5 border-t-2 border-dotted ${className}`} />
      <span>{label}</span>
    </span>
  );
}

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}
