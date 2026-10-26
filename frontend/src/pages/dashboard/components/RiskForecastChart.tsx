import { useId } from "react";
import type { RiskFanPoint } from "./riskForecastTypes";
import { riskMoney } from "./riskForecastFormat";

export function RiskForecastChart({ points }: { points: RiskFanPoint[] }) {
  const titleId = useId();
  if (points.length < 2) return null;
  const values = points.flatMap(point => [point.p10, point.p50, point.p90]);
  const low = Math.min(...values), high = Math.max(...values);
  const padding = Math.max((high-low)*.1, 1);
  const bottom = low-padding, top = high+padding;
  const x = (day: number) => 90+day/points[points.length-1].day*650;
  const y = (value: number) => 210-(value-bottom)/(top-bottom)*185;
  const line = (key: "p10" | "p50" | "p90", rows = points) => rows.map(point => `${x(point.day).toFixed(2)},${y(point[key]).toFixed(2)}`).join(" ");
  return <figure className="rounded-xl border border-app-border bg-app-bg/40 p-3">
    <svg viewBox="0 0 770 250" className="w-full" role="img" aria-labelledby={titleId}>
      <title id={titleId}>Simulated account balance: median and 10th to 90th percentile over {points[points.length-1].day} active trading days</title>
      {[bottom, (top+bottom)/2, top].map(value => <g key={value}>
        <line x1="90" x2="740" y1={y(value)} y2={y(value)} stroke="currentColor" className="text-app-border"/>
        <text x="80" y={y(value)+4} textAnchor="end" fill="currentColor" className="text-app-muted" fontSize="11">{riskMoney(value)}</text>
      </g>)}
      <polygon points={`${line("p90")} ${line("p10", [...points].reverse())}`} fill="currentColor" opacity=".16" className="text-app-accent"/>
      <polyline points={line("p50")} fill="none" stroke="currentColor" strokeWidth="2.5" className="text-app-accent"/>
      {[0, Math.round(points[points.length-1].day/2), points[points.length-1].day].filter((day, i, all) => all.indexOf(day) === i).map(day =>
        <text key={day} x={x(day)} y="235" textAnchor="middle" fill="currentColor" className="text-app-muted" fontSize="12">Day {day}</text>)}
    </svg>
    <figcaption className="text-xs text-app-muted">Line: median balance · Shading: middle 80% of simulated paths. Paths stop at an account-floor breach. These are scenario percentiles, not a confidence interval for future performance.</figcaption>
  </figure>;
}
