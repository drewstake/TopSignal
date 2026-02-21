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

export interface AccountInfo {
  id: number;
  name: string;
  balance: number;
  status: string;
}

export interface AccountTrade {
  id: number;
  account_id: number;
  contract_id: string;
  symbol: string;
  side: string;
  size: number;
  price: number;
  timestamp: string;
  fees: number;
  pnl: number | null;
  order_id: string;
  source_trade_id: string | null;
}

export interface AccountSummary {
  realized_pnl: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
  win_rate: number;
  win_count: number;
  loss_count: number;
  breakeven_count: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  avg_win_duration_minutes: number;
  avg_loss_duration_minutes: number;
  expectancy_per_trade: number;
  tail_risk_5pct: number;
  max_drawdown: number;
  average_drawdown: number;
  risk_drawdown_score: number;
  max_drawdown_length_hours: number;
  recovery_time_hours: number;
  average_recovery_length_hours: number;
  trade_count: number;
  half_turn_count: number;
  execution_count: number;
  day_win_rate: number;
  green_days: number;
  red_days: number;
  flat_days: number;
  avg_trades_per_day: number;
  active_days: number;
  efficiency_per_hour: number;
  profit_per_day: number;
}

export interface AccountTradeRefreshResult {
  fetched_count: number;
  inserted_count: number;
}

export interface AccountPnlCalendarDay {
  date: string;
  trade_count: number;
  gross_pnl: number;
  fees: number;
  net_pnl: number;
}
