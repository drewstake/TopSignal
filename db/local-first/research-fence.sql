-- Explicit migration step, after stopping application writers. Never run as
-- ordinary startup DDL. This preserves every cloud row and prevents old clients
-- from silently continuing a second research cache after the filesystem switch.
BEGIN;
CREATE SCHEMA topsignal_research;
REVOKE ALL ON SCHEMA topsignal_research FROM PUBLIC;
CREATE FUNCTION topsignal_research.require_local_storage() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'Research writes moved to the local filesystem';
END $$;
REVOKE ALL ON FUNCTION topsignal_research.require_local_storage() FROM PUBLIC;
CREATE TRIGGER topsignal_research_fence BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
ON public.market_observations FOR EACH STATEMENT
EXECUTE FUNCTION topsignal_research.require_local_storage();
CREATE TRIGGER topsignal_research_fence BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
ON public.projectx_market_candles FOR EACH STATEMENT
EXECUTE FUNCTION topsignal_research.require_local_storage();
CREATE TRIGGER topsignal_research_fence BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
ON public.decision_research_snapshots FOR EACH STATEMENT
EXECUTE FUNCTION topsignal_research.require_local_storage();
COMMIT;
