export interface CandleStream {
  contract_id: string; symbol: string | null; root_symbol: string; live: boolean;
  unit: string; unit_number: number; source: string; rows: number; complete_rows: number;
  first_timestamp: string; last_timestamp: string; last_fetched_at: string;
  recent_gap_check?: { start: string; end_exclusive: string; expected_open_minutes: number; observed_open_minutes: number; missing_open_minutes: number; note: string } | null;
}
export interface DataInventory {
  generated_at: string; database_rows: number; streams: CandleStream[];
  archive: { status: string; fingerprint: string | null; built_at: string | null;
    series: { symbol: string; timeframe: string; rows: number; first_timestamp: string | null; end_exclusive: string | null; files_present: boolean }[];
    schemas: Record<string, number>; note: string };
  local_capture: { capture_id: string; status: string; source: string; contract_id: string; symbol: string;
    live: boolean; rows: number; first_timestamp: string | null; last_timestamp: string | null;
    matching_database_rows: number; research_exposure: string; note: string };
  feeds: { key: string; label: string; status: string; detail: string }[]; note: string;
}
export interface MarketContext {
  as_of: string; generated_at: string; missing_symbols: string[]; note: string;
  items: { symbol: string; status: string; contract_id: string | null; source: string | null; live: boolean;
    timeframe: string | null; candle_timestamp: string | null; available_at: string | null;
    fetched_at: string | null; age_seconds: number | null; close: number | null;
    volume: number | null; previous_close: number | null; change_pct: number | null; change_period_seconds: number | null;
    observation_date?: string | null; observation_kind?: string | null; value_unit?: string | null;
    change_bps?: number | null; source_url?: string | null; data_notice?: string | null; data_mode?: string | null }[];
}
export interface PublicMarketStatus {
  generated_at: string;
  sources: { symbol: string; source: string; label: string; status: string; enabled: boolean;
    stored_rows: number; latest_observation_date: string | null; last_collected_at: string | null;
    source_url: string; data_notice: string }[];
}
export interface MarketEvent {
  id: string; source: string; source_event_id: string; kind: string; title: string; country: string;
  importance: string; scheduled_at: string | null; scheduled_end_at?: string | null;
  scheduled_date?: string | null; time_precision?: string; published_at: string | null;
  first_seen_at: string; observed_at: string; available_at: string; state: string;
  actual: string | null; forecast: string | null; previous: string | null; revised: string | null; url: string | null;
}
export interface MarketEvents {
  as_of: string; start: string; end: string; events: MarketEvent[];
  sources: { source: string; label: string; status: string; last_attempt_at: string | null;
    last_success_at: string | null; event_count: number; coverage_start: string | null;
    coverage_end: string | null; coverage_scope: string; error_code: string | null;
    actuals_available: boolean; consensus_available: boolean }[];
  risk: { level: string; coverage_trusted: boolean; scope: string; reason: string; nearby_events: MarketEvent[];
    window_before_minutes: number; window_after_minutes: number };
}
export interface Observations {
  enabled: boolean; capture_mode: string; retention_days: number; record_cap: number; queue_capacity: number;
  queued: number; dropped: number; write_errors: number; event_count: number;
  first_received_at: string | null; last_received_at: string | null; counts: Record<string, number>;
  contracts: string[]; warnings: string[];
  profile: { trade_count: number; total_volume: number; classified_volume: number;
    classification_coverage: number | null; delta: number | null; levels: { price: number; volume: number }[]; basis: string };
  spread: { sample_count: number; latest: number | null; mean: number | null };
}
export interface DecisionResearch {
  score_buckets?: { minimum_score: number; maximum_score: number; target: number; stop: number; other: number; resolved_barrier_count: number; target_first_rate: number | null }[];
  items: { id: number; decision_id: number; account_id: number; contract_id: string; action: string; reason: string;
    observed_at: string; score: number | null; score_kind: string; direction: string | null;
    entry_price: number | null; stop_loss: number | null; take_profit: number | null; outcome: string;
    outcome_at: string | null; outcome_details: unknown; snapshot_version: string }[];
  summary: { total: number; pending: number; labeled: number; ambiguous: number; no_geometry: number; score_kind: string };
  execution: { order_attempts: number; matched_orders: number; matched_fill_count: number;
    mean_signed_price_difference: number | null; latency_ms: number | null; limitations: string[] };
}
