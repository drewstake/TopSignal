# Databento full-history backtest boundary

This note documents the current historical replay contract.

- Databento is the only production historical market-data source used by
  `POST /api/bots/{id}/backtests`.
- ProjectX clients are not created by a Databento-backed replay. ProjectX remains
  responsible for accounts, executions, positions, trade/journal synchronization,
  and order routing.
- Imported DBN/zstd archives are verified against their manifests and recorded in
  an idempotent batch/file ledger.
- Raw prices remain fixed-point nanounits in `databento_ohlcv_1m`; conversion occurs
  only when lightweight replay candles are projected.
- `databento_roll_schedule` selects one outright delivery per CME trading session.
  Session D uses volume from the last completed session only, ties keep the current
  contract, and the schedule never rolls backward.
- Intraday resampling is anchored to the 18:00 America/New_York futures-session
  boundary. Buckets never combine deliveries and incomplete trailing buckets are
  excluded.
- MNQ coverage begins with the observed launch session on 2019-05-05 at 22:00 UTC.
  The earlier batch-query start is metadata, not permission to synthesize history.
- Replay queries stream projected columns in chunks. Evaluator history is bounded,
  input fingerprints include Databento delivery/provenance fields, and large
  equity/drawdown series are deterministically sampled while exact metrics continue
  to observe every bar.

The legacy ProjectX-cache orchestration remains callable only behind a process-local,
false-by-default switch for its historical SQLite unit fixtures. It is unavailable to
the application runtime.
