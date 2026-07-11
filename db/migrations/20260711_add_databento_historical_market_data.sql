-- Global, provenance-preserving Databento store for deterministic historical replay.

create table if not exists databento_import_batches (
  id bigserial primary key,
  job_id text not null,
  archive_name text not null,
  archive_sha256 text not null,
  dataset text not null,
  schema_name text not null,
  root_symbol text not null,
  status text not null default 'pending',
  records_read bigint not null default 0,
  records_inserted bigint not null default 0,
  files_completed integer not null default 0,
  manifest_json jsonb,
  error_message text,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint databento_import_batches_status_check
    check (status in ('pending','running','completed','failed')),
  constraint databento_import_batches_counts_nonnegative_check
    check (records_read >= 0 and records_inserted >= 0 and files_completed >= 0),
  constraint databento_import_batches_archive_sha256_length_check
    check (length(archive_sha256) = 64),
  constraint databento_import_batches_completed_at_check
    check (completed_at is null or completed_at >= started_at),
  constraint uq_databento_import_batches_job_id unique (job_id),
  constraint uq_databento_import_batches_archive_sha256 unique (archive_sha256)
);

create index if not exists idx_databento_import_batches_status_started
  on databento_import_batches (status, started_at);


create table if not exists databento_import_files (
  id bigserial primary key,
  batch_id bigint not null references databento_import_batches(id) on delete cascade,
  filename text not null,
  file_sha256 text not null,
  schema_name text not null,
  status text not null default 'pending',
  records_read bigint not null default 0,
  records_inserted bigint not null default 0,
  error_message text,
  completed_at timestamptz,
  constraint databento_import_files_status_check
    check (status in ('pending','running','completed','failed')),
  constraint databento_import_files_counts_nonnegative_check
    check (records_read >= 0 and records_inserted >= 0),
  constraint databento_import_files_sha256_length_check
    check (length(file_sha256) = 64),
  constraint uq_databento_import_files_batch_filename unique (batch_id, filename)
);

create index if not exists idx_databento_import_files_batch_status
  on databento_import_files (batch_id, status);


create table if not exists databento_instruments (
  dataset text not null,
  instrument_id bigint not null,
  raw_symbol text not null,
  root_symbol text not null,
  instrument_class text not null,
  security_type text not null,
  activation timestamptz,
  expiration timestamptz,
  min_price_increment_nano bigint,
  unit_of_measure_qty_nano bigint,
  definition_ts timestamptz not null,
  source_file_sha256 text not null,
  primary key (dataset, instrument_id),
  constraint databento_instruments_active_range_check
    check (expiration is null or activation is null or expiration >= activation),
  constraint databento_instruments_price_increment_positive_check
    check (min_price_increment_nano is null or min_price_increment_nano > 0),
  constraint databento_instruments_unit_qty_positive_check
    check (unit_of_measure_qty_nano is null or unit_of_measure_qty_nano > 0),
  constraint databento_instruments_source_sha256_length_check
    check (length(source_file_sha256) = 64)
);

create index if not exists idx_databento_instruments_root_expiration
  on databento_instruments (dataset, root_symbol, expiration, instrument_id);

create index if not exists idx_databento_instruments_raw_symbol
  on databento_instruments (dataset, raw_symbol);


create table if not exists databento_ohlcv_1m (
  dataset text not null,
  instrument_id bigint not null,
  ts_event timestamptz not null,
  trading_date date not null,
  open_nano bigint not null,
  high_nano bigint not null,
  low_nano bigint not null,
  close_nano bigint not null,
  volume bigint not null,
  source_file_sha256 text not null,
  primary key (dataset, instrument_id, ts_event),
  foreign key (dataset, instrument_id)
    references databento_instruments(dataset, instrument_id) on delete restrict,
  constraint databento_ohlcv_1m_price_envelope_check
    check (
      low_nano <= high_nano
      and open_nano between low_nano and high_nano
      and close_nano between low_nano and high_nano
    ),
  constraint databento_ohlcv_1m_volume_nonnegative_check check (volume >= 0),
  constraint databento_ohlcv_1m_source_sha256_length_check
    check (length(source_file_sha256) = 64)
);

create index if not exists idx_databento_ohlcv_1m_trading_date
  on databento_ohlcv_1m (dataset, trading_date, instrument_id, ts_event);


create table if not exists databento_roll_schedule (
  root_symbol text not null,
  trading_date date not null,
  dataset text not null,
  instrument_id bigint not null,
  raw_symbol text not null,
  decision_session_date date,
  from_instrument_id bigint,
  current_volume bigint,
  candidate_volume bigint,
  reason text not null,
  policy_version text not null,
  primary key (root_symbol, trading_date),
  foreign key (dataset, instrument_id)
    references databento_instruments(dataset, instrument_id) on delete restrict,
  foreign key (dataset, from_instrument_id)
    references databento_instruments(dataset, instrument_id) on delete restrict,
  constraint databento_roll_schedule_decision_before_trading_date_check
    check (decision_session_date is null or decision_session_date < trading_date),
  constraint databento_roll_schedule_current_volume_nonnegative_check
    check (current_volume is null or current_volume >= 0),
  constraint databento_roll_schedule_candidate_volume_nonnegative_check
    check (candidate_volume is null or candidate_volume >= 0)
);

create index if not exists idx_databento_roll_schedule_instrument_date
  on databento_roll_schedule (dataset, instrument_id, trading_date);

create index if not exists idx_databento_roll_schedule_policy_date
  on databento_roll_schedule (policy_version, trading_date);
