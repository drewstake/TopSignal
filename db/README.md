# Database setup

Start Postgres:
  docker compose up -d

Apply schema:
  docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\schema.sql

Apply migration updates (if schema already exists):
  docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\migrations\20260220_add_rule_break_fields.sql

Seed data (optional):
  docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\seed.sql
