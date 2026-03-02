# Database setup

PowerShell commands:

Start Postgres:
  docker compose up -d

Apply schema:
  Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal

Apply migration updates (if schema already exists):
  Get-Content .\db\migrations\20260220_add_rule_break_fields.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260220_add_projectx_trade_events.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260221_add_projectx_trade_day_syncs.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260221_add_journal_entries.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260222_journal_entry_images_and_versioning.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260226_add_expenses.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260301_add_account_state_fields.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
  Get-Content .\db\migrations\20260302_add_instrument_metadata_and_position_lifecycles.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal

Dev note:
- Backend startup (`init_db`) now applies a Postgres compatibility patch for legacy `accounts` tables so
  `account_state`/`is_main` columns exist even before manual migration runs.

Seed data (optional):
  Get-Content .\db\seed.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
