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
  provider_name: string;
  custom_display_name: string | null;
  balance: number;
  status: string;
  account_state: "ACTIVE" | "LOCKED_OUT" | "HIDDEN" | "MISSING";
  is_main: boolean;
  can_trade: boolean | null;
  is_visible: boolean | null;
  last_trade_at: string | null;
}

export interface AccountMainUpdateResult {
  account_id: number;
  is_main: boolean;
}

export interface AccountRenameResult {
  account_id: number;
  name: string;
  provider_name: string;
  custom_display_name: string | null;
}

export interface AccountLastTradeInfo {
  account_id: number;
  last_trade_at: string | null;
  source: string;
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
  entry_time?: string | null;
  exit_time?: string;
  duration_minutes?: number | null;
  entry_price?: number | null;
  exit_price?: number;
  fees: number;
  pnl: number | null;
  mfe?: number | null;
  mae?: number | null;
  order_id: string;
  source_trade_id: string | null;
}

export type SizingBenchmarkLabel =
  | "Far Below Benchmark"
  | "Below Benchmark"
  | "In Line With Benchmark"
  | "Above Benchmark"
  | "Far Above Benchmark";

export interface AccountSizingBenchmark {
  benchmarkMode: "fixed_average_size";
  benchmarkSizeUsed: number;
  benchmarkGrossPnl: number;
  benchmarkNetPnl: number;
  benchmarkDiff: number;
  benchmarkRatio: number | null;
  benchmarkLabel: SizingBenchmarkLabel;
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
  averagePositionSize: number;
  medianPositionSize: number;
  tradeCountUsedForSizingStats: number;
  avgPointGain: number | null;
  avgPointLoss: number | null;
  pointsBasisUsed: "auto" | "MNQ" | "MES" | "MGC" | "SIL";
  sizingBenchmark: AccountSizingBenchmark;
}

export type PointsBasis = "MNQ" | "MES" | "MGC" | "SIL";

export interface PointPayoffSummary {
  avgPointGain: number | null;
  avgPointLoss: number | null;
}

export interface AccountSummaryWithPointBases {
  summary: AccountSummary;
  point_payoff_by_basis: Record<PointsBasis, PointPayoffSummary>;
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

export type ExpenseCategory = "evaluation_fee" | "activation_fee" | "reset_fee" | "data_fee" | "other";
export type ExpenseAccountType = "no_activation" | "standard" | "practice";
export type ExpensePlanSize = "50k" | "100k" | "150k";
export type ExpenseRange = "week" | "month" | "ytd" | "all_time";

export interface ExpenseRecord {
  id: number;
  account_id: number | null;
  provider: string;
  expense_date: string;
  amount_cents: number;
  amount: number;
  currency: string;
  category: ExpenseCategory;
  account_type: ExpenseAccountType | null;
  plan_size: ExpensePlanSize | null;
  description: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface ExpenseCreateInput {
  expense_date: string;
  amount?: number;
  amount_cents?: number;
  category: ExpenseCategory;
  provider?: string;
  account_id?: number;
  account_type?: ExpenseAccountType;
  plan_size?: ExpensePlanSize;
  description?: string;
  tags?: string[];
  is_practice?: boolean;
  currency?: string;
}

export interface ExpenseUpdateInput {
  expense_date?: string;
  amount_cents?: number;
  category?: ExpenseCategory;
  account_id?: number | null;
  account_type?: ExpenseAccountType;
  plan_size?: ExpensePlanSize;
  description?: string;
  tags?: string[];
  is_practice?: boolean;
}

export interface ExpenseListQuery {
  start_date?: string;
  end_date?: string;
  account_id?: number;
  category?: ExpenseCategory;
  limit?: number;
  offset?: number;
}

export interface ExpenseListResponse {
  items: ExpenseRecord[];
  total: number;
}

export interface ExpenseCategoryTotals {
  amount: number;
  amount_cents: number;
  count: number;
}

export interface ExpenseTotals {
  range: ExpenseRange;
  start_date: string | null;
  end_date: string;
  total_amount: number;
  total_amount_cents: number;
  by_category: Record<string, ExpenseCategoryTotals>;
  count: number;
}

export interface PayoutRecord {
  id: number;
  payout_date: string;
  amount_cents: number;
  amount: number;
  currency: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PayoutCreateInput {
  payout_date: string;
  amount?: number;
  amount_cents?: number;
  notes?: string;
  currency?: string;
}

export interface PayoutListQuery {
  start_date?: string;
  end_date?: string;
  limit?: number;
  offset?: number;
}

export interface PayoutListResponse {
  items: PayoutRecord[];
  total: number;
}

export interface PayoutTotals {
  total_amount: number;
  total_amount_cents: number;
  average_amount: number;
  average_amount_cents: number;
  count: number;
}

export type JournalMood = "Focused" | "Neutral" | "Frustrated" | "Confident";

export interface JournalStatsSnapshot {
  trade_count: number;
  total_pnl: number;
  total_fees: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  largest_win: number;
  largest_loss: number;
  largest_position_size?: number;
  gross: number;
  net: number;
  net_realized_pnl?: number;
}

export interface JournalEntry {
  id: number;
  account_id: number;
  entry_date: string;
  title: string;
  mood: JournalMood;
  tags: string[];
  body: string;
  version: number;
  stats_source: string | null;
  stats_json: JournalStatsSnapshot | null;
  stats_pulled_at: string | null;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface JournalEntryCreateResult extends JournalEntry {
  already_existed: boolean;
}

export interface JournalEntrySaveResult {
  id: number;
  account_id: number;
  entry_date: string;
  title: string;
  mood: JournalMood;
  tags: string[];
  version: number;
  is_archived: boolean;
  updated_at: string;
}

export interface JournalEntriesResponse {
  items: JournalEntry[];
  total: number;
}

export interface JournalEntriesQuery {
  start_date?: string;
  end_date?: string;
  mood?: JournalMood;
  q?: string;
  include_archived?: boolean;
  limit?: number;
  offset?: number;
}

export interface JournalEntryCreateInput {
  entry_date: string;
  title: string;
  mood: JournalMood;
  tags: string[];
  body: string;
}

export interface JournalEntryUpdateInput {
  version: number;
  entry_date?: string;
  title?: string;
  mood?: JournalMood;
  tags?: string[];
  body?: string;
  is_archived?: boolean;
}

export interface JournalEntryImage {
  id: number;
  journal_entry_id: number;
  account_id: number;
  entry_date: string;
  filename: string;
  mime_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  created_at: string;
  url: string;
}

export interface JournalDaysResponse {
  days: string[];
}

export interface JournalDaysQuery {
  start_date: string;
  end_date: string;
  include_archived?: boolean;
}

export interface JournalPullTradeStatsInput {
  trade_ids?: number[];
  entry_date?: string;
  start_date?: string;
  end_date?: string;
}

export interface AuthMe {
  user_id: string;
  email: string | null;
}

export interface ProjectXCredentialsInput {
  username: string;
  api_key: string;
}

export interface ProjectXCredentialsStatus {
  configured: boolean;
}
