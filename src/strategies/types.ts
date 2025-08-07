export type TradeAction = 'buy' | 'sell' | 'hold';

export interface TradeSignal {
  action: TradeAction;
  confidence: number; // 0 (no conviction) to 1 (max conviction)
}
