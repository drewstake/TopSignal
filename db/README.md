# Database setup

Start Postgres:
  docker compose up -d

Apply schema:
  docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\schema.sql

Seed data (optional):
  docker exec -i topsignal_db psql -U topsignal -d topsignal < .\db\seed.sql