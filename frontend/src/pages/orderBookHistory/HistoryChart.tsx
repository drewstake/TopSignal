import { memo, useMemo, useRef, useState } from "react";
import { continuousSegments, nearestPoint, type HistoryPoint } from "./history";

const W = 1160, H = 590, LEFT = 92, RIGHT = 24, PW = W - LEFT - RIGHT;
const panels = [{ top: 35, bottom: 190 }, { top: 235, bottom: 390 }, { top: 435, bottom: 550 }];
const clock = (time: number) => new Date(time * 1000).toLocaleTimeString([], { hour12: false });

export const HistoryChart = memo(function HistoryChart({ points, range, selected, onSelect, onRange }: {
  points: HistoryPoint[]; range: [number, number]; selected: HistoryPoint | null;
  onSelect: (point: HistoryPoint) => void; onRange: (range: [number, number]) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const x = (t: number) => LEFT + (t - range[0]) / (range[1] - range[0]) * PW;
  const geometry = useMemo(() => {
    const visible = points.filter(p => p.time >= range[0] && p.time <= range[1]);
    const segments = continuousSegments(visible);
    const clean = segments.flat();
    const prices = clean.map(p => p.mid_price!);
    const min = prices.length ? Math.min(...prices) : 0, max = prices.length ? Math.max(...prices) : 1;
    const padding = Math.max(.5, (max - min) * .12);
    const sizes = clean.flatMap(p => [p.bid_contracts, p.ask_contracts]);
    const scales = [[min - padding, max + padding], [0, Math.max(1, ...sizes) * 1.12], [-1, 1]];
    const y = (value: number, panel: number) => panels[panel].bottom - (value - scales[panel][0]) /
      (scales[panel][1] - scales[panel][0]) * (panels[panel].bottom - panels[panel].top);
    const path = (field: "mid_price" | "bid_contracts" | "ask_contracts" | "imbalance", panel: number) => segments.map(segment =>
      segment.filter(p => p[field] !== null).map((p, i) => `${i ? "L" : "M"}${(LEFT + (p.time-range[0])/(range[1]-range[0])*PW).toFixed(2)},${y(p[field]!, panel).toFixed(2)}`).join(" ")
    ).join(" ");
    const gaps: Array<[number, number]> = [];
    let cursor = range[0];
    for (const segment of segments) {
      if (segment[0].time - cursor > 2) gaps.push([cursor, segment[0].time]);
      cursor = segment.at(-1)!.time;
    }
    if (range[1] - cursor > 2) gaps.push([cursor, range[1]]);
    return { paths: [path("mid_price", 0), path("bid_contracts", 1), path("ask_contracts", 1), path("imbalance", 2)], scales, y, gaps };
  }, [points, range]);
  const pointerTime = (clientX: number) => {
    const box = svg.current!.getBoundingClientRect();
    const pos = (clientX - box.left) / box.width * W;
    return range[0] + Math.max(0, Math.min(1, (pos - LEFT) / PW)) * (range[1] - range[0]);
  };
  return <div className="overflow-x-auto rounded-xl border border-app-border bg-app-bg/50">
    <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block min-w-[680px] w-full select-none touch-none"
      role="img" aria-label="Price, bid and ask contract totals, and imbalance on a shared timeline. Drag to zoom; click to inspect. Arrow keys select adjacent samples."
      tabIndex={0}
      onPointerDown={e => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); const t = pointerTime(e.clientX); setDrag([t,t]); }}
      onPointerMove={e => { if (drag) setDrag([drag[0], pointerTime(e.clientX)]); }}
      onPointerCancel={() => setDrag(null)}
      onPointerUp={e => {
        if (!drag) return;
        const finish = pointerTime(e.clientX);
        if (Math.abs(finish-drag[0]) > Math.max(1, (range[1]-range[0])*.008)) onRange([Math.min(drag[0],finish), Math.max(drag[0],finish)]);
        else { const point = nearestPoint(points, finish); if (point) onSelect(point); }
        setDrag(null);
      }}
      onKeyDown={e => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const index = selected ? points.indexOf(selected) : 0;
        const point = points[Math.max(0, Math.min(points.length - 1, index + (e.key === "ArrowRight" ? 1 : -1)))];
        if (point) onSelect(point);
      }}>
      <rect width={W} height={H} fill="transparent" />
      {geometry.gaps.map(([a,b], i) => <rect key={i} x={x(a)} y={20} width={Math.max(1,x(b)-x(a))} height={535} fill="#d99a32" opacity={.08} />)}
      {panels.map((panel, index) => <g key={index}>
        <text x={LEFT} y={panel.top - 13} fill="currentColor" fontSize="12" fontWeight="600">
          {index === 0 ? "MID-PRICE" : index === 1 ? "RESTING CONTRACTS   ·   BIDS / ASKS" : "IMBALANCE   ·   POSITIVE = MORE BIDS"}
        </text>
        {[0, .5, 1].map(fraction => {
          const value = geometry.scales[index][0] + fraction*(geometry.scales[index][1]-geometry.scales[index][0]);
          const y = geometry.y(value, index);
          return <g key={fraction}>
            <line x1={LEFT} x2={W-RIGHT} y1={y} y2={y} stroke="currentColor" opacity={.1} strokeDasharray={index === 2 && fraction === .5 ? "5 4" : undefined} />
            <text x={LEFT-12} y={y+4} textAnchor="end" fill="currentColor" opacity={.65} fontSize="11">
              {index === 2 ? `${Math.round(value*100)}%` : value.toLocaleString("en-US", { maximumFractionDigits: index === 0 ? 2 : 0 })}
            </text>
          </g>;
        })}
      </g>)}
      {geometry.paths.map((d,i) => <path key={i} d={d} fill="none" stroke={["#60a5fa", "#34d399", "#fb7185", "#a78bfa"][i]} strokeWidth={i===0 ? 1.8 : 1.5} vectorEffect="non-scaling-stroke" />)}
      {Array.from({ length: 6 }, (_, i) => { const t = range[0] + (range[1]-range[0])*i/5; return <text key={i} x={x(t)} y={578} textAnchor={i===0 ? "start" : i===5 ? "end" : "middle"} fill="currentColor" opacity={.65} fontSize="11">{clock(t)}</text>; })}
      {selected && selected.time >= range[0] && selected.time <= range[1] && <line x1={x(selected.time)} x2={x(selected.time)} y1={20} y2={550} stroke="currentColor" opacity={.6} strokeDasharray="4 4" />}
      {drag && <rect x={x(Math.min(...drag))} width={Math.max(1,Math.abs(x(drag[1])-x(drag[0])))} y={20} height={530} fill="#60a5fa" opacity={.2} />}
    </svg>
  </div>;
});
