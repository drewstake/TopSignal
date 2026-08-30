-- TopSignal reaches application tables through its FastAPI server and a direct
-- PostgreSQL connection. The browser-side Supabase client is used for Auth
-- only, so anon/authenticated must not be able to bypass FastAPI through the
-- automatically generated Data API.
--
-- Keep this migration portable to ordinary PostgreSQL installations, where
-- Supabase's API roles do not exist. Supabase Auth and Storage use their own
-- schemas and are intentionally not modified here.

-- Advance the readiness baseline when this migration upgrades an existing
-- deployment.  Keep this self-contained for older installations that predate
-- the baseline marker table.
create table if not exists topsignal_schema_baselines (
  version text primary key,
  created_at timestamptz not null default now()
);

do $topsignal_data_api_hardening$
declare
  api_role text;
begin
  foreach api_role in array array['anon', 'authenticated']
  loop
    if exists (select 1 from pg_roles where rolname = api_role) then
      execute format(
        'revoke all privileges on all tables in schema public from %I',
        api_role
      );
      execute format(
        'revoke all privileges on all sequences in schema public from %I',
        api_role
      );
      execute format(
        'revoke execute on all functions in schema public from %I',
        api_role
      );

      -- Supabase's legacy project defaults automatically grant new public
      -- objects to Data API roles. Remove those grants for future migrations.
      execute format(
        'alter default privileges revoke all privileges on tables from %I',
        api_role
      );
      execute format(
        'alter default privileges in schema public '
        'revoke all privileges on tables from %I',
        api_role
      );
      execute format(
        'alter default privileges revoke all privileges on sequences from %I',
        api_role
      );
      execute format(
        'alter default privileges in schema public '
        'revoke all privileges on sequences from %I',
        api_role
      );
      execute format(
        'alter default privileges revoke execute on functions from %I',
        api_role
      );
      execute format(
        'alter default privileges in schema public '
        'revoke execute on functions from %I',
        api_role
      );

      if exists (select 1 from pg_roles where rolname = 'postgres') then
        if pg_has_role(current_user, 'postgres', 'member') then
          execute format(
            'alter default privileges for role postgres '
            'revoke all privileges on tables from %I',
            api_role
          );
          execute format(
            'alter default privileges for role postgres in schema public '
            'revoke all privileges on tables from %I',
            api_role
          );
          execute format(
            'alter default privileges for role postgres '
            'revoke all privileges on sequences from %I',
            api_role
          );
          execute format(
            'alter default privileges for role postgres in schema public '
            'revoke all privileges on sequences from %I',
            api_role
          );
          execute format(
            'alter default privileges for role postgres '
            'revoke execute on functions from %I',
            api_role
          );
          execute format(
            'alter default privileges for role postgres in schema public '
            'revoke execute on functions from %I',
            api_role
          );
        end if;
      end if;
    end if;
  end loop;

  -- PostgreSQL grants function execution to PUBLIC by default. TopSignal has
  -- no browser-callable public functions, so remove both current and future
  -- execution paths. This does not touch auth.*, storage.*, or extensions.*.
  if exists (
    select 1 from pg_roles where rolname in ('anon', 'authenticated')
  ) then
    -- Browser roles inherit privileges granted to PUBLIC, so direct-role
    -- revocation alone is insufficient.
    revoke all privileges on all tables in schema public from public;
    revoke all privileges on all sequences in schema public from public;
    revoke execute on all functions in schema public from public;

    alter default privileges
      revoke all privileges on tables from public;
    alter default privileges in schema public
      revoke all privileges on tables from public;
    alter default privileges
      revoke all privileges on sequences from public;
    alter default privileges in schema public
      revoke all privileges on sequences from public;

    -- PostgreSQL's built-in function EXECUTE grant to PUBLIC is a global
    -- default. A schema-scoped REVOKE cannot override it, so this statement
    -- intentionally omits `in schema public`. It changes only future
    -- functions owned by the migration role; existing auth/storage functions
    -- and their grants are untouched.
    alter default privileges
      revoke execute on functions from public;
    -- Also remove any Supabase/project-specific per-schema PUBLIC default,
    -- which is additive to PostgreSQL's global default.
    alter default privileges in schema public
      revoke execute on functions from public;

    -- A migration role may be a member of Supabase's object-owner role rather
    -- than running as that role, so explicitly harden postgres's PUBLIC
    -- function defaults as well as the current role's defaults above.
    if exists (select 1 from pg_roles where rolname = 'postgres') then
      if pg_has_role(current_user, 'postgres', 'member') then
        alter default privileges for role postgres
          revoke all privileges on tables from public;
        alter default privileges for role postgres in schema public
          revoke all privileges on tables from public;
        alter default privileges for role postgres
          revoke all privileges on sequences from public;
        alter default privileges for role postgres in schema public
          revoke all privileges on sequences from public;
        alter default privileges for role postgres
          revoke execute on functions from public;
        alter default privileges for role postgres in schema public
          revoke execute on functions from public;
      end if;
    end if;
  end if;

  -- Keep the marker inside this block and as its final statement. If any
  -- hardening statement fails, the DO statement is atomic and cannot leave a
  -- readiness marker even when a default psql client continues afterward.
  insert into topsignal_schema_baselines (version)
  values ('schema-20260830-v6')
  on conflict (version) do nothing;
end
$topsignal_data_api_hardening$;
