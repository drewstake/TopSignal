import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  createChart,
  createSeriesMarkers,
  type AutoscaleInfo,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type LogicalRange,
  type MouseEventParams,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import { Button } from "../../components/ui/Button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/Card";
import { botsApi, buildUserScopedProjectXCandleRequestKey, streamProjectXMarketPrice, type CandleQuery } from "../../lib/api";
import { ENABLE_PERF_LOGS, logPerfInfo } from "../../lib/perf";
import { APP_THEME_CHANGED_EVENT } from "../../lib/theme";
import type { BotActivity, BotConfig, BotEvaluation, BotTimeframeUnit, ProjectXMarketCandle, ProjectXMarketPrice } from "../../lib/types";
import {
  buildBotCandleCacheKey,
  filterMarketCandlesForWindow,
  invalidateLegacyBotCandleCache,
  mergeMarketCandles,
  readBotCandleCache,
  upsertMarketCandles,
  writeBotCandleCache,
  type CandleQueryWindow,
} from "./botCandleCache";
import {
  buildGapRangeKey,
  buildGapRepairWindows,
  findCandleGaps,
  isGapCoveredByRepairWindows,
  planBotCandleFetches,
  type BotCandleFetchRequest,
  type CandleGap,
} from "./botCandleGaps";
import type { BotMarketSnapshot } from "./botMarketContext";
import { BotChartStateOverlay, BotChartStatus } from "./BotChartStatus";
import {
  ChartToolButton,
  ClearDrawingsIcon,
  ComputeLiquidityIcon,
  CursorToolIcon,
  DrawingOverlay,
  FitChartIcon,
  HistoryIcon,
  LegendDot,
  LegendLine,
  LineToolIcon,
  OhlcReadout,
  RectangleToolIcon,
  RefreshIcon,
  type HoveredCandle,
} from "./BotChartPresentation";
import {
  buildDrawingOverlayState,
  chartPanePointFromPointerEvent,
  chartPaneYFromPointerEvent,
  clampNumber,
  constrainDrawingEndPoint,
  drawingPointFromPanePoint,
  drawingPointToPanePoint,
  findDrawingHitTargetAtPanePoint,
  isChartOverlayControlEventTarget,
  isEditableEventTarget,
  isMeaningfulDrawing,
  isSameDrawingPoint,
  normalizeDraggedLiquidityPrice,
  normalizeDrawingPoint,
  releaseChartPointerCapture,
  snapDrawingPointToCandle,
  type ChartPanePoint,
  type DrawingAnchorPreview,
  type DrawingDraft,
  type DrawingEditMode,
  type DrawingEditState,
  type DrawingHitTarget,
  type DrawingKind,
  type DrawingModifiers,
  type DrawingPlacementState,
  type DrawingPoint,
  type DrawingShape,
  type DrawingTool,
} from "./botChartDrawings";
import { BotEvaluationOverlayStatus } from "./BotEvaluationOverlayStatus";
import {
  BOT_CHART_MAX_BARS,
  BOT_CHART_TIMEFRAMES,
  buildBotChartQuery,
  buildInitialBotChartQuery,
  buildBotLivePriceQuery,
  buildCandlestickData,
  buildEmaData,
  buildLiquidityLevels,
  buildOlderCandlesQuery,
  buildSignalMarkers,
  buildSmaData,
  buildVwapData,
  buildLiveCandleFromPriceUpdate,
  toUtcTimestamp,
  type LiquidityLevel,
  type LiquiditySide,
  type BotChartTimeframe,
  type BotChartTimeframeId,
} from "./botChartData";
import {
  LatestRequestCoordinator,
  LogicalViewportMemory,
  PrioritizedRequestScheduler,
  getViewportRestoreRange,
  invalidateChartRequestLanes,
  runChartContextLoadsInParallel,
  type ChartViewportMutation,
  type RequestPriority,
} from "./botChartLifecycle";
import { readBotChartThemeColors } from "./botChartTheme";
import { buildVolumeData } from "./botChartVolume";
import { resolveBotChartViewState } from "./botChartViewState";
import {
  buildBotDrawingStorageKey,
  clearBotDrawings,
  readBotDrawings,
  writeBotDrawings,
  type BotDrawingStorageScope,
} from "./botDrawingStorage";
import {
  buildEvaluationOverlayModel,
  decisionMatchesBotMarket,
  isActionableEvaluation,
  selectLatestActionableEvaluation,
  type EvaluationFreshnessStatus,
  type EvaluationOverlayLevelRole,
} from "./botEvaluationOverlay";

const POLL_INTERVAL_MS = 30_000;
const MARKET_SNAPSHOT_THROTTLE_MS = 1_000;
const FRESHNESS_TICK_MS = 10_000;
const STALE_DATA_AFTER_MS = 2 * POLL_INTERVAL_MS + 15_000;
const MAX_LOADED_BARS = 10_000;
const HISTORY_AUTOLOAD_EDGE_BARS = 12;
const MAX_GAP_REPAIR_WINDOWS = 3;
const LIVE_PRICE_POLL_INTERVAL_MS = 10_000;
const LIVE_PRICE_STREAM_THROTTLE_MS = 250;
const LIVE_PRICE_STREAM_STALE_MS = 5_000;
const LIVE_PRICE_STALE_AFTER_MS = 2 * LIVE_PRICE_POLL_INTERVAL_MS + 5_000;
const CANDLE_REQUEST_TIMEOUT_MS = 70_000;
const LIVE_PRICE_REQUEST_TIMEOUT_MS = 12_000;
const BACKGROUND_CANDLE_START_INTERVAL_MS = 300;
const LIQUIDITY_LINE_DRAG_HIT_RADIUS_PX = 8;
const DEFAULT_CHART_TIMEFRAME_ID: BotChartTimeframeId = "5m";
const EASTERN_TIME_ZONE = "America/New_York";
const VWAP_SESSION_START_TIME = "18:00";

const candleRequestScheduler = new PrioritizedRequestScheduler<string>({
  maxConcurrency: 2,
  maxBackgroundConcurrency: 1,
  minBackgroundStartIntervalMs: BACKGROUND_CANDLE_START_INTERVAL_MS,
});

function requestProjectXCandles(
  authenticatedCacheScope: string,
  query: CandleQuery,
  priority: RequestPriority,
  signal?: AbortSignal,
): Promise<ProjectXMarketCandle[]> {
  return candleRequestScheduler.schedule(
    buildUserScopedProjectXCandleRequestKey(authenticatedCacheScope, query),
    (sharedSignal) => botsApi.getCandles(query, { signal: sharedSignal }),
    { priority, signal },
  );
}

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
interface ChartHandles {
  chart: IChartApi;
  candleSeries: ISeriesApi<"Candlestick">;
  volumeSeries: ISeriesApi<"Histogram">;
  fastSeries: ISeriesApi<"Line">;
  slowSeries: ISeriesApi<"Line">;
  vwapSeries: ISeriesApi<"Line">;
  markers: ISeriesMarkersPluginApi<Time>;
}

interface BotSignalChartProps {
  bot: BotConfig | null;
  demoMode?: boolean;
  /** Stable non-secret namespace for persisted per-user chart state. */
  authenticatedCacheScope: string | null;
  activity: BotActivity | null;
  lastEvaluation: BotEvaluation | null;
  refreshToken: number;
  /** Throttled snapshot of the candles currently on the chart (for analysis/context panels). */
  onMarketData?: (snapshot: BotMarketSnapshot | null) => void;
}

interface LoadCandlesOptions {
  silent?: boolean;
  forceRefresh?: boolean;
}

interface LoadLivePriceOptions {
  force?: boolean;
}

interface LivePricePoint {
  timestamp: string;
  observedAt: string;
  price: number;
  isPartial: boolean;
}

interface ChartTimeframeSelection {
  key: string;
  id: BotChartTimeframeId;
}

type LiquidityPriceOverrides = Partial<Record<LiquiditySide, number>>;
type LiquidityPriceLineMap = Partial<Record<LiquiditySide, IPriceLine>>;
type EvaluationPriceLineMap = Partial<Record<EvaluationOverlayLevelRole, IPriceLine>>;
type ChartLayerId = "fastSma" | "slowSma" | "vwap" | "volume" | "buySignals" | "sellSignals" | "buyLiquidity" | "sellLiquidity";
interface FittedViewportState {
  key: string;
  candleCount: number;
}

interface AppliedSeriesState {
  timeframeKey: string;
  candles: CandlestickData<UTCTimestamp>[];
  volume: HistogramData<UTCTimestamp>[];
  fast: LineData<UTCTimestamp>[];
  slow: LineData<UTCTimestamp>[];
  vwap: LineData<UTCTimestamp>[];
}

type SignalChartLoadKind = "cold" | "warm" | "timeframe-switch";

interface PendingSignalChartLoadTiming {
  contextKey: string;
  kind: SignalChartLoadKind;
  startedAtMs: number;
  requestCount: number;
  cacheHit: boolean;
  measured: boolean;
}

interface SignalChartPerfContext {
  contextKey: string;
  botId: number | null;
  contractId: string;
}

interface LiquidityDragState {
  side: LiquiditySide;
  pointerId: number;
}

