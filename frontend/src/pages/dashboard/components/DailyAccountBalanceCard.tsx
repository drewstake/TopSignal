import { type MouseEvent as ReactMouseEvent, useEffect, useId, useMemo, useRef, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { Skeleton } from "../../../components/ui/Skeleton";
import type { AccountPnlCalendarDay } from "../../../lib/types";
import { formatCurrency } from "../../../utils/formatters";
import { buildInterpolatedAreaPath, buildInterpolatedLinePath } from "./dailyBalanceChartPaths";

interface DailyAccountBalanceCardProps {
  days: AccountPnlCalendarDay[];
  loading: boolean;
  error: string | null;
  currentBalance: number | null;
}

interface BalanceSeriesPoint {
  date: string;
  netPnl: number;
  balance: number;
}

interface ChartPoint extends BalanceSeriesPoint {
  x: number;
  y: number;
}

interface ChartData {
  points: ChartPoint[];
  linePath: string;
  areaPath: string;
  yTicks: Array<{ value: number; y: number }>;
  xTicks: ChartPoint[];
  xGuides: number[];
  markerIndexes: Set<number>;
  startBalanceY: number;
}

const width = 980;
const height = 280;
const yLabelX = 8;
const chartPadding = { top: 20, right: 24, bottom: 36, left: 90 };
const plotWidth = width - chartPadding.left - chartPadding.right;
const plotHeight = height - chartPadding.top - chartPadding.bottom;

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const hoverDateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "numeric",
  day: "2-digit",
  timeZone: "UTC",
});

function parseIsoDate(value: string) {
  return new Date(`${value}T00:00:00.000Z`);
}

function sortByDateAscending(days: AccountPnlCalendarDay[]) {
  return [...days].sort((left, right) => left.date.localeCompare(right.date));
}

function buildBalanceSeries(days: AccountPnlCalendarDay[], currentBalance: number | null): BalanceSeriesPoint[] {
  const orderedDays = sortByDateAscending(days);
  if (orderedDays.length === 0) {
    return [];
  }

  const totalNetPnl = orderedDays.reduce((sum, day) => sum + day.net_pnl, 0);
  const hasCurrentBalance = currentBalance !== null && Number.isFinite(currentBalance);
  const endingBalance = hasCurrentBalance ? currentBalance : totalNetPnl;
  const startingBalance = endingBalance - totalNetPnl;

  let runningNetPnl = 0;
  return orderedDays.map((day) => {
    runningNetPnl += day.net_pnl;
    return {
      date: day.date,
      netPnl: day.net_pnl,
      balance: startingBalance + runningNetPnl,
    };
  });
}

