import { useSyncExternalStore } from 'react';
import { simulationTime } from './simulationPreview';
import { bound } from './simulationViewport';
import { inspectCursor, type ChartCursor, type ChartInspectionView } from './simulationChartCursor';

const money = (value: number) => value.toFixed(2);
type Props = { cursor: ChartCursor; view: ChartInspectionView };

export function SimulationChartReadout({ cursor, view, sma, vwap }: Props & { sma?: (number | null)[]; vwap?: (number | null)[] }) {
  const state = useSyncExternalStore(cursor.subscribe, cursor.getSnapshot);
  const { index, inspected, selected, price } = inspectCursor(state, view);
  const indicatorIndex = inspected ? view.firstVisible + index : view.fallbackIndex;
  return <>
    <div className="simulation-ohlc" aria-live="off"><span>Sim {simulationTime(selected.time)}</span>
      {([['O', selected.open], ['H', selected.high], ['L', selected.low], ['C', selected.close]] as const).map(([label, value]) => <span key={label}>{label} <strong>{money(value)}</strong></span>)}
      {sma && <span className="simulation-sma-legend">SMA {sma[indicatorIndex]?.toFixed(2) ?? 'unavailable'}</span>}
      {vwap && <span className="simulation-vwap-legend">VWAP {vwap[indicatorIndex]?.toFixed(2) ?? 'unavailable'}</span>}
    </div>
    <span className="sr-only" role="status">{inspected ? `Sim ${simulationTime(inspected.time)}. Open ${money(inspected.open)}, high ${money(inspected.high)}, low ${money(inspected.low)}, close ${money(inspected.close)}.` : state.pointer ? `No candle at cursor. Price ${money(price)}.` : ''}</span>
  </>;
}

export function SimulationCrosshair({ cursor, view }: Props) {
  const state = useSyncExternalStore(cursor.subscribe, cursor.getSnapshot);
  const { inspected, x, y, price, shown } = inspectCursor(state, view);
  if (!shown) return null;
  const { plotLeft, plotRight, plotTop, plotBottom, height } = view;
  return <g className="simulation-crosshair" pointerEvents="none">
    <line x1={x} x2={x} y1={plotTop} y2={plotBottom} /><line x1={plotLeft} x2={plotRight} y1={y} y2={y} /><circle cx={x} cy={y} r={4} />
    <rect x={plotRight + 2} y={y - 10} width={80} height={20} className="simulation-crosshair-label" /><text x={plotRight + 6} y={y + 4} className="simulation-axis-label">{money(price)}</text>
    {inspected && <><rect x={bound(x - 40, plotLeft, plotRight - 90)} y={height - 28} width={90} height={24} className="simulation-crosshair-label" /><text x={bound(x - 36, plotLeft + 4, plotRight - 86)} y={height - 11} className="simulation-axis-label">{simulationTime(inspected.time)}</text></>}
  </g>;
}
