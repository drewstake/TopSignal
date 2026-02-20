import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/Card";
import { heatmapLabels, timeOfDayHeatmap } from "../../../mock/data";

const cellSize = 42;
const gap = 8;
const leftPad = 54;
const topPad = 24;

function intensityToColor(value: number) {
  const alpha = Math.min(0.92, Math.max(0.12, value));
  return `rgba(34, 211, 238, ${alpha})`;
}

export function TimeOfDayHeatmapCard() {
  const width = leftPad + heatmapLabels.length * (cellSize + gap);
  const height = topPad + timeOfDayHeatmap.length * (cellSize + gap);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Time-of-Day Heatmap</CardTitle>
        <CardDescription>Mock session edge intensity by weekday and hour.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/60 p-3">
          <svg viewBox={`0 0 ${width} ${height}`} className="h-[26rem] min-w-[420px]" role="img" aria-label="Time-of-day heatmap placeholder">
            {heatmapLabels.map((label, xIndex) => (
              <text
                key={label}
                x={leftPad + xIndex * (cellSize + gap) + cellSize / 2}
                y={16}
                textAnchor="middle"
                fill="#94a3b8"
                fontSize="11"
              >
                {label}
              </text>
            ))}

            {timeOfDayHeatmap.map((hourRow, yIndex) => (
              <g key={hourRow.hour}>
                <text x={8} y={topPad + yIndex * (cellSize + gap) + 26} fill="#94a3b8" fontSize="11">
                  {hourRow.hour}:00
                </text>
                {hourRow.values.map((value, xIndex) => (
                  <rect
                    key={`${hourRow.hour}-${xIndex}`}
                    x={leftPad + xIndex * (cellSize + gap)}
                    y={topPad + yIndex * (cellSize + gap)}
                    width={cellSize}
                    height={cellSize}
                    rx="10"
                    fill={intensityToColor(value)}
                    stroke="rgba(100, 116, 139, 0.4)"
                  />
                ))}
              </g>
            ))}
          </svg>
        </div>
      </CardContent>
    </Card>
  );
}
