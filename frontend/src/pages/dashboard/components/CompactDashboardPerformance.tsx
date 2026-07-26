import {
  memo,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { Card } from "../../../components/ui/Card";
import { cn } from "../../../components/ui/cn";
import { formatCurrency, formatPnl } from "../../../utils/formatters";
import type { CompactChartPoint } from "../compactDashboardData";
import {
  CompactPanel,
  CompactState,
  compactFocusRing,
} from "./CompactDashboardPrimitives";

const CHART_WIDTH = 420;
const CHART_HEIGHT = 236;
const CHART_PADDING = { top: 18, right: 14, bottom: 34, left: 54 };

const shortDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const compactCurrencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function clamp(value: number, min = 0, max = 100) {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}

function parseIsoDay(value: string) {
  return new Date(`${value}T00:00:00Z`);
}

function formatShortDate(value: string) {
  return shortDateFormatter.format(parseIsoDay(value));
}

function formatCompactCurrency(value: number) {
  return compactCurrencyFormatter.format(value);
}

export interface CompactScoreBreakdown {
  label: string;
  riskScore: number;
  consistencyScore: number;
  edgeScore: number;
  sampleSize: number;
  sampleConfidence: number;
}

export interface CompactPerformanceContext {
  tradingDayCount: number;
  maxDrawdown: number;
  riskBase: number | null;
  riskBaseLabel: string;
}

export const CompactContextStrip = memo(function CompactContextStrip({
  rangeLabel,
  context,
  scoreBreakdown,
  loading,
  error,
}: {
  rangeLabel: string;
  context: CompactPerformanceContext;
  scoreBreakdown: CompactScoreBreakdown | null;
  loading: boolean;
  error: string | null;
}) {
  const confidence = clamp((scoreBreakdown?.sampleConfidence ?? 0) * 100);
  const confidenceLabel = confidence >= 80 ? "High" : confidence >= 50 ? "Moderate" : "Low";
  const hasDrawdown = context.tradingDayCount > 0 && Number.isFinite(context.maxDrawdown);
  const hasDrawdownLoss = hasDrawdown && context.maxDrawdown > 0;
  const items = [
    {
      label: "Trading scope",
      value: rangeLabel,
      detail: `${context.tradingDayCount} trading day${context.tradingDayCount === 1 ? "" : "s"}`,
      wrap: true,
    },
    {
      label: "Max drawdown",
      value: hasDrawdown ? formatPnl(hasDrawdownLoss ? -Math.abs(context.maxDrawdown) : 0) : "Not available",
      detail: "Peak-to-trough loss",
      valueClassName: hasDrawdownLoss ? "text-app-negative-text" : undefined,
      wrap: false,
    },
    {
      label: "Risk base",
      value: context.riskBase !== null && context.riskBase > 0 ? formatCurrency(context.riskBase) : "Not available",
      detail: context.riskBaseLabel || "No reliable base",
      wrap: false,
    },
    {
      label: "Sample confidence",
      value: scoreBreakdown && scoreBreakdown.sampleSize > 0 ? `${Math.round(confidence)}%` : "Not available",
      detail: scoreBreakdown && scoreBreakdown.sampleSize > 0
        ? `${confidenceLabel} · ${scoreBreakdown.sampleSize} day sample`
        : "Requires trading-day data",
      wrap: false,
    },
  ];

  return (
    <Card aria-label="TopSignal context and risk" aria-busy={loading} className="overflow-hidden p-0 md:p-0">
      {loading ? (
        <div className="grid grid-cols-2 gap-px bg-app-border/40 sm:grid-cols-4" role="status" aria-live="polite">
          <span className="sr-only">Loading TopSignal context and risk</span>
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="space-y-2 bg-app-surface px-3 py-3 sm:px-4">
              <div className="h-3 w-20 animate-pulse rounded bg-app-border/45 motion-reduce:animate-none" />
              <div className="h-5 w-28 max-w-full animate-pulse rounded bg-app-border/45 motion-reduce:animate-none" />
              <div className="h-3 w-24 max-w-full animate-pulse rounded bg-app-border/30 motion-reduce:animate-none" />
            </div>
          ))}
        </div>
      ) : error ? (
        <CompactState kind="error" title="Context unavailable" detail={error} minHeightClassName="min-h-[104px]" announce={false} />
      ) : (
      <div className="grid grid-cols-2 gap-px bg-app-border/60 sm:grid-cols-4">
        {items.map((item) => (
          <div key={item.label} className="min-w-0 bg-app-surface px-3 py-3 sm:px-4">
            <p className="text-xs font-semibold uppercase tracking-[0.1em] text-app-muted-text">{item.label}</p>
            <p className={cn("mt-1 text-sm font-semibold text-app-text", item.wrap ? "break-words" : "truncate", item.valueClassName)} title={item.value}>{item.value}</p>
            <p className={cn("mt-0.5 text-xs text-app-muted-text", item.wrap ? "break-words" : "truncate")} title={item.detail}>{item.detail}</p>
          </div>
        ))}
      </div>
      )}
    </Card>
  );
});

