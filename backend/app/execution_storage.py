"""Fail-closed identity checks for the single local execution authority."""
import os
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url


def validate_authority(engine):
    mode = os.getenv('TOPSIGNAL_EXECUTION_STORAGE', 'application').strip().lower()
    if mode not in {'application', 'local_postgres'}:
        raise RuntimeError('Invalid execution storage mode')
    if mode == 'application':
        return
    url = make_url(engine.url)
    if url.get_backend_name() != 'postgresql' or url.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('Local execution requires loopback PostgreSQL')
    from .research_storage import enabled
    if not enabled():
        raise RuntimeError('Local execution requires verified local research storage')
    try:
        expected = UUID(os.environ['TOPSIGNAL_LOCAL_AUTHORITY_ID'])
    except (KeyError, ValueError):
        raise RuntimeError('Local execution authority identifier required') from None
    with engine.connect() as connection:
        row = connection.execute(text('SELECT source_id,active FROM topsignal_local.authority WHERE singleton')).first()
        if row is None or UUID(str(row[0])) != expected or row[1] is not True:
            raise RuntimeError('Local execution authority is absent, inactive or mismatched')
        retirement_table = connection.execute(text("SELECT to_regclass('topsignal_local.mirror_retirement')")).scalar()
        mirror_retired = bool(retirement_table and connection.execute(text(
            'SELECT 1 FROM topsignal_local.mirror_retirement WHERE singleton AND authority_id=:authority'
        ), {'authority': str(expected)}).first())
        missing = connection.execute(text('''
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r'
            AND c.relname NOT LIKE 'databento\\_%' ESCAPE '\\'
            AND c.relname NOT IN ('market_observations','projectx_market_candles',
                'decision_research_snapshots','topsignal_schema_baselines','topsignal_schema_migrations')
            AND NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid
                AND t.tgenabled='O' AND t.tgname=CASE WHEN c.relname='bot_runtime_leases'
                    THEN 'topsignal_local_authority' ELSE 'topsignal_local_serialize' END)
        ''')).first()
        if missing:
            raise RuntimeError('Execution schema has an unfenced table')
        # Before retirement, an absent capture trigger is still a failure. After
        # retirement every capture trigger must be disabled so the outbox cannot grow.
        capture_errors = connection.execute(text('''
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r'
            AND EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname='topsignal_local_serialize')
            AND NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid
                AND t.tgname='topsignal_local_capture' AND t.tgenabled=:expected)
        '''), {'expected': 'D' if mirror_retired else 'O'}).first()
        if capture_errors:
            raise RuntimeError('Execution schema mirror state is inconsistent')
