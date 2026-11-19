# Database Setup And Migrations

Read the [Supabase storage policy](../docs/supabase-storage-policy.md) before
bulk data changes. Databento history belongs in the local replay cache, even
when legacy table definitions exist. Use [the storage report](diagnostics/storage_usage.sql)
to check capacity; do not refill historical candle tables through migrations.

See the [data model](data-model.md) for table responsibilities and trade ingestion. For an isolated development database, configure the [development profile](../README.md#development-profiles) before running the commands below from the repository root.

The [schema](schema.sql) describes a fresh PostgreSQL database. The
[migrations](migrations) upgrade older databases through the
[checksummed runner](../backend/tools/migrate_db.py).

The runner records applied filenames and SHA-256 checksums in
`topsignal_schema_migrations`, serializes upgrades with a PostgreSQL advisory
lock, and applies each migration in its own transaction.

## Local Postgres Container

Start the bundled database:

```powershell
docker compose up -d db
```

The container from `docker-compose.yml` is:

- container name: `topsignal_db`
- database: `topsignal`
- user: `topsignal`
- password: `topsignal_password`

## Fresh Database

Apply the full schema:

```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -v ON_ERROR_STOP=1 -U topsignal -d topsignal
```

Then validate the current schema, initialize its migration ledger without
replaying historical upgrades, and verify it. Baseline mode is intentionally
limited to a brand-new database whose application tables are still empty:

```powershell
npm run db:baseline
npm run db:check
```

## Existing Database

If the database already exists, use the migration runner rather than piping SQL
files manually:

```powershell
npm run db:migrate
npm run db:check
```

Do not baseline a populated pre-runner database and do not replay the entire
history over a schema that is already current. Use this one-time, backup-first
adoption sequence during a maintenance window with application writers stopped:

```powershell
# 1. Create and verify a database backup outside TopSignal.
# 2. Bring legacy compatibility-managed schema objects to this release.
npm run db:init
# 3. Validate every ORM table/column, required index, critical constraint,
#    nullable audit FK, and existing bot quantities before recording history.
npm run db:adopt-current
npm run db:check
```

`db:adopt-current` never replays historical data migrations. It requires an
explicit populated-database acknowledgement, refuses a partial ledger, and
records history only after current-schema validation succeeds. Restore the
backup and investigate rather than bypassing a failed validation.

The quantity-safety migration deliberately fails if a legacy bot configuration
contains fractional, non-finite, non-positive, or greater-than-10,000 contract
quantities. Review and correct those rows before retrying; the migration will
not preserve an unvalidated live-order safety state.

The [migration directory](migrations) lists the ordered SQL files. The
[runner](../backend/tools/migrate_db.py) discovers them by filename and verifies
checksums against the ledger. It records the retired Databento market-table
migration as a no-op on databases where it is still pending.

## Compatibility Patches On Startup

The backend also applies some safe Postgres compatibility patches in `backend/app/db.py` during startup.

Those patches currently help older dev databases by:

- adding missing `accounts` columns such as `display_name`, `account_state`, `trade_data_source`, and `is_main`
- backfilling journal versioning and image support
- ensuring multi-tenant `user_id` columns and related indexes
- creating `provider_credentials` when absent
- seeding default `instrument_metadata`

Treat those patches as a temporary safety net, not as the primary
schema-upgrade path. Production deployments should run migrations before
starting application instances and use `TOPSIGNAL_DB_SCHEMA_INIT=skip`.

For faster local dev startup, the root `npm run dev` backend wrapper sets
`TOPSIGNAL_DB_SCHEMA_INIT=skip` unless you override it. Apply and verify
migrations explicitly when schema-related code changes:

```powershell
npm run db:migrate
npm run db:check
```

## Historical Supabase Data API protections

The browser authenticates locally and reads application data through FastAPI.
The historical Supabase project retains cloud audit records and execution fences.
See the [retirement and recovery procedure](../docs/supabase-retirement.md).

`20260830_harden_supabase_data_api.sql` provides defense in depth for projects
that keep the Data API enabled: it revokes `anon` and `authenticated` access to
existing tables, sequences, and functions in `public`, and removes the legacy
automatic default grants for future objects. It also removes equivalent
`PUBLIC` grants, which browser roles inherit. It preserves the historical `auth` and `storage` schemas.
PostgreSQL's built-in `PUBLIC` execute default for new functions is global, so
the migration also removes that default for functions created later by the
migration/object-owner roles. Existing functions outside `public` are unchanged;
future non-public functions must receive their intended execute grants explicitly.

After applying migrations, verify the live project has no application-table
grants for browser roles:

```sql
select grantee, table_name, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and grantee in ('anon', 'authenticated')
order by grantee, table_name, privilege_type;
```

The query must return no rows. Also keep the Data API disabled unless a future
feature deliberately exposes a narrowly scoped table with tested RLS policies.

## Verifying The Schema

Connect with `psql`:

```powershell
docker exec -it topsignal_db psql -U topsignal -d topsignal
```

Useful checks:

```sql
\dt
select count(*) from accounts;
select count(*) from projectx_trade_events;
select count(*) from journal_entries;
select count(*) from expenses;
select count(*) from payouts;
```

Only upgraded installations that already retain the optional legacy
`databento_*` compatibility tables can query those tables directly.

Recent trade-event sample:

```sql
select id, account_id, symbol, side, size, price, pnl, fees, trade_timestamp
from projectx_trade_events
order by trade_timestamp desc
limit 20;
```

## Notes

- This repo does not include `db/seed.sql`
- Databento raw bars, rolls, and mmap timeframes are retained local cache artifacts. Fresh schemas omit the retired relational market tables, while upgraded installations retain any existing `databento_*` tables untouched and production replay never queries them
- ProjectX remains the account, execution, position, analytics, and order-routing source
- The legacy `trades` table still exists for old `/metrics/*` routes
