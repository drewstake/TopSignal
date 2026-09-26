-- Nullable metadata only. Do not backfill: the first receipt of legacy rows is unknown.
-- No historical import, retention change, new index, or growth in candle row count.
-- New/updated rows add roughly 80 bytes each (8-byte time + 64-byte SHA + tuple overhead).
-- Run db/diagnostics/storage_usage.sql before deploying, per storage policy.
alter table public.projectx_market_candles
    add column if not exists first_fetched_at timestamptz,
    add column if not exists revision_hash varchar(64);
