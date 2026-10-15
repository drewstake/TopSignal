import type { SimulationCandle } from './simulationPreview';
import { bound } from './simulationViewport';
import { priceScale } from './simulationPriceScale';

export type ChartPoint = { x: number; y: number };
type CursorState = { pointer: ChartPoint | null; time: number | null };
export type ChartInspectionView = {
  visible: SimulationCandle[]; last: SimulationCandle; firstVisible: number; fallbackIndex: number;
  start: number; spacing: number; minimum: number; maximum: number; log?: boolean;
  plotLeft: number; plotRight: number; plotTop: number; plotBottom: number; height: number;
};

// Only the small inspection components subscribe. Pointer input never republishes
// the market or rerenders the candle/grid/indicator layers, and needs no timer.
export function createChartCursor() {
  let state: CursorState = { pointer: null, time: null };
  const listeners = new Set<() => void>();
  const publish = (next: CursorState) => {
    if (state.time === next.time && state.pointer?.x === next.pointer?.x && state.pointer?.y === next.pointer?.y) return;
    state = next;
    listeners.forEach(listener => listener());
  };
  return {
    getSnapshot: () => state,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    move: (pointer: ChartPoint) => publish({ pointer, time: null }),
    inspect: (time: number) => publish({ pointer: null, time }),
    clear: () => publish({ pointer: null, time: null }),
  };
}
export type ChartCursor = ReturnType<typeof createChartCursor>;

export function inspectCursor({ pointer, time }: CursorState, view: ChartInspectionView) {
  const { visible, firstVisible, start, spacing, plotLeft, plotRight, plotTop, plotBottom } = view;
  const index = pointer ? Math.floor((pointer.x - plotLeft) / spacing + start) - firstVisible : visible.findIndex(bar => bar.time === time);
  const inspected = visible[index], selected = inspected ?? view.last;
  const x = pointer ? bound(pointer.x, plotLeft, plotRight) : plotLeft + (firstVisible + index - start + .5) * spacing;
  const scale = priceScale(view, plotTop, plotBottom);
  const y = pointer ? bound(pointer.y, plotTop, plotBottom) : scale.y(selected.close);
  const price = scale.price(y);
  return { index, inspected, selected, x, y, price, shown: Number.isFinite(y) && (!!pointer || !!inspected) };
}
