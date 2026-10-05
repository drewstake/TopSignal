import type { SimulationCandle } from './simulationPreview';
import { bound } from './simulationViewport';

export type ChartPoint = { x: number; y: number };
type CursorState = { pointer: ChartPoint | null; time: number | null };
export type ChartInspectionView = {
  visible: SimulationCandle[]; last: SimulationCandle; firstVisible: number; fallbackIndex: number;
  start: number; spacing: number; minimum: number; maximum: number;
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
  const { visible, firstVisible, start, spacing, minimum, maximum, plotLeft, plotRight, plotTop, plotBottom } = view;
  const index = pointer ? Math.floor((pointer.x - plotLeft) / spacing + start) - firstVisible : visible.findIndex(bar => bar.time === time);
  const inspected = visible[index], selected = inspected ?? view.last;
  const x = pointer ? bound(pointer.x, plotLeft, plotRight) : plotLeft + (firstVisible + index - start + .5) * spacing;
  const y = pointer ? bound(pointer.y, plotTop, plotBottom) : plotTop + (maximum - selected.close) / (maximum - minimum) * (plotBottom - plotTop);
  const price = maximum - (y - plotTop) / (plotBottom - plotTop) * (maximum - minimum);
  return { index, inspected, selected, x, y, price, shown: !!pointer || !!inspected };
}