function formatPnl(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatCurrency(value)}`;
}

function createMarkerIndexes(length: number) {
  const indexes = new Set<number>();
  if (length === 0) {
    return indexes;
  }

  indexes.add(0);
  indexes.add(length - 1);
  const step = Math.max(1, Math.floor(length / 9));
  for (let index = step; index < length - 1; index += step) {
    indexes.add(index);
  }
  return indexes;
}

export function DailyAccountBalanceCard({ days, loading, error, currentBalance }: DailyAccountBalanceCardProps) {
  const chartSeed = useId().replace(/:/g, "");
  const [hoveredPointIndex, setHoveredPointIndex] = useState<number | null>(null);
  const hoverAnimationFrameRef = useRef<number | null>(null);
  const pendingHoveredPointIndexRef = useRef<number | null>(null);
  const series = useMemo(() => buildBalanceSeries(days, currentBalance), [currentBalance, days]);
  const firstPoint = series[0] ?? null;
  const lastPoint = series[series.length - 1] ?? null;
  const netPnlTotal = useMemo(() => series.reduce((sum, point) => sum + point.netPnl, 0), [series]);
  const hasAnchoredBalance = currentBalance !== null && Number.isFinite(currentBalance);
  const isPositiveTrend = (firstPoint && lastPoint ? lastPoint.balance - firstPoint.balance : 0) >= 0;
  const summaryTint = isPositiveTrend
    ? "linear-gradient(180deg, rgba(16, 185, 129, 0.16) 0%, rgba(15, 23, 42, 0) 100%)"
    : "linear-gradient(180deg, rgba(244, 63, 94, 0.16) 0%, rgba(15, 23, 42, 0) 100%)";
  const tone = isPositiveTrend
    ? {
        lineStart: "#10b981",
        lineEnd: "#34d399",
        point: "#6ee7b7",
        glow: "#34d399",
        area: "#34d399",
      }
    : {
        lineStart: "#f43f5e",
        lineEnd: "#fb7185",
        point: "#fda4af",
        glow: "#fb7185",
        area: "#fb7185",
      };

  const stats = useMemo(() => {
    if (series.length === 0) {
      return null;
    }

    let highBalance = series[0].balance;
    let lowBalance = series[0].balance;
    let largestDailyMove = series[0].netPnl;

    for (const point of series) {
      highBalance = Math.max(highBalance, point.balance);
      lowBalance = Math.min(lowBalance, point.balance);
      if (Math.abs(point.netPnl) > Math.abs(largestDailyMove)) {
        largestDailyMove = point.netPnl;
      }
    }

    return {
      startingBalance: series[0].balance,
      highBalance,
      lowBalance,
      largestDailyMove,
    };
  }, [series]);

  const chartData = useMemo<ChartData | null>(() => {
    if (series.length === 0) {
      return null;
    }

    const balances = series.map((point) => point.balance);
    const minBalance = Math.min(...balances);
    const maxBalance = Math.max(...balances);
    const spread = maxBalance - minBalance;
    const padding = Math.max(spread * 0.15, Math.abs(maxBalance || 1) * 0.01, 100);

    const yMin = minBalance - padding;
    const yMax = maxBalance + padding;
    const yRange = Math.max(yMax - yMin, 1);
    const baselineY = height - chartPadding.bottom;

    const points: ChartPoint[] = series.map((point, index) => {
      const x =
        series.length === 1
          ? chartPadding.left + plotWidth / 2
          : chartPadding.left + (index / (series.length - 1)) * plotWidth;
      const y = chartPadding.top + ((yMax - point.balance) / yRange) * plotHeight;
      return { ...point, x, y };
    });

    const linePath = buildInterpolatedLinePath(points);
    const areaPath = buildInterpolatedAreaPath(points, baselineY);

    const yTickCount = 5;
    const yTicks = Array.from({ length: yTickCount }, (_, index) => {
      const ratio = index / (yTickCount - 1);
      const value = yMax - ratio * yRange;
      const y = chartPadding.top + ratio * plotHeight;
      return { value, y };
    });

    const xTickCount = Math.min(5, points.length);
    const xTicks = Array.from({ length: xTickCount }, (_, index) => {
      if (xTickCount === 1) {
        return points[0];
      }
      const pointIndex = Math.round((index / (xTickCount - 1)) * (points.length - 1));
      return points[pointIndex];
    }).filter((point, index, allTicks) => index === 0 || point.date !== allTicks[index - 1].date);
    const xGuides = xTicks.map((tick) => tick.x);
    const markerIndexes = createMarkerIndexes(points.length);

    return {
      points,
      linePath,
      areaPath,
      yTicks,
      xTicks,
      xGuides,
      markerIndexes,
      startBalanceY: points[0].y,
    };
  }, [series]);

  const fillGradientId = `daily-balance-fill-${chartSeed}`;
  const lineGradientId = `daily-balance-line-${chartSeed}`;
  const glowFilterId = `daily-balance-glow-${chartSeed}`;
  const lastChartPoint = chartData?.points[chartData.points.length - 1] ?? null;
  const hoveredPoint =
    chartData && hoveredPointIndex !== null && hoveredPointIndex >= 0 && hoveredPointIndex < chartData.points.length
      ? chartData.points[hoveredPointIndex]
      : null;
  const hoveredDateLabel = hoveredPoint ? hoverDateFormatter.format(parseIsoDate(hoveredPoint.date)) : null;
  const hoveredBalanceLabel = hoveredPoint ? formatCurrency(hoveredPoint.balance) : null;
  const hoveredTooltipWidth = Math.max(
    126,
    ((hoveredBalanceLabel ? hoveredBalanceLabel.length : 0) + (hoveredDateLabel ? hoveredDateLabel.length : 0)) * 3.8 + 72,
  );
  const hoveredTooltipHeight = 50;
  const endLabelY = lastChartPoint ? Math.max(chartPadding.top + 12, lastChartPoint.y - 10) : null;
  const startLabelY = chartData ? Math.max(chartPadding.top + 11, chartData.startBalanceY - 6) : null;
  const hoveredTooltip =
    hoveredPoint && hoveredDateLabel && hoveredBalanceLabel
      ? (() => {
          const plotBottom = height - chartPadding.bottom;
          const wouldOverflowRight = hoveredPoint.x + 14 + hoveredTooltipWidth > width - chartPadding.right;
          const x = wouldOverflowRight
            ? Math.max(chartPadding.left + 4, hoveredPoint.x - hoveredTooltipWidth - 14)
            : hoveredPoint.x + 14;
          const renderAbove = hoveredPoint.y - hoveredTooltipHeight - 12 > chartPadding.top;
          const y = renderAbove
            ? hoveredPoint.y - hoveredTooltipHeight - 12
            : Math.min(plotBottom - hoveredTooltipHeight - 6, hoveredPoint.y + 12);

          return {
            x,
            y,
            dateLabel: hoveredDateLabel,
            balanceLabel: hoveredBalanceLabel,
          };
        })()
      : null;

  const handleChartMouseMove = (event: ReactMouseEvent<SVGSVGElement>) => {
    if (!chartData || chartData.points.length === 0) {
      return;
    }

    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0) {
      return;
    }

    const normalizedX = ((event.clientX - bounds.left) / bounds.width) * width;
    const clampedRatio = Math.max(0, Math.min(1, (normalizedX - chartPadding.left) / plotWidth));
    const nearestIndex =
      chartData.points.length === 1 ? 0 : Math.round(clampedRatio * (chartData.points.length - 1));

    pendingHoveredPointIndexRef.current = nearestIndex;
    if (hoverAnimationFrameRef.current !== null) {
      return;
    }

    hoverAnimationFrameRef.current = window.requestAnimationFrame(() => {
      hoverAnimationFrameRef.current = null;
      const pendingIndex = pendingHoveredPointIndexRef.current;
      setHoveredPointIndex((current) => (current === pendingIndex ? current : pendingIndex));
    });
  };

  const handleChartMouseLeave = () => {
    pendingHoveredPointIndexRef.current = null;
    if (hoverAnimationFrameRef.current !== null) {
      window.cancelAnimationFrame(hoverAnimationFrameRef.current);
      hoverAnimationFrameRef.current = null;
    }
    setHoveredPointIndex(null);
  };

  useEffect(() => {
    return () => {
      if (hoverAnimationFrameRef.current !== null) {
        window.cancelAnimationFrame(hoverAnimationFrameRef.current);
      }
    };
  }, []);

  return (
    <Card className="relative overflow-hidden">
      {series.length > 0 ? <div className="pointer-events-none absolute inset-x-0 top-0 h-20" style={{ background: summaryTint }} /> : null}
      <CardHeader className="relative space-y-3">
        <div>
          <CardTitle className="tracking-tight">Daily Account Balance</CardTitle>
          <CardDescription>Daily balance curve built from calendar net PnL.</CardDescription>
        </div>
        {stats ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-lg border border-slate-800/75 bg-slate-950/45 px-3 py-2">
              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Start</p>
              <p className="text-sm font-semibold text-slate-100">{formatCurrency(stats.startingBalance)}</p>
            </div>
            <div className="rounded-lg border border-slate-800/75 bg-slate-950/45 px-3 py-2">
              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">High</p>
              <p className="text-sm font-semibold text-emerald-200">{formatCurrency(stats.highBalance)}</p>
            </div>
            <div className="rounded-lg border border-slate-800/75 bg-slate-950/45 px-3 py-2">
              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Low</p>
              <p className="text-sm font-semibold text-rose-200">{formatCurrency(stats.lowBalance)}</p>
            </div>
            <div className="rounded-lg border border-slate-800/75 bg-slate-950/45 px-3 py-2">
              <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Largest Day</p>
              <p className={`text-sm font-semibold ${stats.largestDailyMove >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                {formatPnl(stats.largestDailyMove)}
              </p>
            </div>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <Skeleton className="h-64 w-full" />
        ) : error ? (
          <p className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p>
        ) : series.length === 0 || !chartData ? (
          <p className="rounded-xl border border-slate-800/80 bg-slate-900/40 px-3 py-4 text-sm text-slate-400">
            No daily balance data yet. Sync trades to populate this chart.
          </p>
        ) : (
          <>
            <div className="overflow-x-auto rounded-2xl border border-slate-700/70 bg-gradient-to-b from-slate-950/95 via-slate-900/80 to-slate-950/95 p-4 shadow-[inset_0_1px_0_rgba(148,163,184,0.08)]">
              <svg
                viewBox={`0 0 ${width} ${height}`}
                preserveAspectRatio="none"
                className="h-72 w-full min-w-[860px]"
                role="img"
                aria-label="Daily account balance chart"
                onMouseMove={handleChartMouseMove}
                onMouseLeave={handleChartMouseLeave}
              >
                <defs>
                  <linearGradient id={fillGradientId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={tone.area} stopOpacity="0.33" />
                    <stop offset="100%" stopColor={tone.area} stopOpacity="0.02" />
                  </linearGradient>
                  <linearGradient id={lineGradientId} x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor={tone.lineStart} />
                    <stop offset="100%" stopColor={tone.lineEnd} />
                  </linearGradient>
                  <filter id={glowFilterId} x="-25%" y="-25%" width="150%" height="150%">
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feMerge>
                      <feMergeNode in="blur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>

                {chartData.xGuides.map((x) => (
                  <line
                    key={`x-guide-${x}`}
                    x1={x}
                    y1={chartPadding.top}
                    x2={x}
                    y2={height - chartPadding.bottom}
                    stroke="#334155"
                    strokeWidth="1"
                    opacity="0.28"
                  />
                ))}

                {chartData.yTicks.map((tick) => (
                  <g key={`y-tick-${tick.y}`}>
                    <line
                      x1={chartPadding.left}
                      y1={tick.y}
                      x2={width - chartPadding.right}
                      y2={tick.y}
                      stroke="#334155"
                      strokeWidth="1"
                      strokeDasharray="4 5"
                      opacity="0.85"
                    />
                    <text x={yLabelX} y={tick.y + 4} textAnchor="start" fontSize="11" fill="#94a3b8">
                      {formatCurrency(tick.value)}
                    </text>
                  </g>
                ))}

                <line
                  x1={chartPadding.left}
                  y1={chartData.startBalanceY}
                  x2={width - chartPadding.right}
                  y2={chartData.startBalanceY}
                  stroke={tone.lineStart}
                  strokeWidth="1"
                  strokeDasharray="5 5"
                  opacity="0.5"
                />

                {startLabelY !== null ? (
                  <text x={chartPadding.left + 6} y={startLabelY} fontSize="10" fill="#94a3b8">
                    Start
                  </text>
                ) : null}

                <path d={chartData.areaPath} fill={`url(#${fillGradientId})`} />
                <path
                  d={chartData.linePath}
                  fill="none"
                  stroke={tone.glow}
                  strokeOpacity="0.32"
                  strokeWidth="7"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d={chartData.linePath}
                  fill="none"
                  stroke={`url(#${lineGradientId})`}
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />

                {chartData.points.map((point, index) => (
                  chartData.markerIndexes.has(index) ? (
                    <circle
                      key={`point-${point.date}`}
                      cx={point.x}
                      cy={point.y}
                      r={
                        hoveredPointIndex === index
                          ? index === chartData.points.length - 1
                            ? 6
                            : 4
                          : index === chartData.points.length - 1
                            ? 4.5
                            : 2.5
                      }
                      fill={index === chartData.points.length - 1 ? tone.point : tone.lineEnd}
                      stroke={index === chartData.points.length - 1 ? "#0f172a" : "none"}
                      strokeWidth={index === chartData.points.length - 1 ? 1.5 : 0}
                    />
                  ) : null
                ))}

                {hoveredPoint ? (
                  <g pointerEvents="none">
                    <line
                      x1={hoveredPoint.x}
                      y1={chartPadding.top}
                      x2={hoveredPoint.x}
                      y2={height - chartPadding.bottom}
                      stroke={tone.lineEnd}
                      strokeOpacity="0.5"
                      strokeWidth="1"
                      strokeDasharray="3 4"
                    />
                    <circle cx={hoveredPoint.x} cy={hoveredPoint.y} r={7.5} fill={tone.point} opacity="0.16" />
                    <circle cx={hoveredPoint.x} cy={hoveredPoint.y} r={4} fill={tone.point} stroke="#0f172a" strokeWidth="1.5" />
                  </g>
                ) : null}

                {hoveredTooltip ? (
                  <g pointerEvents="none">
                    <rect
                      x={hoveredTooltip.x}
                      y={hoveredTooltip.y}
                      width={hoveredTooltipWidth}
                      height={hoveredTooltipHeight}
                      rx={9}
                      fill="rgba(2, 6, 23, 0.92)"
                      stroke="rgba(148, 163, 184, 0.38)"
                    />
                    <text x={hoveredTooltip.x + 10} y={hoveredTooltip.y + 19} fontSize="10" fill="#94a3b8">
                      Date
                    </text>
                    <text x={hoveredTooltip.x + hoveredTooltipWidth - 10} y={hoveredTooltip.y + 19} textAnchor="end" fontSize="11" fill="#e2e8f0">
                      {hoveredTooltip.dateLabel}
                    </text>
                    <text x={hoveredTooltip.x + 10} y={hoveredTooltip.y + 37} fontSize="10" fill="#94a3b8">
                      Balance
                    </text>
                    <text
                      x={hoveredTooltip.x + hoveredTooltipWidth - 10}
                      y={hoveredTooltip.y + 37}
                      textAnchor="end"
                      fontSize="12"
                      fontWeight={600}
                      fill={tone.point}
                    >
                      {hoveredTooltip.balanceLabel}
                    </text>
                  </g>
                ) : null}

                {lastChartPoint && lastPoint && endLabelY !== null ? (
                  <g>
                    <circle
                      cx={lastChartPoint.x}
                      cy={lastChartPoint.y}
                      r={11}
                      fill={tone.point}
                      opacity="0.2"
                      filter={`url(#${glowFilterId})`}
                    />
                    <circle cx={lastChartPoint.x} cy={lastChartPoint.y} r={5} fill={tone.point} stroke="#0f172a" strokeWidth="2" />
                    <text x={lastChartPoint.x - 10} y={endLabelY} textAnchor="end" fontSize="11" fontWeight={600} fill={tone.point}>
                      {formatCurrency(lastPoint.balance)}
                    </text>
                  </g>
                ) : null}

                {chartData.xTicks.map((tick) => (
                  <text key={`x-tick-${tick.date}`} x={tick.x} y={height - 8} textAnchor="middle" fontSize="11" fill="#94a3b8">
                    {dateFormatter.format(parseIsoDate(tick.date))}
                  </text>
                ))}
              </svg>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
              <p>
                Range: {firstPoint ? dateFormatter.format(parseIsoDate(firstPoint.date)) : "-"} to{" "}
                {lastPoint ? dateFormatter.format(parseIsoDate(lastPoint.date)) : "-"} | Net:{" "}
                <span className={netPnlTotal >= 0 ? "text-emerald-300" : "text-rose-300"}>{formatPnl(netPnlTotal)}</span>
              </p>
              <p className={hasAnchoredBalance ? "text-cyan-200/80" : "text-slate-400"}>
                {hasAnchoredBalance ? "Anchored to current account balance." : "Anchored to cumulative net PnL only."}
              </p>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