export function BotSignalChart({ bot, demoMode = false, authenticatedCacheScope, activity, lastEvaluation, refreshToken, onMarketData }: BotSignalChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartHandlesRef = useRef<ChartHandles | null>(null);
  const livePriceLineRef = useRef<IPriceLine | null>(null);
  const liquidityPriceLinesRef = useRef<LiquidityPriceLineMap>({});
  const evaluationPriceLinesRef = useRef<EvaluationPriceLineMap>({});
  const liquidityLevelsRef = useRef<LiquidityLevel[]>([]);
  const evaluationPricesRef = useRef<number[]>([]);
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
  const fittedViewportRef = useRef<FittedViewportState | null>(null);
  const autoHistoryHookRef = useRef<((range: LogicalRange | null) => void) | null>(null);
  const appliedSeriesStateRef = useRef<AppliedSeriesState | null>(null);
  const signalChartPerfContextRef = useRef<SignalChartPerfContext | null>(null);
  const pendingSignalChartLoadTimingRef = useRef<PendingSignalChartLoadTiming | null>(null);
  const signalChartMeasureFrameRef = useRef<number | null>(null);
  const candleRequestsRef = useRef(new LatestRequestCoordinator());
  const liveRequestsRef = useRef(new LatestRequestCoordinator());
  const historyRequestsRef = useRef(new LatestRequestCoordinator());
  const repairRequestsRef = useRef(new LatestRequestCoordinator());
  const chartBackgroundControllerRef = useRef<AbortController | null>(null);
  const warmTimeframesControllerRef = useRef<AbortController | null>(null);
  const requestTimeoutIdsRef = useRef<Set<number>>(new Set());
  const pendingViewportRestoreRef = useRef<LogicalRange | null>(null);
  const viewportMemoryRef = useRef(new LogicalViewportMemory<string>());
  const viewportRestoreFrameRef = useRef<number | null>(null);
  const lastLiveStreamEventAtRef = useRef(0);
  const pendingLiveStreamPriceRef = useRef<ProjectXMarketPrice | null>(null);
  const liveStreamRenderTimeoutRef = useRef<number | null>(null);
  const candlesRef = useRef<ProjectXMarketCandle[]>([]);
  const liveCandleRef = useRef<ProjectXMarketCandle | null>(null);
  const repairedGapKeysRef = useRef<Set<string>>(new Set());
  const automaticRepairLoadVersionRef = useRef(-1);
  const actionableEvaluationsRef = useRef<Map<number, BotEvaluation>>(new Map());
  const hasMoreHistoryRef = useRef(true);
  const marketSnapshotTimeoutRef = useRef<number | null>(null);
  const [candles, setCandles] = useState<ProjectXMarketCandle[]>([]);
  const [liveCandle, setLiveCandle] = useState<ProjectXMarketCandle | null>(null);
  const [streamPrice, setStreamPrice] = useState<ProjectXMarketPrice | null>(null);
  const [streamActive, setStreamActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [gapRepairing, setGapRepairing] = useState(false);
  const [servedFromCacheOnly, setServedFromCacheOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [livePriceError, setLivePriceError] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);
  const [freshnessTick, setFreshnessTick] = useState(0);
  const [liquidityPriceOverrides, setLiquidityPriceOverrides] = useState<LiquidityPriceOverrides>({});
  const [visibleChartLayers, setVisibleChartLayers] = useState<Record<ChartLayerId, boolean>>({
    fastSma: true,
    slowSma: true,
    vwap: true,
    volume: true,
    buySignals: true,
    sellSignals: true,
    buyLiquidity: true,
    sellLiquidity: true,
  });
  const [drawingTool, setDrawingTool] = useState<DrawingTool>("cursor");
  const [drawings, setDrawings] = useState<DrawingShape[]>([]);
  const [hydratedDrawingScopeKey, setHydratedDrawingScopeKey] = useState<string | null>(null);
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
  const chartTimeframe = BOT_CHART_TIMEFRAMES.find((option) => option.id === selectedTimeframeId) ?? BOT_CHART_TIMEFRAMES[0];
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
  const chartViewportKey = `${authenticatedCacheScope ?? "scope-pending"}:${bot?.id ?? "none"}:${bot?.contract_id ?? ""}:${bot?.symbol ?? ""}:${selectedTimeframeId}`;
  const chartBotId = bot?.id ?? null;
  const chartContractId = bot?.contract_id ?? "";
  const warmContractKey = `${authenticatedCacheScope ?? "scope-pending"}:${bot?.id ?? "none"}:${bot?.contract_id ?? ""}:${bot?.symbol ?? ""}`;
  const warmBotRef = useRef<BotConfig | null>(bot);
  warmBotRef.current = bot;
  const [marketDataContextKey, setMarketDataContextKey] = useState(chartViewportKey);
  const marketDataMatchesContext = marketDataContextKey === chartViewportKey;

  // State updates clear old rows after a market change. This render-time guard
  // prevents the previous market from being applied or fitted under the new key
  // during that intervening commit.
  const visibleCandles = useMemo(
    () => (marketDataMatchesContext ? mergeLiveCandle(candles, liveCandle) : []),
    [candles, liveCandle, marketDataMatchesContext],
  );
  const chartCandles = useMemo(() => buildCandlestickData(visibleCandles), [visibleCandles]);
  const hoverCandlesByTime = useMemo(
    () => buildHoverCandleMap(visibleCandles, chartCandles),
    [chartCandles, visibleCandles],
  );
  const latestOhlcCandle = useMemo(() => getLatestHoveredCandle(hoverCandlesByTime), [hoverCandlesByTime]);
  const volumeData = useMemo(() => buildVolumeData(visibleCandles), [visibleCandles]);
  const closedChartCandles = useMemo(
    () => buildCandlestickData(marketDataMatchesContext ? candles.filter((candle) => !candle.is_partial) : []),
    [candles, marketDataMatchesContext],
  );
  const usesEmaLayers =
    bot?.strategy_type === "pullback_trap_reversal" ||
    bot?.strategy_type === "ema_scalping" ||
    bot?.strategy_type === "ema_trend_pullback";
  const showAverageLayers = bot?.strategy_type === "sma_cross" || usesEmaLayers;
  const fastAverage = useMemo(
    () => (showAverageLayers ? (usesEmaLayers ? buildEmaData(chartCandles, bot?.fast_period ?? 0) : buildSmaData(chartCandles, bot?.fast_period ?? 0)) : []),
    [bot?.fast_period, chartCandles, showAverageLayers, usesEmaLayers],
  );
  const slowAverage = useMemo(
    () => (showAverageLayers ? (usesEmaLayers ? buildEmaData(chartCandles, bot?.slow_period ?? 0) : buildSmaData(chartCandles, bot?.slow_period ?? 0)) : []),
    [bot?.slow_period, chartCandles, showAverageLayers, usesEmaLayers],
  );
  const vwap = useMemo(
    () => buildVwapData(visibleCandles, { sessionStartTime: VWAP_SESSION_START_TIME, sessionTimeZone: EASTERN_TIME_ZONE }),
    [visibleCandles],
  );
  // Liquidity detection is quadratic in the worst case; recent swings are what
  // matter, so cap the scan even when deep history has been paged in.
  const liquidityLevels = useMemo(
    () => buildLiquidityLevels(closedChartCandles.slice(-BOT_CHART_MAX_BARS)),
    [closedChartCandles],
  );
  const displayedLiquidityLevels = useMemo(
    () =>
      liquidityLevels.map((level) => ({
        ...level,
        price: liquidityPriceOverrides[level.side] ?? level.price,
      })),
    [liquidityLevels, liquidityPriceOverrides],
  );
  const visibleLiquidityLevels = useMemo(
    () =>
      displayedLiquidityLevels.filter((level) =>
        level.side === "buy" ? visibleChartLayers.buyLiquidity : visibleChartLayers.sellLiquidity,
      ),
    [displayedLiquidityLevels, visibleChartLayers.buyLiquidity, visibleChartLayers.sellLiquidity],
  );
  const signalMarkers = useMemo(
    () =>
      buildSignalMarkers({
        candles: closedChartCandles,
        activityDecisions:
          activity &&
          bot &&
          activity.config.id === bot.id &&
          activity.config.contract_id === bot.contract_id &&
          activity.config.timeframe_unit === bot.timeframe_unit &&
          activity.config.timeframe_unit_number === bot.timeframe_unit_number
            ? activity.decisions.filter((decision) => decisionMatchesBotMarket(decision, bot))
            : [],
        lastEvaluation:
          lastEvaluation &&
          bot &&
          lastEvaluation.config.id === bot.id &&
          lastEvaluation.config.contract_id === bot.contract_id &&
          decisionMatchesBotMarket(lastEvaluation.decision, bot) &&
          lastEvaluation.config.timeframe_unit === bot.timeframe_unit &&
          lastEvaluation.config.timeframe_unit_number === bot.timeframe_unit_number
            ? lastEvaluation
            : null,
        timeframeUnit: chartTimeframe.unit,
        timeframeUnitNumber: chartTimeframe.unitNumber,
      }),
    [activity, bot, chartTimeframe, closedChartCandles, lastEvaluation],
  );
  const visibleSignalMarkers = useMemo(
    () =>
      signalMarkers.filter((marker) => {
        if (marker.text === "BUY") {
          return visibleChartLayers.buySignals;
        }
        if (marker.text === "SELL") {
          return visibleChartLayers.sellSignals;
        }
        return true;
      }),
    [signalMarkers, visibleChartLayers.buySignals, visibleChartLayers.sellSignals],
  );
  useEffect(() => {
    if (isActionableEvaluation(lastEvaluation)) {
      actionableEvaluationsRef.current.set(lastEvaluation.config.id, lastEvaluation);
    }
  }, [lastEvaluation]);
  const latestActionableEvaluation = useMemo(
    () =>
      selectLatestActionableEvaluation({
        bot,
        activity,
        lastEvaluation,
        cachedEvaluation: bot ? actionableEvaluationsRef.current.get(bot.id) ?? null : null,
      }),
    [activity, bot, lastEvaluation],
  );
  const latestClosedCandle = useMemo(
    () =>
      marketDataMatchesContext
        ? findLatestClosedMarketCandle(candles)
        : null,
    [candles, marketDataMatchesContext],
  );
  const evaluationOverlayModel = useMemo(
    () =>
      buildEvaluationOverlayModel(latestActionableEvaluation, {
        latestClosedTimestamp: latestClosedCandle?.timestamp ?? null,
      }),
    [latestActionableEvaluation, latestClosedCandle?.timestamp],
  );
  const livePricePoint = useMemo<LivePricePoint | null>(() => {
    if (!marketDataMatchesContext) {
      return null;
    }
    if (streamPrice && Number.isFinite(streamPrice.price) && Number.isFinite(Date.parse(streamPrice.timestamp))) {
      return {
        timestamp: streamPrice.timestamp,
        observedAt: streamPrice.timestamp,
        price: streamPrice.price,
        isPartial: true,
      };
    }
    if (liveCandle && Number.isFinite(liveCandle.close) && Number.isFinite(Date.parse(liveCandle.timestamp))) {
      return {
        timestamp: liveCandle.timestamp,
        observedAt: liveCandle.fetched_at ?? liveCandle.timestamp,
        price: liveCandle.close,
        isPartial: liveCandle.is_partial,
      };
    }
    return null;
  }, [liveCandle, marketDataMatchesContext, streamPrice]);
  const livePrice = livePricePoint?.price ?? null;
  void freshnessTick; // Re-render trigger for data- and price-age status.
  const livePriceObservedAtMs = livePricePoint ? Date.parse(livePricePoint.observedAt) : Number.NaN;
  const livePriceIsStale =
    livePricePoint !== null &&
    (!Number.isFinite(livePriceObservedAtMs) || Date.now() - livePriceObservedAtMs > LIVE_PRICE_STALE_AFTER_MS);
  const liquidityDragContextKey = chartViewportKey;
  const drawingStorageScope = useMemo<BotDrawingStorageScope | null>(
    () =>
      bot && authenticatedCacheScope
        ? {
            userScope: authenticatedCacheScope,
            botId: bot.id,
            contractId: bot.contract_id,
            timeframe: selectedTimeframeId,
          }
        : null,
    [authenticatedCacheScope, bot, selectedTimeframeId],
  );
  const drawingStorageScopeKey = drawingStorageScope ? buildBotDrawingStorageKey(drawingStorageScope) : null;
  const chartViewportKeyRef = useRef(chartViewportKey);
  chartViewportKeyRef.current = chartViewportKey;

  useEffect(() => {
    if (!ENABLE_PERF_LOGS || chartBotId === null) {
      pendingSignalChartLoadTimingRef.current = null;
      signalChartPerfContextRef.current =
        chartBotId === null
          ? null
          : { contextKey: chartViewportKey, botId: chartBotId, contractId: chartContractId };
      return;
    }

    const previous = signalChartPerfContextRef.current;
    const timeframeSwitch =
      previous !== null &&
      previous.contextKey !== chartViewportKey &&
      previous.botId === chartBotId &&
      previous.contractId === chartContractId;
    pendingSignalChartLoadTimingRef.current = {
      contextKey: chartViewportKey,
      kind: timeframeSwitch ? "timeframe-switch" : "cold",
      startedAtMs: performance.now(),
      requestCount: 0,
      cacheHit: false,
      measured: false,
    };
    signalChartPerfContextRef.current = {
      contextKey: chartViewportKey,
      botId: chartBotId,
      contractId: chartContractId,
    };
  }, [chartBotId, chartContractId, chartViewportKey]);

  const queueViewportRestore = useCallback(
    (mutation: ChartViewportMutation, previousRows: ProjectXMarketCandle[], nextRows: ProjectXMarketCandle[]) => {
      const handles = chartHandlesRef.current;
      if (!handles || previousRows.length === 0 || nextRows.length === 0) {
        return;
      }
      pendingViewportRestoreRef.current = getViewportRestoreRange(
        mutation,
        handles.chart.timeScale().getVisibleLogicalRange(),
        marketCandleTimes(previousRows),
        marketCandleTimes(nextRows),
      );
    },
    [],
  );
  const [repairVersion, setRepairVersion] = useState(0);
  const [canonicalLoadVersion, setCanonicalLoadVersion] = useState(0);
  const candleGaps = useMemo<CandleGap[]>(
    () => (chartConfig ? findCandleGaps(candles, chartConfig.timeframe_unit, chartConfig.timeframe_unit_number) : []),
    [candles, chartConfig],
  );
  const dataGaps = useMemo(() => candleGaps.filter((gap) => gap.kind === "data"), [candleGaps]);
  const unrepairedDataGaps = useMemo(
    () => {
      void repairVersion;
      return dataGaps.filter((gap) => !repairedGapKeysRef.current.has(buildGapRangeKey(gap)));
    },
    [dataGaps, repairVersion],
  );
  const confirmedEmptyGapCount = dataGaps.length - unrepairedDataGaps.length;
  const chartConfigRef = useRef<BotConfig | null>(chartConfig);
  const livePriceRef = useRef<number | null>(livePrice);

  useEffect(() => {
    chartConfigRef.current = chartConfig;
  }, [chartConfig]);

  useEffect(() => {
    livePriceRef.current = livePrice;
  }, [livePrice]);

  useEffect(() => {
    candlesRef.current = candles;
  }, [candles]);

  useEffect(() => {
    liveCandleRef.current = liveCandle;
  }, [liveCandle]);

  // Throttled market snapshot for the analysis/context panel.
  useEffect(() => {
    if (!onMarketData) {
      return;
    }

    if (!chartConfig || visibleCandles.length === 0) {
      onMarketData(null);
      return;
    }

    if (marketSnapshotTimeoutRef.current !== null) {
      return;
    }

    marketSnapshotTimeoutRef.current = window.setTimeout(() => {
      marketSnapshotTimeoutRef.current = null;
      const config = chartConfigRef.current;
      if (!config) {
        return;
      }
      onMarketData({
        contractKey: `${config.contract_id}:${config.timeframe_unit}:${config.timeframe_unit_number}`,
        unit: config.timeframe_unit,
        unitNumber: config.timeframe_unit_number,
        candles: mergeLiveCandle(candlesRef.current, liveCandleRef.current),
        lastPrice: livePriceRef.current,
        updatedAt: new Date().toISOString(),
      });
    }, MARKET_SNAPSHOT_THROTTLE_MS);
  }, [chartConfig, onMarketData, visibleCandles]);

  useEffect(() => {
    return () => {
      if (marketSnapshotTimeoutRef.current !== null) {
        window.clearTimeout(marketSnapshotTimeoutRef.current);
        marketSnapshotTimeoutRef.current = null;
      }
    };
  }, []);

  // Reset per-market transient state when the chart context changes.
  useEffect(() => {
    setMarketDataContextKey(chartViewportKey);
    chartBackgroundControllerRef.current?.abort();
    chartBackgroundControllerRef.current = null;
    candlesRef.current = [];
    liveCandleRef.current = null;
    setCandles([]);
    setLiveCandle(null);
    setStreamPrice(null);
    setError(null);
    setLivePriceError(null);
    setLastLoadedAt(null);
    repairedGapKeysRef.current = new Set();
    automaticRepairLoadVersionRef.current = -1;
    hasMoreHistoryRef.current = true;
    setHasMoreHistory(true);
    setServedFromCacheOnly(false);
    setRepairVersion(0);
    setCanonicalLoadVersion(0);
    invalidateChartRequestLanes([
      candleRequestsRef.current,
      liveRequestsRef.current,
      historyRequestsRef.current,
      repairRequestsRef.current,
    ]);
    for (const timeoutId of requestTimeoutIdsRef.current) {
      window.clearTimeout(timeoutId);
    }
    requestTimeoutIdsRef.current.clear();
    pendingViewportRestoreRef.current = null;
    if (viewportRestoreFrameRef.current !== null) {
      window.cancelAnimationFrame(viewportRestoreFrameRef.current);
      viewportRestoreFrameRef.current = null;
    }
    setHistoryLoading(false);
    setGapRepairing(false);
  }, [chartViewportKey]);

  useEffect(() => {
    const viewportMemory = viewportMemoryRef.current;
    const rememberedRange = viewportMemory.restore(chartViewportKey);
    if (rememberedRange) {
      pendingViewportRestoreRef.current = rememberedRange;
    }

    return () => {
      viewportMemory.save(
        chartViewportKey,
        chartHandlesRef.current?.chart.timeScale().getVisibleLogicalRange() ?? null,
      );
    };
  }, [chartViewportKey]);

  // Periodic tick so freshness text ("Updated Xs ago", stale chip) re-renders.
  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setFreshnessTick((current) => current + 1);
    }, FRESHNESS_TICK_MS);
    return () => window.clearInterval(intervalId);
  }, []);

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
    liquidityLevelsRef.current = visibleLiquidityLevels;
  }, [visibleLiquidityLevels]);

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
    const storedDrawings = drawingStorageScope ? readBotDrawings(drawingStorageScope) : [];
    drawingsRef.current = storedDrawings;
    setDrawings(storedDrawings);
    setHydratedDrawingScopeKey(drawingStorageScopeKey);
    chartHandlesRef.current?.chart.clearCrosshairPosition();
    setDrawingOverlayRevision((current) => current + 1);
  }, [drawingStorageScope, drawingStorageScopeKey, liquidityDragContextKey]);

  useEffect(() => {
    if (!drawingStorageScope || !drawingStorageScopeKey || hydratedDrawingScopeKey !== drawingStorageScopeKey) {
      return;
    }
    writeBotDrawings(drawingStorageScope, drawings);
  }, [drawingStorageScope, drawingStorageScopeKey, drawings, hydratedDrawingScopeKey]);

  const commitCandleFetch = useCallback(
    ({
      cacheKey,
      cacheLimit,
      contextKey,
      request,
      rows,
      replaceCache = false,
      applyToChart = true,
      mutation,
    }: {
      cacheKey: string;
      cacheLimit: number;
      contextKey: string;
      request: BotCandleFetchRequest;
      rows: ProjectXMarketCandle[];
      replaceCache?: boolean;
      applyToChart?: boolean;
      mutation?: ChartViewportMutation;
    }) => {
      const fetchedAt = new Date();
      const previousEntry = readBotCandleCache(cacheKey);
      const cacheBase = replaceCache ? [] : previousEntry?.candles ?? [];
      const mergedCache = mergeMarketCandles(
        cacheBase,
        rows,
        Math.min(
          BOT_CHART_MAX_BARS,
          Math.max(cacheLimit, previousEntry?.candles.length ?? 0, rows.length),
        ),
      );
      const coverage = replaceCache
        ? request.window
        : mergeCandleCacheCoverage(previousEntry?.coverage ?? null, request.window);
      writeBotCandleCache(cacheKey, mergedCache, cacheLimitForRows(cacheLimit, mergedCache.length), {
        savedAt: fetchedAt,
        coverage,
      });

      if (!applyToChart || contextKey !== chartViewportKeyRef.current) {
        return;
      }

      const previousRows = candlesRef.current;
      const nextRows = upsertMarketCandles(previousRows, rows, MAX_LOADED_BARS);
      if (nextRows !== previousRows) {
        queueViewportRestore(
          mutation ??
            (request.reason === "missing-history" || request.reason === "interior-gap" ? "pagination" : "live"),
          previousRows,
          nextRows,
        );
        candlesRef.current = nextRows;
        setCandles(nextRows);
      }
      setLastLoadedAt(fetchedAt);
      setServedFromCacheOnly(false);

      if (request.reason === "interior-gap") {
        const config = chartConfigRef.current;
        if (config) {
          const remaining = findCandleGaps(
            candlesRef.current,
            config.timeframe_unit,
            config.timeframe_unit_number,
          );
          for (const gap of remaining) {
            if (gap.kind === "data" && isGapCoveredByRepairWindows(gap, [request.window])) {
              repairedGapKeysRef.current.add(buildGapRangeKey(gap));
            }
          }
          setRepairVersion((current) => current + 1);
        }
      }
    },
    [queueViewportRestore],
  );

  const scheduleBackgroundCandleFetches = useCallback(
    ({
      cacheKey,
      cacheLimit,
      config,
      contextKey,
      requests,
    }: {
      cacheKey: string;
      cacheLimit: number;
      config: BotConfig;
      contextKey: string;
      requests: BotCandleFetchRequest[];
    }) => {
      chartBackgroundControllerRef.current?.abort();
      const runnableRequests = requests.filter((request) => {
        if (request.reason !== "interior-gap") {
          return true;
        }
        return findCandleGaps(
          candlesRef.current,
          config.timeframe_unit,
          config.timeframe_unit_number,
        ).some(
          (gap) =>
            gap.kind === "data" &&
            isGapCoveredByRepairWindows(gap, [request.window]) &&
            !repairedGapKeysRef.current.has(buildGapRangeKey(gap)),
        );
      });
      if (runnableRequests.length === 0) {
        chartBackgroundControllerRef.current = null;
        return;
      }

      const controller = new AbortController();
      chartBackgroundControllerRef.current = controller;
      let pendingCount = runnableRequests.length;
      for (const request of runnableRequests) {
        const query = candleQueryForFetchRequest(config, request);
        if (!authenticatedCacheScope) {
          controller.abort();
          chartBackgroundControllerRef.current = null;
          return;
        }
        void requestProjectXCandles(authenticatedCacheScope, query, "background", controller.signal)
          .then((rows) => {
            commitCandleFetch({
              cacheKey,
              cacheLimit,
              contextKey,
              request,
              rows: filterCandlesForChartContext(rows, config),
              applyToChart: contextKey === chartViewportKeyRef.current,
            });
          })
          .catch((error) => {
            if (!isAbortError(error)) {
              logPerfInfo("[perf][signal-chart] background-fetch-error", {
                contextKey,
                reason: request.reason,
                error: error instanceof Error ? error.message : String(error),
              });
            }
          })
          .finally(() => {
            pendingCount -= 1;
            if (pendingCount === 0 && chartBackgroundControllerRef.current === controller) {
              chartBackgroundControllerRef.current = null;
            }
          });
      }
    },
    [authenticatedCacheScope, commitCandleFetch],
  );

  const loadCandles = useCallback(
    async ({ silent = false, forceRefresh = false }: LoadCandlesOptions = {}) => {
      if (!chartConfig || !authenticatedCacheScope) {
        candleRequestsRef.current.invalidate();
        chartBackgroundControllerRef.current?.abort();
        chartBackgroundControllerRef.current = null;
        setCandles([]);
        setLiveCandle(null);
        setStreamPrice(null);
        setError(null);
        setLivePriceError(null);
        setLastLoadedAt(null);
        setLoading(false);
        setRefreshing(false);
        return;
      }

      if (forceRefresh) {
        // Every lane can touch canonical candle state. Supersede older additive
        // work before a full refresh so late history/repair/live responses cannot
        // mutate the refreshed chart, even if their transport ignores abort.
        invalidateChartRequestLanes([
          historyRequestsRef.current,
          repairRequestsRef.current,
          liveRequestsRef.current,
        ]);
        setHistoryLoading(false);
        setGapRepairing(false);
      }

      const now = new Date();
      const queryWindow = buildBotChartQuery(chartConfig, now);
      const initialQueryWindow = buildInitialBotChartQuery(chartConfig, now);
      const cacheKeyInput = {
        userScope: authenticatedCacheScope,
        contractId: chartConfig.contract_id,
        symbol: chartConfig.symbol,
        live: false,
        unit: chartConfig.timeframe_unit,
        unitNumber: chartConfig.timeframe_unit_number,
      } as const;
      invalidateLegacyBotCandleCache(cacheKeyInput);
      const cacheKey = buildBotCandleCacheKey(cacheKeyInput);
      const cachedEntry = forceRefresh ? null : readBotCandleCache(cacheKey);
      const cachedCandles = cachedEntry
        ? filterCandlesForChartContext(filterMarketCandlesForWindow(cachedEntry.candles, queryWindow), chartConfig)
        : [];
      const plan = forceRefresh
        ? null
        : planBotCandleFetches({
            targetWindow: queryWindow,
            initialWindow: initialQueryWindow,
            cache:
              cachedEntry && cachedCandles.length > 0
                ? {
                    candles: cachedCandles,
                    savedAt: cachedEntry.savedAt,
                    coverage: cachedEntry.coverage,
                  }
                : null,
            unit: chartConfig.timeframe_unit,
            unitNumber: chartConfig.timeframe_unit_number,
            now,
            maxRepairWindows: MAX_GAP_REPAIR_WINDOWS,
          });
      const foregroundRequests: BotCandleFetchRequest[] = forceRefresh
        ? [
            {
              reason: "stale-tail",
              priority: "foreground",
              window: { start: queryWindow.start, end: queryWindow.end },
              limit: queryWindow.limit,
              repair: false,
            },
          ]
        : plan?.foreground ?? [];
      const backgroundRequests = plan?.background ?? [];

      if (cachedEntry && cachedCandles.length > 0) {
        // Paint every valid cached row immediately. In-memory rows already on
        // screen may include deeper paged history, so preserve them on polls.
        if (candlesRef.current.length === 0) {
          setCandles(cachedCandles);
          candlesRef.current = cachedCandles;
          setLastLoadedAt(cachedEntry.savedAt);
        }
        const pendingTiming = pendingSignalChartLoadTimingRef.current;
        if (pendingTiming?.contextKey === chartViewportKey) {
          pendingTiming.cacheHit = true;
          if (pendingTiming.kind === "cold") {
            pendingTiming.kind = "warm";
          }
        }
        setLoading(false);
        if (!silent && foregroundRequests.length > 0) {
          setRefreshing(true);
        }
      } else if (forceRefresh) {
        setRefreshing(true);
      } else if (!silent && foregroundRequests.length > 0) {
        setLoading(true);
      }
      setError(null);

      if (foregroundRequests.length === 0) {
        setLoading(false);
        setRefreshing(false);
        if (cachedCandles.length > 0) {
          setServedFromCacheOnly(false);
        }
        if (backgroundRequests.length > 0) {
          scheduleBackgroundCandleFetches({
            cacheKey,
            cacheLimit: queryWindow.limit,
            config: chartConfig,
            contextKey: chartViewportKey,
            requests: backgroundRequests,
          });
        }
        return;
      }

      const request = candleRequestsRef.current.begin(chartViewportKey);
      const timeoutId = window.setTimeout(() => request.controller.abort(), CANDLE_REQUEST_TIMEOUT_MS);
      requestTimeoutIdsRef.current.add(timeoutId);
      try {
        for (const fetchRequest of foregroundRequests) {
          const pendingTiming = pendingSignalChartLoadTimingRef.current;
          if (pendingTiming?.contextKey === chartViewportKey) {
            pendingTiming.requestCount += 1;
          }
          const fetchedRows = await requestProjectXCandles(
            authenticatedCacheScope,
            {
              ...candleQueryForFetchRequest(chartConfig, fetchRequest),
              refresh: forceRefresh,
            },
            "foreground",
            request.signal,
          );
          if (!candleRequestsRef.current.accepts(request, chartViewportKeyRef.current)) {
            return;
          }
          const rows = filterCandlesForChartContext(fetchedRows, chartConfig);
          commitCandleFetch({
            cacheKey,
            cacheLimit: queryWindow.limit,
            contextKey: chartViewportKey,
            request: fetchRequest,
            rows,
            replaceCache: forceRefresh,
            mutation: forceRefresh ? "refresh" : undefined,
          });
        }
        if (forceRefresh) {
          // Retry previously confirmed-empty holes after an explicit provider refresh.
          repairedGapKeysRef.current = new Set();
          setRepairVersion((current) => current + 1);
        } else if (backgroundRequests.length > 0) {
          scheduleBackgroundCandleFetches({
            cacheKey,
            cacheLimit: queryWindow.limit,
            config: chartConfig,
            contextKey: chartViewportKey,
            requests: backgroundRequests,
          });
        }
      } catch (err) {
        if (!candleRequestsRef.current.accepts(request, chartViewportKeyRef.current)) {
          return;
        }
        if (silent && !forceRefresh && candlesRef.current.length > 0) {
          // Keep showing what we have, but surface that the data is no longer refreshing.
          setServedFromCacheOnly(true);
          return;
        }
        if (candlesRef.current.length > 0) {
          setServedFromCacheOnly(true);
        }
        if (isAbortError(err)) {
          setError("Timed out loading chart candles. Try Refresh, or check the ProjectX history connection.");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load chart candles");
        }
      } finally {
        window.clearTimeout(timeoutId);
        requestTimeoutIdsRef.current.delete(timeoutId);
        if (candleRequestsRef.current.finish(request)) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [authenticatedCacheScope, chartConfig, chartViewportKey, commitCandleFetch, scheduleBackgroundCandleFetches],
  );

  const loadLivePrice = useCallback(async ({ force = false }: LoadLivePriceOptions = {}) => {
    if (!chartConfig || !authenticatedCacheScope) {
      liveRequestsRef.current.invalidate();
      setLiveCandle(null);
      setStreamPrice(null);
      setLivePriceError(null);
      return;
    }

    if (liveRequestsRef.current.hasActiveRequest && !force) {
      return;
    }

    const request = liveRequestsRef.current.begin(chartViewportKey);
    const timeoutId = window.setTimeout(() => request.controller.abort(), LIVE_PRICE_REQUEST_TIMEOUT_MS);
    requestTimeoutIdsRef.current.add(timeoutId);
    const queryWindow = buildBotLivePriceQuery(chartConfig);

    try {
      const fetchedRows = await requestProjectXCandles(authenticatedCacheScope, {
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
      }, "foreground", request.signal);
      if (!liveRequestsRef.current.accepts(request, chartViewportKeyRef.current)) {
        return;
      }
      const rows = filterCandlesForChartContext(fetchedRows, chartConfig);

      // Promote closed candles from the live window into the chart immediately so a
      // just-finished bar does not vanish until the next slow poll.
      const closedRows = rows.filter((row) => !row.is_partial);
      if (closedRows.length > 0) {
        const cacheKey = buildBotCandleCacheKey({
          userScope: authenticatedCacheScope,
          contractId: chartConfig.contract_id,
          symbol: chartConfig.symbol,
          live: false,
          unit: chartConfig.timeframe_unit,
          unitNumber: chartConfig.timeframe_unit_number,
        });
        commitCandleFetch({
          cacheKey,
          cacheLimit: BOT_CHART_MAX_BARS,
          contextKey: chartViewportKey,
          request: {
            reason: "stale-tail",
            priority: "foreground",
            window: { start: queryWindow.start, end: queryWindow.end },
            limit: queryWindow.limit,
            repair: false,
          },
          rows: closedRows,
        });
      }

      const latest = getLatestMarketCandle(rows);
      // Never let a 10s-old REST candle clobber a fresher stream-built bucket.
      setLiveCandle((current) => {
        if (!latest) {
          return current;
        }
        const canonicalClosed = candlesRef.current.find(
          (row) => !row.is_partial && marketCandlesShareTimestamp(row, latest),
        );
        if (latest.is_partial && canonicalClosed) {
          liveCandleRef.current = canonicalClosed;
          return canonicalClosed;
        }
        if (!current) {
          liveCandleRef.current = latest;
          return latest;
        }
        const currentMs = Date.parse(current.timestamp);
        const latestMs = Date.parse(latest.timestamp);
        if (Number.isFinite(currentMs) && Number.isFinite(latestMs) && latestMs < currentMs) {
          return current;
        }
        if (!current.is_partial && latest.is_partial && marketCandlesShareTimestamp(current, latest)) {
          return current;
        }
        if (
          current.is_partial &&
          latest.is_partial &&
          marketCandlesShareTimestamp(current, latest) &&
          marketCandleFetchedAtMs(current) > marketCandleFetchedAtMs(latest)
        ) {
          return current;
        }
        liveCandleRef.current = latest;
        return latest;
      });
      setLivePriceError(latest ? null : "No live price was returned.");
    } catch (err) {
      if (!liveRequestsRef.current.accepts(request, chartViewportKeyRef.current)) {
        return;
      }
      setLivePriceError(isAbortError(err) ? "Timed out loading live price." : err instanceof Error ? err.message : "Failed to load live price.");
    } finally {
      window.clearTimeout(timeoutId);
      requestTimeoutIdsRef.current.delete(timeoutId);
      liveRequestsRef.current.finish(request);
    }
  }, [authenticatedCacheScope, chartConfig, chartViewportKey, commitCandleFetch]);

  const loadOlderCandles = useCallback(async () => {
    const config = chartConfigRef.current;
    const loadedCandles = candlesRef.current;
    if (
      !config ||
      loadedCandles.length === 0 ||
      loadedCandles.length >= MAX_LOADED_BARS ||
      !hasMoreHistoryRef.current ||
      historyRequestsRef.current.hasActiveRequest
    ) {
      return;
    }

    const earliest = loadedCandles[0];
    const queryWindow = buildOlderCandlesQuery(config, earliest.timestamp);
    if (!queryWindow) {
      return;
    }

    const contextKey = chartViewportKeyRef.current;
    const request = historyRequestsRef.current.begin(contextKey);
    const timeoutId = window.setTimeout(() => request.controller.abort(), CANDLE_REQUEST_TIMEOUT_MS);
    requestTimeoutIdsRef.current.add(timeoutId);
    setHistoryLoading(true);
    try {
      if (!authenticatedCacheScope) {
        return;
      }
      const fetchedRows = await requestProjectXCandles(authenticatedCacheScope, {
        contractId: config.contract_id,
        symbol: config.symbol ?? undefined,
        start: queryWindow.start,
        end: queryWindow.end,
        live: false,
        unit: config.timeframe_unit,
        unitNumber: config.timeframe_unit_number,
        limit: queryWindow.limit,
        includePartialBar: false,
      }, "foreground", request.signal);
      if (!historyRequestsRef.current.accepts(request, chartViewportKeyRef.current)) {
        return;
      }
      const rows = filterCandlesForChartContext(fetchedRows, config);

      const earliestMs = Date.parse(earliest.timestamp);
      const olderRows = rows.filter((row) => {
        const rowMs = Date.parse(row.timestamp);
        return Number.isFinite(rowMs) && rowMs < earliestMs;
      });
      if (olderRows.length === 0) {
        hasMoreHistoryRef.current = false;
        setHasMoreHistory(false);
        return;
      }

      const cacheKey = buildBotCandleCacheKey({
        userScope: authenticatedCacheScope,
        contractId: config.contract_id,
        symbol: config.symbol,
        live: false,
        unit: config.timeframe_unit,
        unitNumber: config.timeframe_unit_number,
      });
      commitCandleFetch({
        cacheKey,
        cacheLimit: BOT_CHART_MAX_BARS,
        contextKey,
        request: {
          reason: "missing-history",
          priority: "foreground",
          window: { start: queryWindow.start, end: queryWindow.end },
          limit: queryWindow.limit,
          repair: false,
        },
        rows: olderRows,
      });
      if (candlesRef.current.length >= MAX_LOADED_BARS) {
        hasMoreHistoryRef.current = false;
        setHasMoreHistory(false);
      }
    } catch (err) {
      if (historyRequestsRef.current.accepts(request, chartViewportKeyRef.current) && !isAbortError(err)) {
        setError(err instanceof Error ? err.message : "Failed to load older candles");
      }
    } finally {
      window.clearTimeout(timeoutId);
      requestTimeoutIdsRef.current.delete(timeoutId);
      if (historyRequestsRef.current.finish(request)) {
        setHistoryLoading(false);
      }
    }
  }, [authenticatedCacheScope, commitCandleFetch]);

  const repairDataGaps = useCallback(async () => {
    const config = chartConfigRef.current;
    const cacheScope = authenticatedCacheScope;
    if (!config || !cacheScope || repairRequestsRef.current.hasActiveRequest) {
      return;
    }

    const gaps = findCandleGaps(candlesRef.current, config.timeframe_unit, config.timeframe_unit_number).filter(
      (gap) => gap.kind === "data" && !repairedGapKeysRef.current.has(buildGapRangeKey(gap)),
    );
    if (gaps.length === 0) {
      return;
    }

    const windows = buildGapRepairWindows(gaps, config.timeframe_unit, config.timeframe_unit_number, MAX_GAP_REPAIR_WINDOWS);
    if (windows.length === 0) {
      return;
    }

    const contextKey = chartViewportKeyRef.current;
    const request = repairRequestsRef.current.begin(contextKey);
    const timeoutId = window.setTimeout(() => request.controller.abort(), CANDLE_REQUEST_TIMEOUT_MS);
    requestTimeoutIdsRef.current.add(timeoutId);
    setGapRepairing(true);
    try {
      const repairedRows: ProjectXMarketCandle[] = [];
      for (const window_ of windows) {
        const fetchedRows = await requestProjectXCandles(cacheScope, {
          contractId: config.contract_id,
          symbol: config.symbol ?? undefined,
          start: window_.start,
          end: window_.end,
          live: false,
          unit: config.timeframe_unit,
          unitNumber: config.timeframe_unit_number,
          limit: BOT_CHART_MAX_BARS,
          includePartialBar: false,
          repair: true,
        }, "foreground", request.signal);
        if (!repairRequestsRef.current.accepts(request, chartViewportKeyRef.current)) {
          return;
        }
        const rows = filterCandlesForChartContext(fetchedRows, config);
        if (rows.length > 0) {
          repairedRows.push(...rows);
        }
      }
      if (repairedRows.length > 0) {
        const previousRows = candlesRef.current;
        const merged = upsertMarketCandles(previousRows, repairedRows, MAX_LOADED_BARS);
        if (merged !== previousRows) {
          queueViewportRestore("pagination", previousRows, merged);
          candlesRef.current = merged;
          setCandles(merged);
        }

        const cacheKey = buildBotCandleCacheKey({
          userScope: cacheScope,
          contractId: config.contract_id,
          symbol: config.symbol,
          live: false,
          unit: config.timeframe_unit,
          unitNumber: config.timeframe_unit_number,
        });
        const previousEntry = readBotCandleCache(cacheKey);
        const cacheRows = mergeMarketCandles(previousEntry?.candles ?? [], repairedRows, BOT_CHART_MAX_BARS);
        const coverage = windows.reduce<CandleQueryWindow | null>(
          (current, window_) => mergeCandleCacheCoverage(current, window_),
          previousEntry?.coverage ?? null,
        );
        writeBotCandleCache(cacheKey, cacheRows, cacheLimitForRows(BOT_CHART_MAX_BARS, cacheRows.length), {
          savedAt: new Date(),
          coverage,
        });
      }

      // Only gaps covered by this bounded pass can be confirmed empty. Remaining
      // unattempted windows stay visible and repairable.
      const remaining = findCandleGaps(candlesRef.current, config.timeframe_unit, config.timeframe_unit_number);
      for (const gap of remaining) {
        if (gap.kind === "data" && isGapCoveredByRepairWindows(gap, windows)) {
          repairedGapKeysRef.current.add(buildGapRangeKey(gap));
        }
      }
      setRepairVersion((current) => current + 1);
    } catch (err) {
      if (repairRequestsRef.current.accepts(request, chartViewportKeyRef.current) && !isAbortError(err)) {
        setError(err instanceof Error ? err.message : "Failed to backfill candle gaps");
      }
    } finally {
      window.clearTimeout(timeoutId);
      requestTimeoutIdsRef.current.delete(timeoutId);
      if (repairRequestsRef.current.finish(request)) {
        setGapRepairing(false);
      }
    }
  }, [authenticatedCacheScope, queueViewportRestore]);

  const applyLiveStreamPrice = useCallback(
    (price: ProjectXMarketPrice) => {
      if (!chartConfig) {
        return;
      }

      setStreamPrice(price);
      setLivePriceError(null);

      // Fold the tick into the active candle so the bar itself moves, instead of
      // only the axis price label. Volume stays whatever the REST partial bar
      // last reported; the 10s live poll reconciles it.
      const currentLiveCandle = liveCandleRef.current;
      const nextLiveCandle = buildLiveCandleFromPriceUpdate({
        config: chartConfig,
        price,
        closedCandles: candlesRef.current,
        currentLiveCandle,
      });
      if (!nextLiveCandle || nextLiveCandle === currentLiveCandle) {
        return;
      }

      const previousVisibleRows = mergeLiveCandle(candlesRef.current, currentLiveCandle);
      // On bucket rollover, keep the finished partial bar on the chart until an
      // authoritative closed bar replaces it.
      if (currentLiveCandle && !marketCandlesShareTimestamp(currentLiveCandle, nextLiveCandle)) {
        const merged = upsertMarketCandles(candlesRef.current, [currentLiveCandle], MAX_LOADED_BARS);
        candlesRef.current = merged;
        setCandles(merged);
      }
      queueViewportRestore("live", previousVisibleRows, mergeLiveCandle(candlesRef.current, nextLiveCandle));
      liveCandleRef.current = nextLiveCandle;
      setLiveCandle(nextLiveCandle);
    },
    [chartConfig, queueViewportRestore],
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
    const theme = readBotChartThemeColors();

    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 1),
      height: Math.max(container.clientHeight, 320),
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: theme.label,
      },
      grid: {
        vertLines: { color: theme.grid },
        horzLines: { color: theme.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      localization: {
        locale: "en-US",
        timeFormatter: formatEasternCrosshairTime,
      },
      rightPriceScale: {
        borderColor: theme.border,
        scaleMargins: {
          top: 0.08,
          bottom: 0.12,
        },
      },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: true,
        tickMarkFormatter: formatEasternTickMark,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: theme.positive,
      downColor: theme.negative,
      borderUpColor: theme.positive,
      borderDownColor: theme.negative,
      wickUpColor: theme.positive,
      wickDownColor: theme.negative,
      priceLineVisible: false,
      lastValueVisible: false,
      autoscaleInfoProvider: () =>
        buildVisibleCandleAutoscaleInfo(
          chartCandlesRef.current,
          chart.timeScale().getVisibleLogicalRange(),
          evaluationPricesRef.current,
        ),
    });
    const fastSeries = chart.addSeries(LineSeries, {
      color: theme.accent,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const slowSeries = chart.addSeries(LineSeries, {
      color: theme.warning,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const vwapSeries = chart.addSeries(LineSeries, {
      color: theme.secondary,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      },
      1,
    );
    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.12,
        bottom: 0,
      },
    });
    const panes = chart.panes();
    panes[0]?.setStretchFactor(4);
    panes[1]?.setStretchFactor(1);
    const markers = createSeriesMarkers(candleSeries);
    chartHandlesRef.current = { chart, candleSeries, volumeSeries, fastSeries, slowSeries, vwapSeries, markers };
    const handleThemeChange = () => {
      const nextTheme = readBotChartThemeColors();
      chart.applyOptions({
        layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: nextTheme.label },
        grid: { vertLines: { color: nextTheme.grid }, horzLines: { color: nextTheme.grid } },
        rightPriceScale: { borderColor: nextTheme.border },
        timeScale: { borderColor: nextTheme.border },
      });
      candleSeries.applyOptions({
        upColor: nextTheme.positive,
        downColor: nextTheme.negative,
        borderUpColor: nextTheme.positive,
        borderDownColor: nextTheme.negative,
        wickUpColor: nextTheme.positive,
        wickDownColor: nextTheme.negative,
      });
      fastSeries.applyOptions({ color: nextTheme.accent });
      slowSeries.applyOptions({ color: nextTheme.warning });
      vwapSeries.applyOptions({ color: nextTheme.secondary });
    };
    window.addEventListener(APP_THEME_CHANGED_EVENT, handleThemeChange);

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
      chart.resize(Math.max(container.clientWidth, 1), Math.max(container.clientHeight, 240));
      requestDrawingOverlayUpdate();
    };
    resize();
    chart.subscribeCrosshairMove(handleCrosshairMove);
    chart.timeScale().subscribeVisibleLogicalRangeChange(requestDrawingOverlayUpdate);
    const handleVisibleRangeForHistory = (range: LogicalRange | null) => {
      autoHistoryHookRef.current?.(range);
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleVisibleRangeForHistory);

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

      container.focus({ preventScroll: true });

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
      const chartHasFocus = document.activeElement === container || container.contains(document.activeElement);
      if (
        (event.key === "Backspace" || event.key === "Delete") &&
        chartHasFocus &&
        selectedDrawingIdRef.current &&
        !isEditableEventTarget(event.target)
      ) {
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
      window.removeEventListener(APP_THEME_CHANGED_EVENT, handleThemeChange);
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(requestDrawingOverlayUpdate);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleVisibleRangeForHistory);
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
      appliedSeriesStateRef.current = null;
      livePriceLineRef.current = null;
      liquidityPriceLinesRef.current = {};
      evaluationPriceLinesRef.current = {};
      evaluationPricesRef.current = [];
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
  }, [chartConfig?.timeframe_unit]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    const timeframeKey = chartViewportKey;
    const nextState: AppliedSeriesState = {
      timeframeKey,
      candles: chartCandles,
      volume: visibleChartLayers.volume ? volumeData : [],
      fast: visibleChartLayers.fastSma ? fastAverage : [],
      slow: visibleChartLayers.slowSma ? slowAverage : [],
      vwap: visibleChartLayers.vwap ? vwap : [],
    };
    const previousState = appliedSeriesStateRef.current;
    const incremental = previousState !== null && previousState.timeframeKey === timeframeKey;

    applySeriesData(handles.candleSeries, incremental ? previousState.candles : null, nextState.candles);
    applySeriesData(handles.volumeSeries, incremental ? previousState.volume : null, nextState.volume);
    applySeriesData(handles.fastSeries, incremental ? previousState.fast : null, nextState.fast);
    applySeriesData(handles.slowSeries, incremental ? previousState.slow : null, nextState.slow);
    applySeriesData(handles.vwapSeries, incremental ? previousState.vwap : null, nextState.vwap);
    appliedSeriesStateRef.current = nextState;
    const restoreRange = chartCandles.length > 0 ? pendingViewportRestoreRef.current : null;
    if (chartCandles.length > 0) {
      pendingViewportRestoreRef.current = null;
    }
    if (viewportRestoreFrameRef.current !== null) {
      window.cancelAnimationFrame(viewportRestoreFrameRef.current);
      viewportRestoreFrameRef.current = null;
    }
    if (restoreRange) {
      viewportRestoreFrameRef.current = window.requestAnimationFrame(() => {
        viewportRestoreFrameRef.current = null;
        if (chartHandlesRef.current === handles) {
          handles.chart.timeScale().setVisibleLogicalRange(restoreRange);
        }
      });
    }
    const pendingTiming = pendingSignalChartLoadTimingRef.current;
    if (
      ENABLE_PERF_LOGS &&
      chartCandles.length > 0 &&
      pendingTiming &&
      !pendingTiming.measured &&
      pendingTiming.contextKey === timeframeKey
    ) {
      pendingTiming.measured = true;
      if (signalChartMeasureFrameRef.current !== null) {
        window.cancelAnimationFrame(signalChartMeasureFrameRef.current);
      }
      signalChartMeasureFrameRef.current = window.requestAnimationFrame(() => {
        signalChartMeasureFrameRef.current = null;
        if (pendingSignalChartLoadTimingRef.current !== pendingTiming) {
          return;
        }
        const endedAtMs = performance.now();
        const metricName = `topsignal:signal-chart:${pendingTiming.kind}-first-paint`;
        const detail = {
          contextKey: timeframeKey,
          cacheHit: pendingTiming.cacheHit,
          requestCount: pendingTiming.requestCount,
          barCount: chartCandles.length,
        };
        try {
          performance.measure(metricName, {
            start: pendingTiming.startedAtMs,
            end: endedAtMs,
            detail,
          });
        } catch {
          // Older browsers can omit PerformanceMeasureOptions.detail.
          performance.measure(metricName, {
            start: pendingTiming.startedAtMs,
            end: endedAtMs,
          });
        }
        logPerfInfo(
          "[perf][signal-chart] first-paint",
          JSON.stringify({
            metricName,
            durationMs: Math.round((endedAtMs - pendingTiming.startedAtMs) * 10) / 10,
            ...detail,
          }),
        );
      });
    }
    setDrawingOverlayRevision((current) => current + 1);
  }, [
    chartViewportKey,
    chartCandles,
    fastAverage,
    slowAverage,
    volumeData,
    visibleChartLayers.fastSma,
    visibleChartLayers.slowSma,
    visibleChartLayers.volume,
    visibleChartLayers.vwap,
    vwap,
  ]);

  useEffect(() => {
    const panes = chartHandlesRef.current?.chart.panes();
    if (!panes) {
      return;
    }
    panes[0]?.setStretchFactor(visibleChartLayers.volume ? 4 : 1);
    panes[1]?.setStretchFactor(visibleChartLayers.volume ? 1 : 0.01);
  }, [visibleChartLayers.volume]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }
    handles.markers.setMarkers(visibleSignalMarkers);
  }, [visibleSignalMarkers]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    const nextSides = new Set(visibleLiquidityLevels.map((level) => level.side));
    for (const side of ["buy", "sell"] as const) {
      const priceLine = liquidityPriceLinesRef.current[side];
      if (priceLine && !nextSides.has(side)) {
        handles.candleSeries.removePriceLine(priceLine);
        delete liquidityPriceLinesRef.current[side];
      }
    }

    for (const level of visibleLiquidityLevels) {
      const options = liquidityLevelToPriceLineOptions(level);
      const existingLine = liquidityPriceLinesRef.current[level.side];
      if (existingLine) {
        existingLine.applyOptions(options);
      } else {
        liquidityPriceLinesRef.current[level.side] = handles.candleSeries.createPriceLine(options);
      }
    }
  }, [visibleLiquidityLevels]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles) {
      return;
    }

    const levels: Partial<Record<EvaluationOverlayLevelRole, { price: number } | null>> = evaluationOverlayModel
      ? {
          entry: evaluationOverlayModel.geometry.entry,
          stop: evaluationOverlayModel.geometry.stop,
          target: evaluationOverlayModel.geometry.target,
        }
      : {};
    evaluationPricesRef.current = [levels.entry?.price, levels.stop?.price, levels.target?.price].filter(
      (price): price is number => typeof price === "number" && Number.isFinite(price),
    );
    for (const role of ["entry", "stop", "target"] as const) {
      const level = levels[role];
      const existingLine = evaluationPriceLinesRef.current[role];
      if (!level) {
        if (existingLine) {
          handles.candleSeries.removePriceLine(existingLine);
          delete evaluationPriceLinesRef.current[role];
        }
        continue;
      }

      const options = evaluationLevelToPriceLineOptions(
        role,
        level.price,
        evaluationOverlayModel?.staleness.status ?? "unknown",
      );
      if (existingLine) {
        existingLine.applyOptions(options);
      } else {
        evaluationPriceLinesRef.current[role] = handles.candleSeries.createPriceLine(options);
      }
    }
  }, [evaluationOverlayModel]);

  useEffect(() => {
    const handles = chartHandlesRef.current;
    if (!handles || !bot || closedChartCandles.length === 0) {
      return;
    }

    const fittedViewport = fittedViewportRef.current;
    if (fittedViewport?.key === chartViewportKey) {
      return;
    }

    handles.chart.timeScale().fitContent();
    fittedViewportRef.current = { key: chartViewportKey, candleCount: closedChartCandles.length };
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
      color: livePriceIsStale ? "rgb(245,158,11)" : "rgb(56,189,248)",
      lineWidth: 2 as const,
      lineStyle: LineStyle.Dashed,
      lineVisible: false,
      axisLabelVisible: true,
      title: livePriceIsStale ? "Stale" : livePricePoint?.isPartial ? "Live" : "Last",
      axisLabelColor: livePriceIsStale ? "rgb(217,119,6)" : "rgb(8,145,178)",
      axisLabelTextColor: "rgb(240,249,255)",
    };

    if (livePriceLineRef.current) {
      livePriceLineRef.current.applyOptions(priceLineOptions);
    } else {
      livePriceLineRef.current = handles.candleSeries.createPriceLine(priceLineOptions);
    }
  }, [livePrice, livePriceIsStale, livePricePoint?.isPartial]);

  useEffect(() => {
    // These requests use independent latest-wins lanes and merge with
    // closed-over-partial precedence, so starting them together gives a cached
    // chart its live quote immediately instead of waiting for history refresh.
    if (demoMode) {
      void loadCandles();
      return;
    }
    void runChartContextLoadsInParallel(
      () => loadCandles(),
      () => loadLivePrice({ force: true }),
    );
  }, [demoMode, loadCandles, loadLivePrice, refreshToken]);

  useEffect(() => {
    warmTimeframesControllerRef.current?.abort();
    const selectedBot = warmBotRef.current;
    if (demoMode || !selectedBot || !authenticatedCacheScope) {
      warmTimeframesControllerRef.current = null;
      return;
    }

    const controller = new AbortController();
    warmTimeframesControllerRef.current = controller;
    window.queueMicrotask(() => {
      if (controller.signal.aborted || warmTimeframesControllerRef.current !== controller) {
        return;
      }
      const now = new Date();
      let queuedCount = 0;
      for (const timeframe of BOT_CHART_TIMEFRAMES) {
        const config: BotConfig = {
          ...selectedBot,
          timeframe_unit: timeframe.unit,
          timeframe_unit_number: timeframe.unitNumber,
        };
        const initialWindow = buildInitialBotChartQuery(config, now);
        const cacheKey = buildBotCandleCacheKey({
          userScope: authenticatedCacheScope,
          contractId: config.contract_id,
          symbol: config.symbol,
          live: false,
          unit: config.timeframe_unit,
          unitNumber: config.timeframe_unit_number,
        });
        const cacheEntry = readBotCandleCache(cacheKey);
        const cachedCandles = cacheEntry
          ? filterMarketCandlesForWindow(cacheEntry.candles, initialWindow)
          : [];
        const plan = planBotCandleFetches({
          targetWindow: initialWindow,
          initialWindow,
          cache:
            cacheEntry && cachedCandles.length > 0
              ? {
                  candles: cachedCandles,
                  savedAt: cacheEntry.savedAt,
                  coverage: cacheEntry.coverage,
                }
              : null,
          unit: config.timeframe_unit,
          unitNumber: config.timeframe_unit_number,
          now,
          maxRepairWindows: 1,
        });

        for (const fetchRequest of plan.requests) {
          queuedCount += 1;
          const query = candleQueryForFetchRequest(config, fetchRequest);
          void requestProjectXCandles(authenticatedCacheScope, query, "background", controller.signal)
            .then((rows) => {
              commitCandleFetch({
                cacheKey,
                cacheLimit: initialWindow.limit,
                contextKey: `warm:${warmContractKey}:${timeframe.id}`,
                request: fetchRequest,
                rows: filterCandlesForChartContext(rows, config),
                applyToChart: false,
              });
            })
            .catch((error) => {
              if (!isAbortError(error)) {
                logPerfInfo("[perf][signal-chart] warm-error", {
                  contractKey: warmContractKey,
                  timeframe: timeframe.id,
                  error: error instanceof Error ? error.message : String(error),
                });
              }
            });
        }
      }
      logPerfInfo(
        "[perf][signal-chart] warm-queued",
        JSON.stringify({
          contractKey: warmContractKey,
          queuedCount,
          timeframes: BOT_CHART_TIMEFRAMES.map((timeframe) => timeframe.id),
        }),
      );
    });

    return () => {
      controller.abort();
      if (warmTimeframesControllerRef.current === controller) {
        warmTimeframesControllerRef.current = null;
      }
    };
  }, [authenticatedCacheScope, commitCandleFetch, demoMode, warmContractKey]);

  useEffect(() => {
    if (
      demoMode ||
      canonicalLoadVersion <= 0 ||
      automaticRepairLoadVersionRef.current === canonicalLoadVersion ||
      unrepairedDataGaps.length === 0 ||
      loading ||
      refreshing ||
      historyLoading ||
      gapRepairing
    ) {
      return;
    }

    // One bounded automatic pass per canonical load. Further holes remain
    // eligible on the next poll or through the explicit Backfill control.
    automaticRepairLoadVersionRef.current = canonicalLoadVersion;
    void repairDataGaps();
  }, [canonicalLoadVersion, demoMode, gapRepairing, historyLoading, loading, refreshing, repairDataGaps, unrepairedDataGaps.length]);

  useEffect(() => {
    const candleRequests = candleRequestsRef.current;
    const liveRequests = liveRequestsRef.current;
    const historyRequests = historyRequestsRef.current;
    const repairRequests = repairRequestsRef.current;
    const requestTimeoutIds = requestTimeoutIdsRef.current;
    return () => {
      candleRequests.dispose();
      liveRequests.dispose();
      historyRequests.dispose();
      repairRequests.dispose();
      chartBackgroundControllerRef.current?.abort();
      chartBackgroundControllerRef.current = null;
      warmTimeframesControllerRef.current?.abort();
      warmTimeframesControllerRef.current = null;
      for (const timeoutId of requestTimeoutIds) {
        window.clearTimeout(timeoutId);
      }
      requestTimeoutIds.clear();
      if (viewportRestoreFrameRef.current !== null) {
        window.cancelAnimationFrame(viewportRestoreFrameRef.current);
        viewportRestoreFrameRef.current = null;
      }
      if (signalChartMeasureFrameRef.current !== null) {
        window.cancelAnimationFrame(signalChartMeasureFrameRef.current);
        signalChartMeasureFrameRef.current = null;
      }
      if (liveStreamRenderTimeoutRef.current !== null) {
        window.clearTimeout(liveStreamRenderTimeoutRef.current);
        liveStreamRenderTimeoutRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    autoHistoryHookRef.current = (range: LogicalRange | null) => {
      if (demoMode || !range || Number(range.from) > HISTORY_AUTOLOAD_EDGE_BARS) {
        return;
      }
      void loadOlderCandles();
    };
    return () => {
      autoHistoryHookRef.current = null;
    };
  }, [demoMode, loadOlderCandles]);

  // Poll closed candles for any selected bot. Review of a stopped bot still
  // needs a fresh chart; the backend serves cached rows cheaply when fresh.
  useEffect(() => {
    if (demoMode || !bot || !authenticatedCacheScope) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void loadCandles({ silent: true });
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [authenticatedCacheScope, bot, demoMode, loadCandles]);

  useEffect(() => {
    if (demoMode || !bot) {
      lastLiveStreamEventAtRef.current = 0;
      pendingLiveStreamPriceRef.current = null;
      setStreamPrice(null);
      return;
    }

    lastLiveStreamEventAtRef.current = 0;
    pendingLiveStreamPriceRef.current = null;
    setStreamPrice(null);
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
          setStreamActive(true);
          scheduleLiveStreamPrice(price);
        },
        onError: () => {
          lastLiveStreamEventAtRef.current = 0;
          setStreamActive(false);
        },
      },
    );

    return () => {
      stopStreaming();
      lastLiveStreamEventAtRef.current = 0;
      pendingLiveStreamPriceRef.current = null;
      setStreamActive(false);
      setStreamPrice(null);
      if (liveStreamRenderTimeoutRef.current !== null) {
        window.clearTimeout(liveStreamRenderTimeoutRef.current);
        liveStreamRenderTimeoutRef.current = null;
      }
    };
  }, [authenticatedCacheScope, bot, demoMode, scheduleLiveStreamPrice]);

  useEffect(() => {
    if (demoMode || !bot) {
      return;
    }

    const intervalId = window.setInterval(() => {
      const streamIsStale = Date.now() - lastLiveStreamEventAtRef.current > LIVE_PRICE_STREAM_STALE_MS;
      if (streamIsStale) {
        setStreamPrice(null);
        setStreamActive(false);
      }
      void loadLivePrice();
    }, LIVE_PRICE_POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [bot, demoMode, loadLivePrice]);

  const subtitle = bot
    ? buildChartSubtitle(bot, chartTimeframe)
    : "No bot selected";
  const toggleChartLayer = useCallback((layer: ChartLayerId) => {
    setVisibleChartLayers((current) => ({
      ...current,
      [layer]: !current[layer],
    }));
  }, []);
  const dataAgeMs = lastLoadedAt ? Date.now() - lastLoadedAt.getTime() : null;
  const dataIsStale = servedFromCacheOnly || (dataAgeMs !== null && dataAgeMs > STALE_DATA_AFTER_MS);
  const lastRefreshText = dataAgeMs === null ? null : refreshing ? "Refreshing" : `Refreshed ${formatDataAge(dataAgeMs)}`;
  const freshnessTitle = lastLoadedAt
    ? `Closed candles loaded ${lastLoadedFormatter.format(lastLoadedAt)} ET${
        servedFromCacheOnly ? ". Last refresh failed; showing cached data." : ""
      }`
    : undefined;
  const livePriceText =
    livePrice !== null
      ? `${livePriceIsStale ? "Stale" : livePricePoint?.isPartial ? "Live" : "Last"} ${priceFormatter.format(livePrice)}`
      : null;
  const livePriceTitle = livePricePoint
    ? `Price timestamp ${lastLoadedFormatter.format(new Date(livePricePoint.timestamp))} ET (${
        streamActive ? "streaming" : "polling"
      })`
    : undefined;
  const gapChipTitle =
    unrepairedDataGaps.length > 0
      ? unrepairedDataGaps
          .slice(0, 4)
          .map(
            (gap) =>
              `${gap.missingBars} bar${gap.missingBars === 1 ? "" : "s"} missing after ${lastLoadedFormatter.format(
                new Date(gap.fromMs),
              )} ET`,
          )
          .join("; ")
      : confirmedEmptyGapCount > 0
        ? `${confirmedEmptyGapCount} gap(s) confirmed empty at the provider (holiday or no trades)`
        : undefined;
  const computedLiquidityButtonTitle =
    liquidityLevels.length > 0
      ? "Move Buy liq and Sell liq to their computed swing liquidity levels"
      : "No computed liquidity levels are available yet";
  const drawingToolsDisabled = !bot || chartCandles.length === 0;
  const connectionState =
    !bot || livePrice === null ? "unavailable" : livePriceIsStale ? "stale" : streamActive ? "live" : "delayed";
  const latestBarState = latestOhlcCandle ? (latestOhlcCandle.isPartial ? "partial" : "closed") : "none";
  const keyboardHelpId = `bot-chart-keyboard-help-${bot?.id ?? "none"}`;
  const chartViewState = resolveBotChartViewState({
    hasBot: bot !== null,
    loading,
    error,
    candleCount: chartCandles.length,
  });
  const fitPriceScaleToVisibleRange = useCallback(() => {
    const handles = chartHandlesRef.current;
    if (!handles || chartCandlesRef.current.length === 0) {
      return;
    }

    handles.candleSeries.priceScale().setAutoScale(true);
    setDrawingOverlayRevision((current) => current + 1);
  }, []);
  const handleChartKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.altKey || event.ctrlKey || event.metaKey || isEditableEventTarget(event.target)) {
        return;
      }
      const handles = chartHandlesRef.current;
      if (!handles) {
        return;
      }

      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const chartRows = chartCandlesRef.current;
        if (chartRows.length === 0) {
          return;
        }
        const currentIndex =
          hoveredCandleTimeRef.current === null
            ? chartRows.length - 1
            : chartRows.findIndex((row) => Number(row.time) === hoveredCandleTimeRef.current);
        const nextIndex = clampNumber(
          (currentIndex < 0 ? chartRows.length - 1 : currentIndex) + (event.key === "ArrowLeft" ? -1 : 1),
          0,
          chartRows.length - 1,
        );
        const nextRow = chartRows[nextIndex];
        handles.chart.setCrosshairPosition(nextRow.close, nextRow.time, handles.candleSeries);
        hoveredCandleTimeRef.current = Number(nextRow.time);
        setHoveredCandle(hoverCandlesByTimeRef.current.get(Number(nextRow.time)) ?? null);
        handles.chart.timeScale().scrollToPosition(
          handles.chart.timeScale().scrollPosition() + (event.key === "ArrowLeft" ? 1 : -1),
          false,
        );
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        handles.chart.timeScale().fitContent();
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        handles.chart.timeScale().scrollToRealTime();
        const latestRow = chartCandlesRef.current[chartCandlesRef.current.length - 1];
        if (latestRow) {
          handles.chart.setCrosshairPosition(latestRow.close, latestRow.time, handles.candleSeries);
          hoveredCandleTimeRef.current = Number(latestRow.time);
          setHoveredCandle(hoverCandlesByTimeRef.current.get(Number(latestRow.time)) ?? null);
        }
        return;
      }
      if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        fitPriceScaleToVisibleRange();
        return;
      }
      if (event.key === "Escape") {
        setDrawingTool("cursor");
      }
    },
    [fitPriceScaleToVisibleRange],
  );
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
        livePricePoint,
      ),
    [drawingAnchorPreview, drawingDraft, drawingOverlayRevision, drawings, livePricePoint, selectedDrawingId],
  );

  return (
    <Card className="flex flex-col pb-3 md:pb-3">
      <CardHeader className="shrink-0 !space-y-0">
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <CardTitle>Signal Chart</CardTitle>
            <CardDescription className="mt-1">{subtitle}</CardDescription>
            <div className="mt-2">
              {demoMode ? (
                <div className="inline-flex min-h-8 items-center rounded-md border border-app-accent/35 bg-app-accent/10 px-2.5 text-xs font-semibold text-app-accent" role="note">
                  Demo snapshot · closed sample bars · live quote disabled
                </div>
              ) : <BotChartStatus
                connection={connectionState}
                connectionTitle={livePriceError ?? livePriceTitle ?? undefined}
                barState={latestBarState}
                lastRefreshText={lastRefreshText}
                lastRefreshTitle={freshnessTitle}
                stale={dataIsStale}
                unrepairedGapCount={unrepairedDataGaps.length}
                timeframeLabel={chartTimeframe.label}
                timezoneLabel="ET"
              />}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 md:justify-end">
            {demoMode ? (
              <span className="inline-flex min-h-8 items-center rounded-md border border-app-accent/35 bg-app-accent/10 px-2.5 text-xs font-semibold text-app-accent">
                Demo snapshot
              </span>
            ) : livePriceText ? (
              <span
                className={`inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-md border px-2.5 text-xs font-semibold ${
                  livePriceIsStale || livePriceError
                    ? "border-app-warning/35 bg-app-warning/10 text-app-warning"
                    : "border-app-accent/35 bg-app-accent/10 text-app-accent"
                }`}
                title={livePriceError ?? livePriceTitle}
              >
                <span className={`h-2 w-2 rounded-full bg-current ${streamActive && !livePriceIsStale ? "animate-pulse" : "opacity-50"}`} />
                {livePriceText}
              </span>
            ) : bot && livePriceError ? (
              <span
                className="inline-flex h-8 items-center rounded-md border border-app-warning/35 bg-app-warning/10 px-2.5 text-xs font-semibold text-app-warning"
                title={livePriceError}
              >
                Live price unavailable
              </span>
            ) : null}
            {!demoMode && bot && unrepairedDataGaps.length > 0 ? (
              <span
                className="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-md border border-app-warning/35 bg-app-warning/10 pl-2.5 pr-1 text-xs font-semibold text-app-warning"
                title={gapChipTitle}
              >
                {unrepairedDataGaps.length} data gap{unrepairedDataGaps.length === 1 ? "" : "s"}
                <button
                  type="button"
                  onClick={() => void repairDataGaps()}
                  disabled={gapRepairing}
                  className="rounded bg-app-warning/15 px-1.5 py-0.5 text-[11px] font-semibold text-app-warning transition hover:bg-app-warning/25 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {gapRepairing ? "Backfilling" : "Backfill"}
                </button>
              </span>
            ) : null}
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-app-border/80 bg-app-bg/40 p-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex min-h-11 overflow-hidden rounded-md border border-app-border-strong/70 bg-app-surface/95 shadow-[0_10px_24px_-22px_rgb(var(--theme-shadow-color)/0.55)] sm:min-h-9" aria-label="Chart timeframe">
              {BOT_CHART_TIMEFRAMES.map((option) => {
                const active = option.id === selectedTimeframeId;
                return (
                  <button
                    key={option.id}
                    type="button"
                    aria-pressed={active}
                    aria-label={`Show ${option.label} candles`}
                    onClick={() => setTimeframeSelection({ key: botTimeframeSelectionKey, id: option.id })}
                    disabled={!bot}
                    className={`min-h-11 min-w-11 border-r border-app-border/80 px-2.5 text-xs font-semibold transition last:border-r-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/45 disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-9 ${
                      active
                        ? "bg-app-accent/15 text-app-accent shadow-[inset_0_0_0_1px_rgb(var(--theme-accent)/0.32)]"
                        : "text-app-text-soft hover:bg-app-accent/10 hover:text-app-text"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
            <div className="inline-flex min-h-11 overflow-hidden rounded-md border border-app-border-strong/70 bg-app-surface/95 shadow-[0_10px_24px_-22px_rgb(var(--theme-shadow-color)/0.55)] sm:min-h-9" aria-label="Chart drawing tools">
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
                  if (drawingStorageScope) {
                    clearBotDrawings(drawingStorageScope);
                  }
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
              onClick={() => void loadOlderCandles()}
              disabled={demoMode || !bot || loading || historyLoading || candles.length === 0 || !hasMoreHistory}
              title={
                demoMode
                  ? "Live history requests are disabled in Demo Mode."
                  : hasMoreHistory
                  ? "Load older candles. Panning to the left edge also loads more automatically."
                  : "No further history is available for this market and timeframe."
              }
            >
              <HistoryIcon />
              <span>{historyLoading ? "Loading older" : hasMoreHistory ? "Load older" : "No more history"}</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => {
                if (demoMode) {
                  return;
                }
                void loadCandles({ silent: true, forceRefresh: true });
                void loadLivePrice({ force: true });
              }}
              disabled={demoMode || !bot || loading || refreshing || historyLoading || gapRepairing}
              title={demoMode ? "Live market refresh is disabled in Demo Mode." : undefined}
            >
              <RefreshIcon />
              <span>{refreshing ? "Refreshing" : "Refresh"}</span>
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-col">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-app-border bg-app-bg/35 px-3 py-2 text-xs text-app-muted">
          {showAverageLayers ? (
            <>
              <LegendDot
                active={visibleChartLayers.fastSma}
                className="bg-cyan-400"
                label={`${usesEmaLayers ? "Fast EMA" : "Fast SMA"} ${bot?.fast_period ?? "-"}`}
                onClick={() => toggleChartLayer("fastSma")}
              />
              <LegendDot
                active={visibleChartLayers.slowSma}
                className="bg-yellow-300"
                label={`${usesEmaLayers ? "Slow EMA" : "Slow SMA"} ${bot?.slow_period ?? "-"}`}
                onClick={() => toggleChartLayer("slowSma")}
              />
            </>
          ) : null}
          <LegendDot active={visibleChartLayers.vwap} className="bg-pink-400" label="VWAP" onClick={() => toggleChartLayer("vwap")} />
          <LegendDot active={visibleChartLayers.volume} className="bg-app-muted" label="Volume" onClick={() => toggleChartLayer("volume")} />
          <LegendDot
            active={visibleChartLayers.buySignals}
            className="bg-emerald-500"
            label="Buy"
            onClick={() => toggleChartLayer("buySignals")}
          />
          <LegendDot
            active={visibleChartLayers.sellSignals}
            className="bg-rose-500"
            label="Sell"
            onClick={() => toggleChartLayer("sellSignals")}
          />
          <LegendLine
            active={visibleChartLayers.buyLiquidity}
            className="border-emerald-500"
            label="Buy-side liquidity"
            onClick={() => toggleChartLayer("buyLiquidity")}
          />
          <LegendLine
            active={visibleChartLayers.sellLiquidity}
            className="border-rose-500"
            label="Sell-side liquidity"
            onClick={() => toggleChartLayer("sellLiquidity")}
          />
        </div>
        <div className="relative h-[420px] overflow-hidden rounded-xl border border-app-border bg-app-bg/45 md:h-[560px]">
          <div
            ref={containerRef}
            className="h-full w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-app-accent/60"
            role="region"
            tabIndex={0}
            aria-label={`${subtitle} candlestick and volume chart`}
            aria-describedby={keyboardHelpId}
            onKeyDown={handleChartKeyDown}
          />
          <span id={keyboardHelpId} className="sr-only">
            Use Left and Right Arrow to inspect adjacent candles and pan, Home to fit all data, End to inspect the latest candle, and F to fit the price scale.
          </span>
          <OhlcReadout candle={activeOhlcCandle} />
          <BotEvaluationOverlayStatus model={evaluationOverlayModel} />
          <DrawingOverlay overlay={drawingOverlay} />
          <BotChartStateOverlay state={chartViewState} />
          {historyLoading ? (
            <span className="absolute bottom-3 left-3 z-30 inline-flex h-7 items-center gap-2 rounded-md border border-app-border-strong bg-app-bg/85 px-2.5 text-[11px] font-medium text-app-text-soft shadow-lg shadow-app-bg/30 backdrop-blur" role="status">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-app-accent" />
              Loading older candles
            </span>
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
            className="absolute bottom-3 right-3 z-30 inline-flex h-11 items-center gap-1.5 rounded-md border border-app-accent/45 bg-app-surface/95 px-2.5 text-xs font-semibold text-app-text shadow-[0_14px_30px_-20px_rgb(var(--theme-shadow-color)/0.8)] backdrop-blur transition hover:border-app-accent/70 hover:bg-app-accent/15 hover:text-app-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/45 disabled:cursor-not-allowed disabled:opacity-45 sm:h-8"
          >
            <FitChartIcon />
            <span>Fit</span>
          </button>
        </div>
      </CardContent>
    </Card>
  );
}

function candleQueryForFetchRequest(config: BotConfig, request: BotCandleFetchRequest): CandleQuery {
  return {
    contractId: config.contract_id,
    symbol: config.symbol ?? undefined,
    start: request.window.start,
    end: request.window.end,
    live: false,
    unit: config.timeframe_unit,
    unitNumber: config.timeframe_unit_number,
    limit: request.limit,
    includePartialBar: false,
    repair: request.repair,
  };
}

function mergeCandleCacheCoverage(
  existing: CandleQueryWindow | null,
  incoming: CandleQueryWindow,
): CandleQueryWindow {
  if (!existing) {
    return incoming;
  }
  const existingStartMs = Date.parse(existing.start);
  const existingEndMs = Date.parse(existing.end);
  const incomingStartMs = Date.parse(incoming.start);
  const incomingEndMs = Date.parse(incoming.end);
  if (![existingStartMs, existingEndMs, incomingStartMs, incomingEndMs].every(Number.isFinite)) {
    return incoming;
  }
  return {
    start: new Date(Math.min(existingStartMs, incomingStartMs)).toISOString(),
    end: new Date(Math.max(existingEndMs, incomingEndMs)).toISOString(),
  };
}

function cacheLimitForRows(requestedLimit: number, rowCount: number): number {
  return Math.min(BOT_CHART_MAX_BARS, Math.max(1, Math.trunc(requestedLimit), Math.trunc(rowCount)));
}

function findMatchingTimeframeId(unit: BotTimeframeUnit, unitNumber: number): BotChartTimeframeId | null {
  const normalizedNumber = Math.max(1, Math.trunc(unitNumber));
  return (
    BOT_CHART_TIMEFRAMES.find((option) => option.unit === unit && option.unitNumber === normalizedNumber)?.id ?? null
  );
}

function defaultChartTimeframeIdForBot(bot: BotConfig | null): BotChartTimeframeId {
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

const MAX_INCREMENTAL_APPEND_BARS = 8;

interface ChartSeriesPoint {
  time: UTCTimestamp;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  value?: number;
  color?: string;
}

/**
 * Apply new series data with `update()` when only the tail changed (live ticks,
 * bar rollover), falling back to `setData()` for structural changes. This keeps
 * 250ms stream updates from forcing a full chart redraw.
 */
function applySeriesData<T extends ChartSeriesPoint>(
  series: { setData(data: T[]): void; update(bar: T): void },
  previousData: T[] | null,
  nextData: T[],
): void {
  if (previousData === nextData) {
    return;
  }
  if (!previousData || previousData.length === 0 || nextData.length === 0 || nextData.length < previousData.length) {
    series.setData(nextData);
    return;
  }

  const appendedCount = nextData.length - previousData.length;
  if (appendedCount > MAX_INCREMENTAL_APPEND_BARS) {
    series.setData(nextData);
    return;
  }

  // Everything before the previous last bar must be untouched, and the bar at
  // the previous tail index must keep its timestamp (update() cannot rewrite
  // history without historicalUpdate support).
  const sharedCount = previousData.length;
  for (let index = 0; index < sharedCount - 1; index += 1) {
    if (!isSameSeriesPoint(previousData[index], nextData[index])) {
      series.setData(nextData);
      return;
    }
  }
  if (Number(previousData[sharedCount - 1].time) !== Number(nextData[sharedCount - 1].time)) {
    series.setData(nextData);
    return;
  }

  for (let index = sharedCount - 1; index < nextData.length; index += 1) {
    if (index < sharedCount && isSameSeriesPoint(previousData[index], nextData[index])) {
      continue;
    }
    series.update(nextData[index]);
  }
}

function isSameSeriesPoint(left: ChartSeriesPoint, right: ChartSeriesPoint): boolean {
  return (
    Number(left.time) === Number(right.time) &&
    left.open === right.open &&
    left.high === right.high &&
    left.low === right.low &&
    left.close === right.close &&
    left.value === right.value &&
    left.color === right.color
  );
}

function buildVisibleCandleAutoscaleInfo(
  candles: CandlestickData<UTCTimestamp>[],
  logicalRange: LogicalRange | null,
  additionalPrices: readonly number[] = [],
): AutoscaleInfo | null {
  if (candles.length === 0) {
    return null;
  }

  const fromIndex = logicalRange ? Math.max(0, Math.floor(Number(logicalRange.from))) : 0;
  const toIndex = logicalRange ? Math.min(candles.length - 1, Math.ceil(Number(logicalRange.to))) : candles.length - 1;
  if (fromIndex > toIndex) {
    return null;
  }

  let minValue = Number.POSITIVE_INFINITY;
  let maxValue = Number.NEGATIVE_INFINITY;
  for (let index = fromIndex; index <= toIndex; index += 1) {
    const candle = candles[index];
    if (!candle) {
      continue;
    }

    minValue = Math.min(minValue, candle.low);
    maxValue = Math.max(maxValue, candle.high);
  }
  for (const price of additionalPrices) {
    if (Number.isFinite(price)) {
      minValue = Math.min(minValue, price);
      maxValue = Math.max(maxValue, price);
    }
  }

  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
    return null;
  }

  if (minValue === maxValue) {
    const padding = Math.max(Math.abs(minValue) * 0.001, 0.01);
    minValue -= padding;
    maxValue += padding;
  }

  return {
    priceRange: {
      minValue,
      maxValue,
    },
  };
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

function buildChartSubtitle(bot: BotConfig, chartTimeframe: BotChartTimeframe): string {
  const market = bot.symbol ?? bot.contract_id;
  const botTimeframeLabel = formatTimeframeLabel(bot.timeframe_unit, bot.timeframe_unit_number);
  if (botTimeframeLabel === chartTimeframe.label) {
    return `${market} / ${chartTimeframe.label}`;
  }
  return `${market} / ${chartTimeframe.label} chart / ${botTimeframeLabel} bot`;
}

function formatTimeframeLabel(unit: BotTimeframeUnit, unitNumber: number): string {
  const normalizedNumber = Math.max(1, Math.trunc(unitNumber));
  const preset = BOT_CHART_TIMEFRAMES.find((option) => option.unit === unit && option.unitNumber === normalizedNumber);
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

function evaluationLevelToPriceLineOptions(
  role: EvaluationOverlayLevelRole,
  price: number,
  freshness: EvaluationFreshnessStatus,
) {
  const colors: Record<EvaluationOverlayLevelRole, { line: string; axis: string }> = {
    entry: { line: "rgb(56,189,248)", axis: "rgb(2,132,199)" },
    stop: { line: "rgb(251,113,133)", axis: "rgb(225,29,72)" },
    target: { line: "rgb(52,211,153)", axis: "rgb(5,150,105)" },
  };
  const labels: Record<EvaluationOverlayLevelRole, string> = {
    entry: "Entry",
    stop: "Stop",
    target: "Target",
  };
  const color = colors[role];
  const stale = freshness === "stale";
  const freshnessPrefix = stale ? "Stale " : freshness === "unknown" ? "Freshness unknown · " : "";
  return {
    id: `evaluation-${role}`,
    price,
    color: stale ? "rgba(251,191,36,0.8)" : color.line,
    lineWidth: role === "entry" ? (2 as const) : (1 as const),
    lineStyle: role === "entry" ? LineStyle.Solid : LineStyle.Dashed,
    lineVisible: true,
    axisLabelVisible: true,
    title: `${freshnessPrefix}${labels[role]}`,
    axisLabelColor: stale ? "rgb(217,119,6)" : color.axis,
    axisLabelTextColor: "rgb(255,255,255)",
  };
}

function mergeLiveCandle(candles: ProjectXMarketCandle[], liveCandle: ProjectXMarketCandle | null): ProjectXMarketCandle[] {
  if (!liveCandle || !isRenderableMarketCandle(liveCandle)) {
    return candles;
  }
  return upsertMarketCandles(candles, [liveCandle]);
}

function marketCandleTimes(candles: ProjectXMarketCandle[]): UTCTimestamp[] {
  return buildCandlestickData(candles).map((candle) => candle.time);
}

function marketCandlesShareTimestamp(left: ProjectXMarketCandle, right: ProjectXMarketCandle): boolean {
  const leftMs = Date.parse(left.timestamp);
  const rightMs = Date.parse(right.timestamp);
  return Number.isFinite(leftMs) && leftMs === rightMs;
}

function filterCandlesForChartContext(
  candles: readonly ProjectXMarketCandle[],
  config: BotConfig,
): ProjectXMarketCandle[] {
  const contractId = config.contract_id.trim().toUpperCase();
  const symbol = config.symbol?.trim().toUpperCase() ?? null;
  return candles.filter((candle) => {
    if (
      candle.live ||
      candle.unit !== config.timeframe_unit ||
      candle.unit_number !== config.timeframe_unit_number
    ) {
      return false;
    }
    const candleSymbol = candle.symbol?.trim().toUpperCase() ?? null;
    return symbol && candleSymbol
      ? candleSymbol === symbol
      : candle.contract_id.trim().toUpperCase() === contractId;
  });
}

function marketCandleFetchedAtMs(candle: ProjectXMarketCandle): number {
  const fetchedAtMs = candle.fetched_at ? Date.parse(candle.fetched_at) : Number.NaN;
  return Number.isFinite(fetchedAtMs) ? fetchedAtMs : 0;
}

function getLatestMarketCandle(candles: ProjectXMarketCandle[]): ProjectXMarketCandle | null {
  let latest: ProjectXMarketCandle | null = null;
  let latestTimestampMs = Number.NEGATIVE_INFINITY;
  for (const candle of candles) {
    if (!isRenderableMarketCandle(candle)) {
      continue;
    }
    const timestampMs = Date.parse(candle.timestamp);
    if (timestampMs >= latestTimestampMs) {
      latest = candle;
      latestTimestampMs = timestampMs;
    }
  }
  return latest;
}

function findLatestClosedMarketCandle(candles: readonly ProjectXMarketCandle[]): ProjectXMarketCandle | null {
  for (let index = candles.length - 1; index >= 0; index -= 1) {
    const candle = candles[index];
    if (!candle.is_partial && isRenderableMarketCandle(candle)) {
      return candle;
    }
  }
  return null;
}

function isRenderableMarketCandle(candle: ProjectXMarketCandle): boolean {
  return (
    Number.isFinite(Date.parse(candle.timestamp)) &&
    [candle.open, candle.high, candle.low, candle.close].every(Number.isFinite)
  );
}

function buildHoverCandleMap(
  candles: ProjectXMarketCandle[],
  displayCandles: CandlestickData<UTCTimestamp>[] = buildCandlestickData(candles),
): Map<number, HoveredCandle> {
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

  displayCandles.forEach((candle, index) => {
    const sourceCandle = sourceCandlesByTime.get(Number(candle.time)) ?? null;
    const previousCandle = index > 0 ? displayCandles[index - 1] : null;
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

function formatDataAge(ageMs: number): string {
  const seconds = Math.max(0, Math.round(ageMs / 1000));
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m ago`;
}

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === "AbortError";
}
