// Exponential damping follows sustained moves while suppressing rapid reversals.
// This is display-only: never feed the result back into matching or recorded OHLC.
export function smoothDisplayPrice(current: number, target: number, elapsedMs: number): number {
  const weight = 1 - Math.exp(-Math.max(0, elapsedMs) / 240);
  const next = current + (target - current) * weight;
  return Math.abs(target - next) < 0.0001 ? target : next;
}
