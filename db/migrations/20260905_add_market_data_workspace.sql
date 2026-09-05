-- Market data context, timestamped events, observations and decision research.
-- Existing execution and account records are not modified.

CREATE TABLE IF NOT EXISTS market_event_versions (
	id TEXT NOT NULL, 
	user_id UUID NOT NULL, 
	source TEXT NOT NULL, 
	source_event_id TEXT NOT NULL, 
	content_hash TEXT NOT NULL, 
	kind TEXT NOT NULL, 
	title TEXT NOT NULL, 
	country TEXT NOT NULL, 
	importance TEXT NOT NULL, 
	scheduled_at TIMESTAMP WITH TIME ZONE, 
	scheduled_end_at TIMESTAMP WITH TIME ZONE, 
	scheduled_date TEXT, 
	time_precision TEXT NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	available_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	state TEXT NOT NULL, 
	actual TEXT, 
	forecast TEXT, 
	previous TEXT, 
	revised TEXT, 
	url TEXT, 
	raw_fields JSONB NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_market_event_observation UNIQUE (user_id, source, source_event_id, observed_at)
);

CREATE INDEX IF NOT EXISTS ix_market_event_asof ON market_event_versions (user_id, source, available_at);

CREATE INDEX IF NOT EXISTS ix_market_event_identity ON market_event_versions (user_id, source, source_event_id, observed_at);

CREATE TABLE IF NOT EXISTS market_event_source_snapshots (
	id TEXT NOT NULL, 
	user_id UUID NOT NULL, 
	source TEXT NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status TEXT NOT NULL, 
	error_code TEXT, 
	event_count INTEGER NOT NULL, 
	coverage_start TIMESTAMP WITH TIME ZONE, 
	coverage_end TIMESTAMP WITH TIME ZONE, 
	coverage_complete BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_market_event_source_asof ON market_event_source_snapshots (user_id, source, observed_at);

CREATE TABLE IF NOT EXISTS market_observations (
	id BIGSERIAL NOT NULL, 
	user_id UUID NOT NULL, 
	contract_id TEXT NOT NULL, 
	source TEXT NOT NULL, 
	event_type TEXT NOT NULL, 
	provider_timestamp TIMESTAMP WITH TIME ZONE, 
	received_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	fingerprint TEXT NOT NULL, 
	price NUMERIC(24, 8), 
	size NUMERIC(24, 8), 
	side TEXT, 
	bid NUMERIC(24, 8), 
	ask NUMERIC(24, 8), 
	details JSONB NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_market_observation_fingerprint UNIQUE (user_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS ix_market_observation_contract_time ON market_observations (user_id, contract_id, received_at);

CREATE INDEX IF NOT EXISTS ix_market_observation_owner_time ON market_observations (user_id, received_at);

CREATE TABLE IF NOT EXISTS decision_research_snapshots (
	id BIGSERIAL NOT NULL, 
	user_id UUID NOT NULL, 
	decision_id BIGINT NOT NULL, 
	account_id BIGINT NOT NULL, 
	contract_id TEXT NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	candle_source TEXT NOT NULL, 
	candle_live BOOLEAN NOT NULL, 
	signal_timestamp TIMESTAMP WITH TIME ZONE, 
	action TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	direction TEXT, 
	entry_price NUMERIC(24, 8), 
	stop_loss NUMERIC(24, 8), 
	take_profit NUMERIC(24, 8), 
	score INTEGER, 
	snapshot_version TEXT NOT NULL, 
	snapshot_hash TEXT NOT NULL, 
	snapshot JSONB NOT NULL, 
	outcome TEXT NOT NULL, 
	outcome_at TIMESTAMP WITH TIME ZONE, 
	outcome_details JSONB, 
	routing JSONB, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_decision_research_owner_decision UNIQUE (user_id, decision_id)
);

CREATE INDEX IF NOT EXISTS ix_decision_research_owner_account ON decision_research_snapshots (user_id, account_id, observed_at);

CREATE INDEX IF NOT EXISTS ix_decision_research_pending ON decision_research_snapshots (user_id, outcome, observed_at);

DO $market_data_acl$
DECLARE
  table_name text;
  api_role text;
  serial_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['market_event_versions','market_event_source_snapshots','market_observations','decision_research_snapshots'] LOOP
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %I FROM PUBLIC', table_name);
    serial_name := pg_get_serial_sequence(table_name, 'id');
    IF serial_name IS NOT NULL THEN
      EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC', serial_name);
    END IF;
    FOREACH api_role IN ARRAY ARRAY['anon','authenticated'] LOOP
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %I FROM %I', table_name, api_role);
        IF serial_name IS NOT NULL THEN
          EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM %I', serial_name, api_role);
        END IF;
      END IF;
    END LOOP;
  END LOOP;
  INSERT INTO topsignal_schema_baselines (version) VALUES ('schema-20260905-v7') ON CONFLICT (version) DO NOTHING;
END
$market_data_acl$;
