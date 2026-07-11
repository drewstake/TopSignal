-- Seed standard E-mini instrument economics without overwriting local overrides.

insert into instrument_metadata (symbol, tick_size, tick_value)
values
  ('NQ', 0.25, 5.00),
  ('ES', 0.25, 12.50)
on conflict (symbol) do nothing;
