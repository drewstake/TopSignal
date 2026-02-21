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

Seed data (optional):
  Get-Content .\db\seed.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
