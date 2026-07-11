import { useCallback, useEffect, useRef } from "react";

export class LatestRequestGate {
  private generation = 0;

  begin() {
    const generation = ++this.generation;
    return () => this.generation === generation;
  }

  invalidate() {
    this.generation += 1;
  }
}
/**
 * Creates a generation guard for an async UI load. Starting a newer load makes
 * every older guard stale, and unmounting invalidates the active load.
 */
export function useLatestRequestGuard() {
  const gateRef = useRef<LatestRequestGate | null>(null);
  if (gateRef.current === null) {
    gateRef.current = new LatestRequestGate();
  }

  useEffect(
    () => () => {
      gateRef.current?.invalidate();
    },
    [],
  );

  return useCallback(() => {
    return gateRef.current!.begin();
  }, []);
}
