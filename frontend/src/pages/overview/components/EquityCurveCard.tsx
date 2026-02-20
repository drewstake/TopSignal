import { Badge } from "../../../components/ui/Badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { equityCurve } from "../../../mock/data";

const width = 700;
const height = 240;

function buildPoints(values: number[]) {
  const min = Math.min(...values);
  const max = Math.max(...values);

  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const normalized = (value - min) / (max - min || 1);
      const y = height - normalized * (height - 24) - 12;
      return `${x},${y}`;
    })
    .join(" ");
}

export function EquityCurveCard() {
  const points = buildPoints(equityCurve);
  const start = equityCurve[0];
  const end = equityCurve[equityCurve.length - 1];
  const pct = ((end - start) / start) * 100;

  return (
    <Card className="h-full">
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Equity Curve</CardTitle>
            <CardDescription>Simple SVG placeholder with cumulative account growth.</CardDescription>
          </div>
          <Badge variant={pct >= 0 ? "positive" : "negative"}>
            {pct >= 0 ? "+" : ""}
            {pct.toFixed(2)}%
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950/70 p-3">
          <svg viewBox={`0 0 ${width} ${height}`} className="h-56 w-full" role="img" aria-label="Equity curve chart placeholder">
            <defs>
              <linearGradient id="curveGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.45" />
                <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.05" />
              </linearGradient>
            </defs>
            <rect x="0" y="0" width={width} height={height} fill="transparent" />
            <polyline fill="none" stroke="#334155" strokeDasharray="4 8" strokeWidth="1" points={`0,${height / 2} ${width},${height / 2}`} />
            <polyline fill="url(#curveGradient)" stroke="none" points={`0,${height} ${points} ${width},${height}`} />
            <polyline fill="none" stroke="#22d3ee" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" points={points} />
          </svg>
        </div>
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>Start: ${start.toLocaleString()}</span>
          <span>Current: ${end.toLocaleString()}</span>
        </div>
      </CardContent>
    </Card>
  );
}