type PerformanceMode = "cumulative" | "daily";

function PerformanceToggle({
  mode,
  onChange,
}: {
  mode: PerformanceMode;
  onChange: (mode: PerformanceMode) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Performance chart series"
      className="inline-flex rounded-xl border border-app-border bg-app-bg/35 p-0.5"
    >
      {(["cumulative", "daily"] as const).map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={mode === option}
          className={cn(
            "min-h-11 min-w-[88px] rounded-[10px] px-3 text-xs font-semibold capitalize transition",
            mode === option
              ? "bg-app-accent/15 text-app-text shadow-sm"
              : "text-app-muted-text hover:bg-app-surface-raised hover:text-app-text",
            compactFocusRing,
          )}
          onClick={() => onChange(option)}
        >
          {option === "cumulative" ? "Cumulative" : "Daily"}
        </button>
      ))}
    </div>
  );
}

export const CompactPerformanceChart = memo(function CompactPerformanceChart({
  points,
  loading,
  error,
}: {
  points: readonly CompactChartPoint[];
  loading: boolean;
  error: string | null;
}) {
  const [mode, setMode] = useState<PerformanceMode>("cumulative");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const pointRefs = useRef<Array<SVGCircleElement | null>>([]);
  const rawId = useId();
  const chartId = rawId.replace(/:/g, "");
  const titleId = `${chartId}-title`;
  const descriptionId = `${chartId}-description`;
  const values = points.map((point) => mode === "cumulative" ? point.cumulativePnl : point.dailyPnl);
  const plotWidth = CHART_WIDTH - CHART_PADDING.left - CHART_PADDING.right;
  const plotHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom;
  const dataMin = values.length > 0 ? Math.min(0, ...values) : -1;
  const dataMax = values.length > 0 ? Math.max(0, ...values) : 1;
  const rawSpan = dataMax - dataMin;
  const padding = rawSpan > 0 ? rawSpan * 0.08 : 1;
  const yMin = dataMin - padding;
  const yMax = dataMax + padding;
  const ySpan = yMax - yMin || 1;
  const toX = (index: number) => CHART_PADDING.left + (
    points.length <= 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth
  );
  const toY = (value: number) => CHART_PADDING.top + ((yMax - value) / ySpan) * plotHeight;
  const baselineY = toY(0);
  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${toX(index).toFixed(2)} ${toY(point.cumulativePnl).toFixed(2)}`)
    .join(" ");
  const areaPath = points.length > 0
    ? `M ${toX(0).toFixed(2)} ${baselineY.toFixed(2)} ${linePath.replace(/^M/, "L")} L ${toX(points.length - 1).toFixed(2)} ${baselineY.toFixed(2)} Z`
    : "";
  const step = points.length > 0 ? plotWidth / points.length : plotWidth;
  const barWidth = Math.max(3, Math.min(15, step * 0.58));
  const ticks = Array.from({ length: 5 }, (_, index) => yMin + ((4 - index) / 4) * ySpan);
  const labelIndexes = points.length > 0
    ? [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])]
    : [];
  const summary = useMemo(() => {
    if (points.length === 0) {
      return [];
    }
    if (mode === "cumulative") {
      const cumulativeValues = points.map((point) => point.cumulativePnl);
      return [
        { label: "Ending", value: points.at(-1)?.cumulativePnl ?? 0 },
        { label: "High", value: Math.max(...cumulativeValues) },
        { label: "Low", value: Math.min(...cumulativeValues) },
      ];
    }
    const dailyValues = points.map((point) => point.dailyPnl);
    return [
      { label: "Net", value: dailyValues.reduce((total, value) => total + value, 0) },
      { label: "Best day", value: Math.max(...dailyValues) },
      { label: "Worst day", value: Math.min(...dailyValues) },
    ];
  }, [mode, points]);
  const activeLabel = mode === "cumulative" ? "Cumulative P&L" : "Daily P&L";

  const selectedDateIndex = selectedDate
    ? points.findIndex((point) => point.date === selectedDate)
    : -1;
  const safeSelectedIndex = selectedDateIndex >= 0
    ? selectedDateIndex
    : Math.max(0, points.length - 1);
  const selectedPoint = points[safeSelectedIndex] ?? points.at(-1) ?? {
    date: "",
    dailyPnl: 0,
    cumulativePnl: 0,
  };
  const selectedValue = mode === "cumulative" ? selectedPoint.cumulativePnl : selectedPoint.dailyPnl;

  const handlePointKeyDown = (event: ReactKeyboardEvent<SVGCircleElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = Math.max(0, index - 1);
    } else if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = Math.min(points.length - 1, index + 1);
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = points.length - 1;
    }
    if (nextIndex === null || nextIndex === index) {
      return;
    }
    event.preventDefault();
    setSelectedDate(nextIndex === points.length - 1 ? null : points[nextIndex]?.date ?? null);
    pointRefs.current[nextIndex]?.focus();
  };

  return (
    <CompactPanel
      title="Performance"
      info="Switch between the cumulative equity path and individual daily results. Both cover every trading day in the selected scope."
      headerActions={<PerformanceToggle mode={mode} onChange={setMode} />}
      className="h-full"
    >
      {loading ? (
        <CompactState kind="loading" title="Loading performance" detail="Retrieving daily net P&L for this range." minHeightClassName="min-h-[292px]" />
      ) : error ? (
        <CompactState kind="error" title="Performance unavailable" detail={error} minHeightClassName="min-h-[292px]" announce={false} />
      ) : points.length === 0 ? (
        <CompactState kind="empty" title="No performance data" detail="No closed-trade days fall inside this range." minHeightClassName="min-h-[292px]" />
      ) : (
        <div className="px-2 pb-3 pt-2 sm:px-4">
          <div className="mx-auto w-full max-w-3xl">
          <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            className="h-auto min-h-[210px] w-full"
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-labelledby={`${titleId} ${descriptionId}`}
          >
            <title id={titleId}>{activeLabel} chart</title>
            <desc id={descriptionId}>
              {`${points.length} trading days from ${formatShortDate(points[0].date)} to ${formatShortDate(points.at(-1)?.date ?? points[0].date)}. ${summary.map((item) => `${item.label} ${formatPnl(item.value)}`).join(", ")}.`}
            </desc>
            <defs>
              <linearGradient id={`${chartId}-area`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="rgb(var(--theme-accent-secondary) / .4)" />
                <stop offset="100%" stopColor="rgb(var(--theme-accent-secondary) / .03)" />
              </linearGradient>
            </defs>
            {ticks.map((tick, index) => {
              const y = toY(tick);
              return (
                <g key={`${tick}-${index}`}>
                  <line
                    x1={CHART_PADDING.left}
                    x2={CHART_WIDTH - CHART_PADDING.right}
                    y1={y}
                    y2={y}
                    stroke="var(--dashboard-chart-grid)"
                    strokeDasharray="4 5"
                  />
                  <text
                    x={CHART_PADDING.left - 7}
                    y={y + 4}
                    textAnchor="end"
                    fontSize="10"
                    fill="rgb(var(--theme-chart-text) / 1)"
                  >
                    {formatCompactCurrency(tick)}
                  </text>
                </g>
              );
            })}
            <line
              x1={CHART_PADDING.left}
              x2={CHART_WIDTH - CHART_PADDING.right}
              y1={baselineY}
              y2={baselineY}
              stroke="var(--dashboard-chart-grid-strong)"
              strokeWidth="1.2"
            />
            {mode === "cumulative" ? (
              <>
                <path d={areaPath} fill={`url(#${chartId}-area)`} />
                <path
                  d={linePath}
                  fill="none"
                  stroke="rgb(var(--theme-accent-secondary) / 1)"
                  strokeWidth="2.25"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  vectorEffect="non-scaling-stroke"
                />
                {points.length === 1 ? (
                  <circle
                    cx={toX(0)}
                    cy={toY(points[0].cumulativePnl)}
                    r="4"
                    fill="rgb(var(--theme-surface) / 1)"
                    stroke="rgb(var(--theme-accent-secondary) / 1)"
                    strokeWidth="2"
                  />
                ) : null}
              </>
            ) : points.map((point, index) => {
              const valueY = toY(point.dailyPnl);
              const x = CHART_PADDING.left + index * step + (step - barWidth) / 2;
              return (
                <rect
                  key={point.date}
                  x={x}
                  y={point.dailyPnl === 0 ? baselineY - 1 : Math.min(valueY, baselineY)}
                  width={barWidth}
                  height={point.dailyPnl === 0 ? 2 : Math.max(1, Math.abs(baselineY - valueY))}
                  rx="1.5"
                  fill={point.dailyPnl > 0
                    ? "rgb(var(--theme-positive) / .82)"
                    : point.dailyPnl < 0
                      ? "rgb(var(--theme-negative) / .88)"
                      : "rgb(var(--theme-muted) / .72)"}
                />
              );
            })}
            {labelIndexes.map((index) => (
              <text
                key={points[index].date}
                x={mode === "cumulative" ? toX(index) : CHART_PADDING.left + index * step + step / 2}
                y={CHART_HEIGHT - 9}
                textAnchor={index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}
                fontSize="10"
                fill="rgb(var(--theme-chart-text) / 1)"
              >
                {formatShortDate(points[index].date)}
              </text>
            ))}
            {points.map((point, index) => {
              const value = mode === "cumulative" ? point.cumulativePnl : point.dailyPnl;
              const x = mode === "cumulative" ? toX(index) : CHART_PADDING.left + index * step + step / 2;
              const selected = safeSelectedIndex === index;
              return (
                <circle
                  key={`focus-${point.date}`}
                  ref={(element) => { pointRefs.current[index] = element; }}
                  cx={x}
                  cy={toY(value)}
                  r={selected ? 5 : 8}
                  fill={selected ? "rgb(var(--theme-surface) / 1)" : "transparent"}
                  stroke={selected ? "rgb(var(--theme-accent-secondary) / 1)" : "transparent"}
                  strokeWidth="2"
                  role="img"
                  tabIndex={selected ? 0 : -1}
                  aria-label={`${formatShortDate(point.date)}, ${formatPnl(value)}`}
                  onMouseEnter={() => setSelectedDate(index === points.length - 1 ? null : point.date)}
                  onFocus={() => setSelectedDate(index === points.length - 1 ? null : point.date)}
                  onKeyDown={(event) => handlePointKeyDown(event, index)}
                  className="cursor-crosshair focus:outline-none"
                />
              );
            })}
          </svg>
          <p className="mb-2 text-center text-xs text-app-muted-text" aria-live="polite">
            Selected: <span className="font-semibold text-app-text">{formatShortDate(selectedPoint.date)} · {formatPnl(selectedValue)}</span>
          </p>
          <div className="grid grid-cols-3 gap-2 border-t border-app-border/60 pt-3" aria-label={`${activeLabel} summary`}>
            {summary.map((item) => (
              <div key={item.label} className="min-w-0 text-center">
                <p className="text-xs text-app-muted-text">{item.label}</p>
                <p className="mt-0.5 truncate text-xs font-semibold text-app-text">{formatPnl(item.value)}</p>
              </div>
            ))}
          </div>
          <table className="sr-only">
            <caption>{activeLabel} data</caption>
            <thead><tr><th scope="col">Date</th><th scope="col">Value</th></tr></thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.date}>
                  <th scope="row">{formatShortDate(point.date)}</th>
                  <td>{formatPnl(mode === "cumulative" ? point.cumulativePnl : point.dailyPnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </CompactPanel>
  );
});

