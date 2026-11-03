import { memo, useMemo, useRef, useState } from "react";
import { continuousSegments, metrics, nearestPoint, type HistoryPoint } from "./history";

const W = 1160, H = 590, LEFT = 92, RIGHT = 24, PW = W - LEFT - RIGHT;
const panels = [{ top: 35, bottom: 190 }, { top: 235, bottom: 390 }, { top: 435, bottom: 550 }];
const clock = (time: number) => new Date(time * 1000).toLocaleTimeString([], { hour12: false });

export const HistoryChart = memo(function HistoryChart({ points, range, selected, onSelect, onRange }: {
  points: HistoryPoint[]; range: [number, number]; selected: HistoryPoint | null;
  onSelect: (point: HistoryPoint) => void; onRange: (range: [number, number]) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<{ point: HistoryPoint; at: number } | null>(null);
  const x = (t: number) => LEFT + (t - range[0]) / (range[1] - range[0]) * PW;
  const geometry = useMemo(() => {
    const visible = points.filter(p => p.time >= range[0] && p.time <= range[1]);
    const series = metrics.map(field => continuousSegments(visible, field));
    const prices = series[0].flat().map(p => p.mid_price!);
    const min = prices.length ? prices.reduce((a,b)=>Math.min(a,b),Infinity) : 0, max = prices.length ? prices.reduce((a,b)=>Math.max(a,b),-Infinity) : 1;
    const padding = Math.max(.5, (max - min) * .12);
    const sizes = [...series[1].flat().map(p => p.bid_contracts), ...series[2].flat().map(p => p.ask_contracts)];
    const scales = [[min - padding, max + padding], [0, sizes.reduce((a,b)=>Math.max(a,b),1) * 1.12], [-1, 1]];
    const y = (value: number, panel: number) => panels[panel].bottom - (value - scales[panel][0]) /
      (scales[panel][1] - scales[panel][0]) * (panels[panel].bottom - panels[panel].top);
    const paths = metrics.map((field, index) => series[index].map(segment =>
      segment.map((p, i) => `${i ? "L" : "M"}${(LEFT + (p.time-range[0])/(range[1]-range[0])*PW).toFixed(2)},${y(p[field]!, index === 3 ? 2 : index === 0 ? 0 : 1).toFixed(2)}`).join(" ")
    ).join(" "));
    const dots = metrics.flatMap((field,index) => series[index].filter(s => s.length === 1).map(s => ({ point:s[0], field, index })));
    const gaps: Array<{ start:number; end:number; reason:string; panel:number }> = [];
    // Shade each metric's actual continuity boundaries; a price issue need not erase liquidity.
    for (const index of [0,1,2,3]) {
      let cursor = range[0];
      for (const segment of series[index]) {
        const first = segment[0];
        if (first.time > cursor) {
          const gap = first.gap_before?.[metrics[index]];
          gaps.push({start:cursor,end:first.time,panel:index === 3 ? 2 : index === 0 ? 0 : 1,
            reason: gap?.reasons.join(", ").replaceAll("_"," ") || "Unknown interval or source/generation change"});
        }
        cursor = segment.at(-1)!.time;
      }
      if (range[1]-cursor > 2) gaps.push({start:cursor,end:range[1],panel:index === 3 ? 2 : index === 0 ? 0 : 1,reason:"No fresh observations"});
    }
    return { paths, scales, y, gaps, dots };
  }, [points, range]);
  const pointerTime = (clientX: number) => {
    const box = svg.current!.getBoundingClientRect();
    const pos = (clientX - box.left) / box.width * W;
    return range[0] + Math.max(0, Math.min(1, (pos - LEFT) / PW)) * (range[1] - range[0]);
  };
  const hoverGap = hover ? geometry.gaps.find(g => hover.at >= g.start && hover.at <= g.end) : null;
  return <div className="relative overflow-x-auto rounded-xl border border-app-border bg-app-bg/50">
    <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="block min-w-[680px] w-full select-none touch-none"
      role="img" aria-label="Price, bid and ask contract totals, and imbalance on a shared timeline. Drag to zoom; click to inspect. Arrow keys select adjacent samples."
      tabIndex={0}
      onPointerDown={e => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); const t = pointerTime(e.clientX); setDrag([t,t]); }}
      onPointerMove={e => {
        const at = pointerTime(e.clientX);
        if (drag) setDrag([drag[0], at]);
        else { const point = nearestPoint(points, at); setHover(point ? { point, at } : null); }
      }}
      onPointerLeave={() => setHover(null)}
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
        const index = selected ? points.findIndex(p => p.capture_id === selected.capture_id && p.index === selected.index) : 0;
        const point = points[Math.max(0, Math.min(points.length - 1, index + (e.key === "ArrowRight" ? 1 : -1)))];
        if (point) onSelect(point);
      }}>
      <rect width={W} height={H} fill="transparent" />
      {geometry.gaps.map((gap, i) => <rect key={i} x={x(gap.start)} y={panels[gap.panel].top} width={Math.max(1,x(gap.end)-x(gap.start))} height={panels[gap.panel].bottom-panels[gap.panel].top} fill="#d99a32" opacity={.09}><title>{gap.reason} · {(gap.end-gap.start).toFixed(2)}s</title></rect>)}
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
      {geometry.dots.map(({point,field,index},i) => <circle key={i} cx={x(point.time)} cy={geometry.y(point[field]!,index === 3 ? 2 : index === 0 ? 0 : 1)} r={1.6} fill={["#60a5fa","#34d399","#fb7185","#a78bfa"][index]} />)}
      {Array.from({ length: 6 }, (_, i) => { const t = range[0] + (range[1]-range[0])*i/5; return <text key={i} x={x(t)} y={578} textAnchor={i===0 ? "start" : i===5 ? "end" : "middle"} fill="currentColor" opacity={.65} fontSize="11">{clock(t)}</text>; })}
      {selected && selected.time >= range[0] && selected.time <= range[1] && <line x1={x(selected.time)} x2={x(selected.time)} y1={20} y2={550} stroke="currentColor" opacity={.6} strokeDasharray="4 4" />}
      {drag && <rect x={x(Math.min(...drag))} width={Math.max(1,Math.abs(x(drag[1])-x(drag[0])))} y={20} height={530} fill="#60a5fa" opacity={.2} />}
    </svg>
    {hover && <div role="tooltip" className="pointer-events-none absolute right-3 top-3 max-w-sm rounded-lg border border-app-border bg-app-bg p-3 text-xs shadow-xl">
      <p className="font-semibold">{new Date(hover.point.timestamp).toLocaleString(undefined,{timeZoneName:"short"})}</p>
      <p>Bids {hover.point.bid_contracts?.toLocaleString() ?? "unavailable"} · Asks {hover.point.ask_contracts?.toLocaleString() ?? "unavailable"}</p>
      <p>{hover.point.bid_levels} bid levels / {hover.point.ask_levels} ask levels · Mid {hover.point.mid_price ?? "unavailable"}</p>
      <p>Quality: {hover.point.quality_flags.join(", ").replaceAll("_"," ") || "No flags"}</p>
      <p>{hover.point.capture_id.startsWith("live") ? "Provisional live sample" : "Recorded observation"} · Generation {hover.point.generation}</p>
      {hoverGap && <p className="text-amber-400">Gap {(hoverGap.end-hoverGap.start).toFixed(2)}s: {hoverGap.reason}. Values are from the nearest observation.</p>}
    </div>}
  </div>;
});
