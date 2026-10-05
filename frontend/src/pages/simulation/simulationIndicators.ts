import type { Execution, Instrument, SimulationCandle } from './simulationModel';

export function movingAverage(candles: SimulationCandle[], lookback: number): (number | null)[] {
  if (!Number.isInteger(lookback) || lookback < 2 || lookback > 200) return candles.map(() => null);
  let sum = 0;
  return candles.map((bar, index) => {
    sum += bar.close;
    if (index >= lookback) sum -= candles[index - lookback].close;
    return index + 1 >= lookback ? sum / lookback : null;
  });
}
// Exact execution VWAP over the retained tape only. No estimates from OHLC.
export function retainedVwap(candles: SimulationCandle[], trades: Execution[], instrument: Instrument, tick: number, seconds: number) {
  const tape = trades.filter(t => t.contract === instrument);
  let cursor = 0, value = 0, volume = 0;
  return candles.map(bar => {
    while (cursor < tape.length && tape[cursor].time < bar.time + seconds) {
      const trade = tape[cursor++]; value += trade.ticks * tick * trade.quantity; volume += trade.quantity;
    }
    return volume && tape[0].time < bar.time + seconds ? value / volume : null;
  });
}