export const CompactScoreCard = memo(function CompactScoreCard({
  score,
  breakdown,
  loading,
  error,
}: {
  score: number;
  breakdown: CompactScoreBreakdown | null;
  loading: boolean;
  error: string | null;
}) {
  const rawId = useId();
  const chartId = rawId.replace(/:/g, "");
  const center = { x: 160, y: 132 };
  const anchors = [
    { label: "Risk", x: 160, y: 30 },
    { label: "Consistency", x: 276, y: 215 },
    { label: "Edge", x: 44, y: 215 },
  ];
  const values = breakdown
    ? [clamp(breakdown.riskScore), clamp(breakdown.consistencyScore), clamp(breakdown.edgeScore)]
    : [];
  const polygonAt = (scale: number) => anchors
    .map((anchor) => `${center.x + (anchor.x - center.x) * scale},${center.y + (anchor.y - center.y) * scale}`)
    .join(" ");
  const scorePolygon = anchors
    .map((anchor, index) => {
      const scale = (values[index] ?? 0) / 100;
      return `${center.x + (anchor.x - center.x) * scale},${center.y + (anchor.y - center.y) * scale}`;
    })
    .join(" ");
  const scoreTone = score >= 80
    ? "text-app-positive-text"
    : score >= 60
      ? "text-app-accent-text"
      : "text-app-negative-text";

  return (
    <CompactPanel
      title="TopSignal Score"
      info="A sustainability score built from actual risk, consistency, and edge components. Sample confidence increases with more trading days."
      className="h-full"
    >
      {loading ? (
        <CompactState kind="loading" title="Calculating score" detail="Evaluating risk, consistency, and edge." minHeightClassName="min-h-[292px]" />
      ) : error ? (
        <CompactState kind="error" title="Score unavailable" detail={error} minHeightClassName="min-h-[292px]" announce={false} />
      ) : !breakdown || breakdown.sampleSize === 0 ? (
        <CompactState kind="insufficient" title="Not enough data" detail="The TopSignal Score requires at least one closed-trade day." minHeightClassName="min-h-[292px]" />
      ) : (
        <div className="flex min-h-[292px] flex-col items-center justify-center px-3 pb-4 pt-2">
          <svg
            viewBox="0 0 320 244"
            className="h-auto max-h-[230px] w-full max-w-[330px]"
            role="img"
            aria-labelledby={`${chartId}-title ${chartId}-description`}
          >
            <title id={`${chartId}-title`}>TopSignal component scores</title>
            <desc id={`${chartId}-description`}>
              {`Overall ${Math.round(score)} out of 100, ${breakdown.label}. Risk ${Math.round(values[0])}, consistency ${Math.round(values[1])}, edge ${Math.round(values[2])}.`}
            </desc>
            {[0.33, 0.67, 1].map((level) => (
              <polygon
                key={level}
                points={polygonAt(level)}
                fill={level === 1 ? "rgb(var(--theme-surface-raised) / .28)" : "none"}
                stroke="rgb(var(--theme-border) / .62)"
                strokeWidth="1"
              />
            ))}
            {anchors.map((anchor) => (
              <line
                key={anchor.label}
                x1={center.x}
                y1={center.y}
                x2={anchor.x}
                y2={anchor.y}
                stroke="rgb(var(--theme-border) / .55)"
              />
            ))}
            <polygon
              points={scorePolygon}
              fill="rgb(var(--theme-accent-secondary) / .2)"
              stroke="rgb(var(--theme-accent-secondary) / 1)"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {scorePolygon.split(" ").map((point, index) => {
              const [cx, cy] = point.split(",");
              return (
                <circle
                  key={anchors[index].label}
                  cx={cx}
                  cy={cy}
                  r="3.5"
                  fill="rgb(var(--theme-surface) / 1)"
                  stroke="rgb(var(--theme-accent-secondary) / 1)"
                  strokeWidth="2"
                />
              );
            })}
            <text x="160" y="16" textAnchor="middle" fontSize="11" fill="rgb(var(--theme-muted) / 1)">Risk · {Math.round(values[0])}</text>
            <text x="300" y="232" textAnchor="end" fontSize="11" fill="rgb(var(--theme-muted) / 1)">Consistency · {Math.round(values[1])}</text>
            <text x="20" y="232" textAnchor="start" fontSize="11" fill="rgb(var(--theme-muted) / 1)">Edge · {Math.round(values[2])}</text>
          </svg>
          <div className="-mt-1 flex flex-wrap items-baseline justify-center gap-x-2 gap-y-1 text-center">
            <span className="text-xs text-app-muted-text">Overall</span>
            <strong className={cn("text-2xl", scoreTone)}>{Math.round(score)}</strong>
            <span className="rounded-full bg-app-surface-raised px-2 py-1 text-xs font-semibold text-app-text">{breakdown.label}</span>
          </div>
          <p className="mt-1 text-xs text-app-muted-text">Based on {breakdown.sampleSize} trading day{breakdown.sampleSize === 1 ? "" : "s"}</p>
          <dl className="sr-only">
            {anchors.map((anchor, index) => (
              <div key={anchor.label}><dt>{anchor.label}</dt><dd>{Math.round(values[index])} out of 100</dd></div>
            ))}
          </dl>
        </div>
      )}
    </CompactPanel>
  );
});
