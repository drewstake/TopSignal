import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { dailyPnl } from "../../../mock/data";

const width = 720;
const height = 220;
const barWidth = 48;
const gap = 18;

export function PnlByDayCard() {
  const maxAbs = Math.max(...dailyPnl.map((item) => Math.abs(item.value)));
  const baseline = height / 2;

  return (
    <Card>
      <CardHeader>
        <CardTitle>PnL by Day</CardTitle>
        <CardDescription>Placeholder bars for recent daily outcomes.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/70 p-3">
          <svg viewBox={`0 0 ${width} ${height}`} className="h-56 min-w-[640px]" role="img" aria-label="PnL by day bar chart placeholder">
            <line x1="0" y1={baseline} x2={width} y2={baseline} stroke="#334155" strokeDasharray="4 6" />
            {dailyPnl.map((item, index) => {
              const scaled = (Math.abs(item.value) / (maxAbs || 1)) * (height / 2 - 20);
              const x = index * (barWidth + gap) + 12;
              const isGain = item.value >= 0;
              const y = isGain ? baseline - scaled : baseline;

              return (
                <g key={`${item.day}-${index}`}>
                  <rect
                    x={x}
                    y={y}
                    width={barWidth}
                    height={scaled}
                    rx="6"
                    fill={isGain ? "#34d399" : "#fb7185"}
                    opacity="0.75"
                  />
                  <text x={x + barWidth / 2} y={height - 8} fill="#94a3b8" fontSize="11" textAnchor="middle">
                    {item.day}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
        <p className="text-xs text-slate-400">Positive bars in green, losses in red.</p>
      </CardContent>
    </Card>
  );
}
