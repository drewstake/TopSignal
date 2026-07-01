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
  snapshot_version?: number;
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

export type AIJournalRecapMode = "append_or_create";

export interface AIJournalRecapInput {
  entry_date: string;
  mode?: AIJournalRecapMode;
  include_existing_notes?: boolean;
}

export interface AIJournalRecapResult {
  account_id: number;
  entry_date: string;
  journal_entry_id: number | null;
  created: boolean;
  updated: boolean;
  skipped: boolean;
  skip_reason: string | null;
  source_trade_count: number;
  recap_markdown: string;
  generated_at: string;
}

export type JournalMergeConflictStrategy = "skip" | "overwrite";

export interface JournalMergeInput {
  from_account_id: number;
  to_account_id: number;
  on_conflict: JournalMergeConflictStrategy;
  include_images: boolean;
}

export interface JournalMergeResult {
  from_account_id: number;
  to_account_id: number;
  transferred_count: number;
  skipped_count: number;
  overwritten_count: number;
  image_count: number;
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

export type BotExecutionMode = "dry_run" | "live";
export type BotTimeframeUnit = "second" | "minute" | "hour" | "day" | "week" | "month";
export type BotAction = "BUY" | "SELL" | "HOLD" | "NONE" | "STOP";
export type BotStrategyType =
  | "sma_cross"
  | "support_resistance"
  | "donchian_breakout"
  | "fvg_sweep_mss"
  | "liquidity_sweep_retest"
  | "opening_rvol_breakout"
  | "supertrend_pivot"
  | "atr_adjusted_relative_strength"
  | "relative_strength_spy"
  | "pullback_trap_reversal"
  | "bollinger_rsi_reversal"
  | "bollinger_mean_reversion"
  | "macd_support_resistance"
  | "ema_trend_pullback"
  | "ema_scalping"
  | "delayed_orb_confirmation"
  | "orb_fibonacci_pullback"
  | "fisher_transform_mean_reversion"
  | "vwap_atr_mean_reversion"
  | "vwap_gap_retrace";
export type BotTakeProfitMode = "vwap" | "half_vwap_distance" | "r_multiple" | "middle_band" | "two_r" | "fixed_r";
export type BotDelayedOrbTakeProfitMode = "r_1_5" | "r_2" | "end_of_day";
export type BotLiquiditySweepTargetMode = "2r" | "3r" | "next_liquidity";
export type BotTrailingStopMode = "atr" | "swing" | "moving_average";
export type BotOrbStopMode = "inside_range" | "opposite_side";
export type BotOrbTargetMode = "2r" | "3r" | "measured_move" | "day_extreme";
export type BotMarketBias = "bullish" | "bearish" | "neutral";

export interface BotDirectionalProbabilities {
  bullish: number;
  bearish: number;
  sideways: number;
}

export type TradePlanDirection = "long" | "short";
export type TradePlanTrend = "bullish" | "bearish" | "neutral" | "unknown";
export type TradePlanTimeOfDay = "premarket" | "open" | "ny_am" | "lunch" | "power_hour" | "close" | "overnight";
export type TradePlanMarketRegime = "trend" | "range" | "chop" | "breakout" | "reversal" | "unknown";
export type TradePlanNewsRisk = "low" | "medium" | "high";
export type TradeEvaluationDecision = "take" | "wait" | "avoid";
export type TradeEvaluationConfidence = "low" | "medium" | "high";
export type TradeEvaluationGrade = "A" | "B" | "C" | "D" | "F";

export interface TradePlanInput {
  symbol: string;
  direction: TradePlanDirection;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  timestamp: string;
  account_balance?: number | null;
  current_day_pnl?: number | null;
  max_daily_loss?: number | null;
  trailing_drawdown?: number | null;
}

export interface MarketContextInput {
  current_price: number;
  high_of_day?: number | null;
  low_of_day?: number | null;
  previous_day_high?: number | null;
  previous_day_low?: number | null;
  previous_close?: number | null;
  open_price?: number | null;
  vwap?: number | null;
  anchored_vwap?: number | null;
  volume_profile_poc?: number | null;
  value_area_high?: number | null;
  value_area_low?: number | null;
  ema21_5m?: number | null;
  ema21_15m?: number | null;
  ema21_1h?: number | null;
  ema21_4h?: number | null;
  ma200_5m?: number | null;
  ma200_15m?: number | null;
  ma200_1h?: number | null;
  ma200_4h?: number | null;
  trend5m?: TradePlanTrend;
  trend15m?: TradePlanTrend;
  trend1h?: TradePlanTrend;
  trend4h?: TradePlanTrend;
  atr1m?: number | null;
  atr5m?: number | null;
  atr15m?: number | null;
  atr1h?: number | null;
  average_daily_range?: number | null;
  current_day_range?: number | null;
  current_volume?: number | null;
  average_volume_at_time?: number | null;
  relative_volume?: number | null;
  cumulative_delta?: number | null;
  time_of_day?: TradePlanTimeOfDay;
  market_regime?: TradePlanMarketRegime;
  news_risk?: TradePlanNewsRisk;
  es_trend?: TradePlanTrend | null;
  nq_trend?: TradePlanTrend | null;
  vix_trend?: TradePlanTrend | null;
  ten_year_yield_trend?: TradePlanTrend | null;
  nvda_trend?: TradePlanTrend | null;
  smh_trend?: TradePlanTrend | null;
}

export interface TradePlanEvaluationInput {
  trade_plan: TradePlanInput;
  market_context: MarketContextInput;
}

export interface TradePlanFeatures {
  risk_points: number;
  reward_points: number;
  risk_reward_ratio: number | null;
  breakeven_win_rate: number | null;
  is_long: boolean;
  is_short: boolean;
  price_above_vwap: boolean | null;
  price_below_vwap: boolean | null;
  entry_distance_from_vwap_points: number | null;
  entry_distance_from_vwap_atr: number | null;
  vwap_supports_direction: boolean | null;
  distance_from_high_of_day: number | null;
  distance_from_low_of_day: number | null;
  distance_from_previous_day_high: number | null;
  distance_from_previous_day_low: number | null;
  entry_near_high_of_day: boolean;
  entry_near_low_of_day: boolean;
  take_profit_blocked_by_high_of_day: boolean;
  take_profit_blocked_by_low_of_day: boolean;
  stop_atr_multiple: number | null;
  target_atr_multiple: number | null;
  is_stop_too_tight: boolean;
  is_stop_too_wide: boolean;
  is_target_realistic: boolean;
  trend_alignment_score: number;
  aligned_timeframes: number;
  conflicting_timeframes: number;
  higher_timeframe_conflict: boolean;
  stop_behind_structure: boolean;
  entry_chasing: boolean;
  has_room_to_target: boolean;
  bad_location: boolean;
  max_loss_risk_percent: number | null;
  daily_loss_danger: boolean | null;
  should_reduce_size: boolean | null;
}

export interface TradeEvaluationResult {
  total_score: number;
  score: number;
  grade: TradeEvaluationGrade;
  decision: TradeEvaluationDecision;
  confidence: TradeEvaluationConfidence;
  summary: string;
  reasons: string[];
  warnings: string[];
  positives: string[];
  suggested_adjustments: string[];
  features: TradePlanFeatures;
  category_scores: Record<string, number>;
}

export interface ProjectXStyleIndicatorSnapshot {
  rsi: number | null;
  atr: number | null;
  vwap: number | null;
  bollinger: {
    middle: number | null;
    upper: number | null;
    lower: number | null;
  } | null;
  fair_value_gaps: {
    detected_count?: number;
    active_count: number;
    latest: Record<string, unknown> | null;
  };
  order_blocks: {
    detected_count?: number;
    active_count: number;
    latest: Record<string, unknown> | null;
  };
  candlestick_patterns: Array<Record<string, unknown>>;
  waddah_attar: Record<string, unknown> | null;
}

export interface BotAnalysis {
  current_price: number | null;
  previous_close: number | null;
  price_change: number | null;
  price_change_percent: number | null;
  trend: BotMarketBias;
  trend_strength: number;
  volatility_state: "low" | "normal" | "elevated" | "extreme";
  volume_state: "low" | "normal" | "elevated";
  support_levels: number[];
  resistance_levels: number[];
  nearest_support: number | null;
  nearest_resistance: number | null;
  bullish_probability: number;
  bearish_probability: number;
  sideways_probability: number;
  expected_move: number | null;
  expected_move_percent?: number | null;
  invalidation_level: number | null;
  summary: string;
  reasoning: string[];
  risk_notes: string[];
  indicators?: ProjectXStyleIndicatorSnapshot | null;
  /** Timestamp of the latest candle the analysis was computed from. */
  candle_timestamp?: string | null;
  generated_at?: string | null;
  trade_evaluation?: TradeEvaluationResult | null;
}

export interface BotStrategyParams {
  entry_period?: number;
  exit_period?: number;
  bars_per_timeframe?: number;
  swing_window?: number;
  level_tolerance_percent?: number;
  stop_beyond_level_percent?: number;
  reclaim_within_bars?: number;
  retest_within_bars?: number;
  stop_beyond_sweep_percent?: number;
  take_profit_r_multiple?: number;
  relative_volume_lookback_days?: number;
  min_relative_volume?: number;
  min_opening_volume?: number;
  min_body_to_range_ratio?: number;
  daily_bars?: number;
  supertrend_period?: number;
  supertrend_multiplier?: number;
  pivot_tolerance_percent?: number;
  chop_lookback_bars?: number;
  chop_max_flips?: number;
  chop_max_range_percent?: number;
  signal_period?: number;
  initial_stop_atr_multiplier?: number;
  trailing_stop_mode?: BotTrailingStopMode;
  trailing_atr_multiplier?: number;
  trailing_ma_period?: number;
  benchmark_symbol?: string;
  benchmark_contract_id?: string | null;
  comparison_bars?: number;
  pullback_lookback_bars?: number;
  relative_volume_period?: number;
  minimum_relative_volume?: number;
  minimum_relative_strength_percent?: number;
  minimum_benchmark_move_percent?: number;
  ema_period?: number;
  major_level_lookback_bars?: number;
  entry_level_tolerance_percent?: number;
  stop_buffer_percent?: number;
  opening_range_minutes?: number;
  confirmation_minutes?: number;
  stop_mode?: BotOrbStopMode;
  swing_lookback_bars?: number;
  volume_average_period?: number;
  long_rsi_min?: number;
  long_rsi_max?: number;
  short_rsi_min?: number;
  short_rsi_max?: number;
  partial_take_profit_r_multiple?: number;
  final_take_profit_r_multiple?: number;
  trend_sma_period?: number;
  stop_after_losses_per_session?: number;
  target_mode?: BotOrbTargetMode | BotLiquiditySweepTargetMode | "recent_swing";
  stop_reference?: "ema19" | "micro_swing" | "wider";
  micro_swing_window?: number;
  target_lookback_bars?: number;
  move_lookback_bars?: number;
  atr_period?: number;
  relative_volume_cap?: number;
  long_score_threshold?: number;
  short_score_threshold?: number;
  stop_structure_window?: number;
  stop_atr_multiple?: number;
  rsi_period?: number;
  bollinger_period?: number;
  bollinger_stddev?: number;
  adx_period?: number;
  stretch_atr_multiple?: number;
  rsi_oversold?: number;
  rsi_overbought?: number;
  adx_max?: number;
  vwap_slope_bars?: number;
  flat_vwap_threshold_bps?: number;
  local_extreme_lookback?: number;
  stop_buffer_atr?: number;
  volume_lookback_bars?: number;
  strong_volume_multiplier?: number;
  take_profit_mode?: BotTakeProfitMode | BotLiquiditySweepTargetMode | BotDelayedOrbTakeProfitMode | BotOrbTargetMode;
  fisher_length?: number;
  fisher_extreme_threshold?: number;
  price_stretch_percent?: number;
  ema_slope_lookback_bars?: number;
  ema_slope_max_percent?: number;
  swing_stop_lookback_bars?: number;
  min_gap_percent?: number;
  wait_start_minutes?: number;
  wait_end_minutes?: number;
  min_volume_ratio?: number;
  stop_beyond_vwap_percent?: number;
  touch_tolerance_percent?: number;
  bars_to_fetch?: number;
  micro_level_window?: number;
  volume_baseline_bars?: number;
  volume_spike_multiple?: number;
  wick_to_body_ratio_min?: number;
  trend_confirmation_bars?: number;
  min_countertrend_bars?: number;
  pullback_range_multiplier?: number;
  prior_swing_window?: number;
  atr_stop_multiple?: number;
  atr_trail_multiple?: number;
  atr_size_reference_percent?: number;
  min_size_scale?: number;
  atr_stop_buffer?: number;
  news_blackout_windows?: string[];
}

export interface ProjectXContract {
  id: string;
  name: string;
  description: string | null;
  tick_size: number | null;
  tick_value: number | null;
  active_contract: boolean | null;
  symbol_id: string | null;
}

export interface ProjectXMarketCandle {
  id: number | null;
  contract_id: string;
  symbol: string | null;
  live: boolean;
  unit: BotTimeframeUnit;
  unit_number: number;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_partial: boolean;
  fetched_at: string | null;
}

export interface ProjectXMarketPrice {
  contract_id: string;
  symbol: string | null;
  price: number;
  timestamp: string;
}

export interface BotConfig {
  id: number;
  name: string;
  account_id: number;
  provider: string;
  enabled: boolean;
  execution_mode: BotExecutionMode;
  strategy_type: BotStrategyType;
  strategy_params: BotStrategyParams;
  contract_id: string;
  symbol: string | null;
  timeframe_unit: BotTimeframeUnit;
  timeframe_unit_number: number;
  lookback_bars: number;
  fast_period: number;
  slow_period: number;
  order_size: number;
  max_contracts: number;
  max_daily_loss: number;
  max_trades_per_day: number;
  max_open_position: number;
  allowed_contracts: string[];
  trading_start_time: string;
  trading_end_time: string;
  cooldown_seconds: number;
  max_data_staleness_seconds: number;
  allow_market_depth: boolean;
  created_at: string;
  updated_at: string;
}

export interface BotConfigInput {
  name: string;
  account_id: number;
  contract_id: string;
  symbol?: string | null;
  enabled?: boolean;
  execution_mode?: BotExecutionMode;
  strategy_type?: BotStrategyType;
  strategy_params?: BotStrategyParams;
  timeframe_unit?: BotTimeframeUnit;
  timeframe_unit_number?: number;
  lookback_bars?: number;
  fast_period?: number;
  slow_period?: number;
  order_size?: number;
  max_contracts?: number;
  max_daily_loss?: number;
  max_trades_per_day?: number;
  max_open_position?: number;
  allowed_contracts?: string[];
  trading_start_time?: string;
  trading_end_time?: string;
  cooldown_seconds?: number;
  max_data_staleness_seconds?: number;
  allow_market_depth?: boolean;
}

export type BotConfigUpdateInput = Partial<BotConfigInput>;

export interface BotConfigListResponse {
  items: BotConfig[];
  total: number;
}

export interface BotRun {
  id: number;
  bot_config_id: number;
  account_id: number;
  status: "running" | "stopped" | "blocked" | "error";
  dry_run: boolean;
  started_at: string;
  stopped_at: string | null;
  stop_reason: string | null;
  last_heartbeat_at: string | null;
}

export interface BotDecision {
  id: number;
  bot_config_id: number;
  bot_run_id: number | null;
  account_id: number;
  contract_id: string;
  symbol: string | null;
  decision_type: string;
  action: BotAction;
  reason: string;
  candle_timestamp: string | null;
  price: number | null;
  quantity: number | null;
  created_at: string;
}

export interface BotOrderAttempt {
  id: number;
  bot_config_id: number;
  bot_run_id: number | null;
  bot_decision_id: number | null;
  account_id: number;
  contract_id: string;
  side: "BUY" | "SELL";
  order_type: string;
  size: number;
  status: "pending" | "dry_run" | "submitted" | "blocked" | "rejected" | "error";
  provider_order_id: string | null;
  rejection_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface BotRiskEvent {
  id: number;
  bot_config_id: number;
  bot_run_id: number | null;
  account_id: number;
  severity: "info" | "warning" | "critical";
  code: string;
  message: string;
  created_at: string;
}

export interface BotEvaluation {
  config: BotConfig;
  run: BotRun | null;
  decision: BotDecision;
  order_attempt: BotOrderAttempt | null;
  risk_events: BotRiskEvent[];
  analysis?: BotAnalysis | null;
  candles: ProjectXMarketCandle[];
}

export interface BotActivity {
  config: BotConfig;
  runs: BotRun[];
  decisions: BotDecision[];
  order_attempts: BotOrderAttempt[];
  risk_events: BotRiskEvent[];
}
