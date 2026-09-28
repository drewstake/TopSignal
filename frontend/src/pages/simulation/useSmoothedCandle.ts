import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { SimulationCandle } from './simulationPreview';
import { smoothDisplayPrice } from './simulationMotion';

export function useSmoothedCandle(candle: SimulationCandle) {
  const target = useRef(candle);
  const [motion, setMotion] = useState({ update: candle.update, close: candle.close });
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false);
  useLayoutEffect(() => { target.current = candle; }, [candle]);

  useEffect(() => {
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const syncPreference = () => setReducedMotion(preference?.matches ?? false);
    preference?.addEventListener('change', syncPreference);
    let previous = performance.now();
    let frame = 0;
    const animate = (now: number) => {
      const elapsed = Math.max(0, now - previous);
      previous = now;
      const latest = target.current;
      // Do not animate through time spent in a backgrounded tab.
      const immediate = preference?.matches || elapsed > 250;
      setMotion((current) => {
        const start = current.update === latest.update ? current.close : latest.open;
        const close = immediate ? latest.close : smoothDisplayPrice(start, latest.close, elapsed);
        return current.update === latest.update && current.close === close ? current : { update: latest.update, close };
      });
      frame = window.requestAnimationFrame(animate);
    };
    frame = window.requestAnimationFrame(animate);
    return () => {
      window.cancelAnimationFrame(frame);
      preference?.removeEventListener('change', syncPreference);
    };
  }, []);

  const close = reducedMotion ? candle.close : motion.update === candle.update ? motion.close : candle.open;
  return Math.max(candle.low, Math.min(candle.high, close));
}
