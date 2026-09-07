-- Read-only storage check. Run in the Supabase SQL editor before bulk writes
-- and during weekly maintenance. Thresholds are TopSignal's internal budget,
-- not a replacement for the organization's current Supabase quota metrics.
BEGIN READ ONLY;

SELECT datname AS database_name,
       pg_database_size(oid) AS bytes,
       pg_size_pretty(pg_database_size(oid)) AS size
FROM pg_database
ORDER BY pg_database_size(oid) DESC;

SELECT sum(pg_database_size(oid)) AS total_database_bytes,
       CASE
         WHEN sum(pg_database_size(oid)) >= 400000000 THEN 'STOP bulk writes: review storage'
         WHEN sum(pg_database_size(oid)) >= 350000000 THEN 'WARNING: plan archival or capacity'
         ELSE 'Within internal storage budget'
       END AS storage_budget_status
FROM pg_database;

SELECT schemaname, relname AS table_name,
       pg_total_relation_size(relid) AS total_bytes,
       pg_size_pretty(pg_table_size(relid)) AS table_size,
       pg_size_pretty(pg_indexes_size(relid)) AS index_size,
       n_live_tup AS estimated_rows, n_dead_tup AS estimated_dead_rows
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 20;

-- Legacy Databento tables must not grow in cloud deployments. Row estimates
-- can be stale; use a targeted exact count when investigating an anomaly.
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
WHERE schemaname = 'public' AND relname LIKE 'databento\_%' ESCAPE '\'
ORDER BY pg_total_relation_size(relid) DESC;

COMMIT;
