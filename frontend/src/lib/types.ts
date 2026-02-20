export interface TradeRecord {
  id: number;
  account_id: number;
  symbol: string;
  side: "LONG" | "SHORT";
  opened_at: string;
  closed_at: string | null;
  qty: number;
  entry_price: number;
  exit_price: number | null;
  pnl: number | null;
  fees: number | null;
  notes: string | null;
  is_rule_break: boolean;
  rule_break_type: string | null;
}

export interface SummaryMetrics {
  trade_count: number;
  net_pnl: number;
  win_rate: number;
  profit_factor: number;
  expectancy: number;
  average_win: number;
  average_loss: number;
  average_win_loss_ratio: number;
  max_drawdown: number;
  largest_losing_trade: number;
  average_hold_minutes: number;
  average_hold_minutes_winners: number;
  average_hold_minutes_losers: number;
}

export interface HourPnlPoint {
  hour: number;
  trade_count: number;
  pnl: number;
}

export interface DayPnlPoint {
  day_of_week: number;
  day_label: string;
  trade_count: number;
  pnl: number;
}

export interface SymbolPnlPoint {
  symbol: string;
  trade_count: number;
  pnl: number;
  win_rate: number;
}

export interface PnlAfterLossPoint {
  loss_streak: number;
  trade_count: number;
  total_pnl: number;
  average_pnl: number;
}

export interface StreakMetrics {
  current_win_streak: number;
  current_loss_streak: number;
  longest_win_streak: number;
  longest_loss_streak: number;
  pnl_after_losses: PnlAfterLossPoint[];
}

export interface BehaviorMetrics {
  trade_count: number;
  average_position_size: number;
  max_position_size: number;
  rule_break_count: number;
  rule_break_pnl: number;
  rule_following_pnl: number;
}
