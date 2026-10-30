import type { HistoryPoint } from '../orderBookHistory/history';

export type Condition = { feature: string; operator: 'lte' | 'gte'; threshold: number };
export type Execution = {
  direction: 'long' | 'short'; order: 'market' | 'limit_unresolved'; entry_delay_ms: number;
  entry_latency_ms: number; exit_latency_ms: number; stop_ticks: number; target_ticks: number;
  equal_reward_risk: boolean; max_hold_seconds: number; commission_fees_round_trip: number;
  slippage_ticks_per_side: number; size: number; tick_size: number; tick_value: number;
  overlapping: 'skip' | 'independent'; ambiguity: 'stop_first' | 'exclude';
};
export type Study = {
  conditions: Condition[]; combine: 'all' | 'any'; activity_window: number; persistence_seconds: number;
  cooldown_seconds: number; horizons: number[]; nothing_ticks: number; validation_session: string | null;
  test_session: string | null; execution: Execution; threshold_rationale: string;
};
export type Window = { start: string; end: string };
export type ResearchRequest = { kind: 'window' | 'study'; windows: Window[]; scope: string;
  tick_distances: number[]; study: Study; experiment_id?: string | null; forward?: boolean };
export type Activity = Record<string, number | boolean | null>;
export type Point = HistoryPoint & {
  best_bid: number | null; best_ask: number | null; best_bid_quantity: number | null; best_ask_quantity: number | null;
  activity: Record<string, Activity>; baseline_warmup: boolean; baseline_observations: number;
  bid_depth_ratio: number | null; ask_depth_ratio: number | null; negative_imbalance_seconds: number;
  positive_imbalance_seconds: number; bid_thin_seconds: number | null; ask_thin_seconds: number | null;
  provider_at: string | null; feature_version: string; volatility: number | null; session: string;
  reconstruction_id: string; explicit_depth_levels: { bid: number; ask: number };
  execution_interval?: { valid: boolean; after_source_index: number | null; bid_low: number | null; bid_high: number | null; ask_low: number | null; ask_high: number | null };
};
export type Frame = { time: number; timestamp: string; contract_id: string; capture_id: string; index: number;
  generation: number; quality_flags: string[]; mid_price: number | null; last_trade_price: number | null;
  bids: { price: number; size: number }[]; asks: { price: number; size: number }[]; reconstruction_id: number };
export type Coverage = { earliest: string | null; latest: string | null; available_days: string[]; segments: number;
  compressed_bytes: number; skipped_segments: number; source: string; max_seconds: number };
export type WindowResult = Coverage & { points: Point[]; frames: Frame[]; data_id: string; start: string; end: string;
  scope: string; contracts: string[]; reconstruction: string; raw_events: number; read_bytes: number; feature_version: string;
  observed_sequence_diagnostics: Record<string, number>; provider_trade_types: Record<string, number>;
  excluded_intervals: { start: string; end: string; reason: string }[] };
export type Outcome = { valid: boolean; reason?: string; return_points?: number; favorable?: number; adverse?: number; nothing?: boolean };
export type Episode = { episode: number; timestamp: string; time: number; contract_id: string; session: string; partition: string;
  outcomes: Record<string, Outcome>; control_timestamp: string | null; control: Record<string, Outcome>; features: Record<string, number> };
export type Interval = { status: string; sessions: number; low: number | null; high: number | null; method: string };
export type OutcomeSummary = { n: number; valid: number; missing: number; exclusions: Record<string, number>; mean: number | null;
  median: number | null; p10: number | null; p90: number | null; mean_favorable: number | null; mean_adverse: number | null;
  nothing_rate: number | null; distribution: { low: number; high: number; count: number }[];
  baseline?: OutcomeSummary; paired_mean_difference?: number | null; uncertainty?: Interval };
export type Metrics = { trades: number; attempts: number; exclusions: Record<string, number>; ambiguous: number;
  gross_expectancy: number | null; net_expectancy: number | null; net_expectancy_r: number | null; win_rate: number | null;
  realized_reward_risk: number | null; profit_factor: number | null; net_profit: number; max_drawdown: number;
  longest_loss_streak: number; exposure_seconds: number; break_even_round_trip_cost: number | null;
  cost_sensitivity: { additional_round_trip_cost: number; net_expectancy: number | null }[]; uncertainty: Interval };
export type Trade = { timestamp: string; status: string; reason: string; net?: number; gross?: number; fees?: number; ambiguous?: boolean };
export type StudyResult = { engine_version: string; occurrences: number; independent_episodes: number; episodes: Episode[];
  sessions: string[]; days: string[]; invalid_measurements: number; unavailable_feature_measurements: number;
  outcome_summary: Record<string, OutcomeSummary>; breakdowns: Record<string, Record<string, Record<string, OutcomeSummary>>>;
  tester: { metrics: Metrics; trades: Trade[] }; harsher: { metrics: Metrics }; status: string; confirmation: string;
  walk_forward: { session: string; prior_sessions: number; partition: string; metrics: Metrics }[];
  partition_metrics: Record<string, Metrics>; methodology: Record<string, string>; tested_variations_before_this_run: number;
  coverage: (Coverage & { data_id: string; start: string; end: string; raw_events: number; read_bytes: number })[] };
export type Job = { id: string; state: string; kind: 'window' | 'study'; created_at: string; progress: number; message: string;
  request: ResearchRequest; result?: WindowResult | StudyResult; result_bytes?: number; code_version?: string };
export type Bookmark = { id: string; created_at: string; note: string; job_id: string; timestamp: string; contract_id: string; scope: string; feature_version: string };
export type Experiment = { id: string; created_at: string; version: number; hypothesis: string; notes: string; frozen: boolean;
  result_job_id: string; forward_after: string; request: ResearchRequest; tested_variations: number; version_digest: string; status: string };
export type Notebook = { jobs: Job[]; experiments: Experiment[]; bookmarks: Bookmark[]; jobs_total: number;
  storage: { bytes: number; budget_bytes: number; blocked_reason: string | null } };

export const defaultStudy: Study = { conditions: [{ feature: 'imbalance', operator: 'lte', threshold: -.3 }],
  combine: 'all', activity_window: 5, persistence_seconds: 1, cooldown_seconds: 30, horizons: [5,15,60], nothing_ticks: 2,
  validation_session: null, test_session: null, threshold_rationale: 'Manual exploratory threshold; not optimized.',
  execution: { direction: 'short', order: 'market', entry_delay_ms: 0, entry_latency_ms: 250, exit_latency_ms: 250,
    stop_ticks: 8, target_ticks: 8, equal_reward_risk: true, max_hold_seconds: 30, commission_fees_round_trip: 1.24,
    slippage_ticks_per_side: 1, size: 1, tick_size: .25, tick_value: .5, overlapping: 'skip', ambiguity: 'stop_first' } };
export const fmt = (v: number | null | undefined, digits=2) => v == null ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: digits });
export const percent = (v: number | null | undefined) => v == null ? '—' : `${fmt(v*100,1)}%`;
export const when = (v: string) => new Date(v).toLocaleString(undefined, { timeZoneName: 'short' });
export const inputClass = 'h-10 min-w-0 w-full rounded-lg border border-app-border bg-app-bg px-3 text-sm text-app-text';

export function atOrBefore<T extends {time:number}>(points: T[], at: number): T | undefined {
  let low=0,high=points.length;
  while(low<high){const middle=Math.floor((low+high)/2);if(points[middle].time<=at)low=middle+1;else high=middle;}
  return points[low-1];
}
