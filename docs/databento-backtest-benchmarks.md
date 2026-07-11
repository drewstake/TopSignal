# Databento Local Backtest Benchmarks

Measured on 2026-07-11 on Windows with the pre-existing local Python 3.10.11
virtual environment. TopSignal's supported development and CI runtime remains
Python 3.11 or newer; the version here records the original measurement
environment and is not a support declaration. Re-run the commands below with a
supported Python 3.11+ environment for release comparisons. The source
fingerprint was
`8126869196675afdf8e3b0b80817b167dccd4041157897b8935bb6055e29ed92`.

## Cache build

- Inputs: 10 Databento ZIPs, 272,794 definitions, 23,285,196 OHLCV-1m records,
  and 7,580,065 statistics records.
- Outputs: partitioned Parquet plus NumPy mmap arrays for 1m, 5m, 15m, 1h,
  4h, and 1d across MNQ, MES, NQ, and ES.
- V3 cold build: 520.92 seconds.
- Published V3 artifact size: 1.70 GiB.
- Unchanged fingerprint plus full artifact-integrity check/reuse: 1.99 seconds.

## Cache plus direct replay

The benchmark now defaults to `--input-mode lazy`: `open_candles` returns an
O(1), binary-sliced `MmapCandleSequence`, and candle proxies are created only as
the replay consumes them. `--input-mode eager` remains available to materialize
a `CachedCandleList` for comparison; `--max-rows` applies only to that eager
path. JSON and text reports identify `input_mode`, `source_method`, `input_type`,
`lazy_mmap`, and the separate `prepare_input` and `run_backtest` phases.

"Cold" means a new `DatabentoReplayStore` with no process-local mapped series
or materialized candle slice. It does not evict the operating-system file cache.
"Warm direct" reuses the store and exact source slice but intentionally reruns
the engine so the replay cost remains visible.

Current lazy-mmap measurements after the profile-guided replay changes:

| Case | Bars | Cold open | Cold open + replay | Warm open | Warm direct replay | Semantic SHA-256 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| MNQ 5m, 30 days | 5,976 input / 5,955 execution | 0.0138 s | 0.3658 s | 0.0021 s median | 0.3626 s median | `fa9e9b1bd29a894c76c710f27eedd2b9d620c223cdd30cd7c9009b5f9291f2c0` |
| MNQ 5m, full history | 507,247 input / 507,226 execution | 0.0165 s | 30.1163 s | 0.0021 s median | 30.1562 s median | `64c4d166015957cd9e2cb124c9b39630e38d9b33e01b9ddfbfdcb0bd59ebf91e` |

Warm direct samples intentionally include a complete new replay. They measure
the engine, not the application result LRU.

The following table is the checked eager-materialization baseline captured
before lazy mmap became the benchmark default. Keep it as a regression and
materialization-cost reference; it is not a claim about current lazy-open time.

| Case | Bars | Cold load + replay | Warm direct replay | Semantic SHA-256 |
| --- | ---: | ---: | ---: | --- |
| MNQ 5m, 30 days | 5,976 input / 5,955 execution | 0.159 s | 0.102 s median | `fa9e9b1bd29a894c76c710f27eedd2b9d620c223cdd30cd7c9009b5f9291f2c0` |
| ES 1m, 7 days | 6,890 input / 6,869 execution | 0.268 s | 0.134 s median | `de41968b8c83579b3061f213afd9d3816b8c0db641ee3681746b5ce953124bea` |
| MNQ 5m, 1 year | 70,836 input / 70,815 execution | 2.066 s | 1.140 s median | `6189cdc58c90f306e9d52bdad0d05e8397c6beaabd936e32268013d2063c0642` |
| MNQ 5m, full history | 507,247 input / 507,226 execution | 12.233 s | 8.076 s median | `64c4d166015957cd9e2cb124c9b39630e38d9b33e01b9ddfbfdcb0bd59ebf91e` |

The historical warm materialized-slice LRU lookup was about 2 milliseconds in
these cases. The benchmark tool verifies the complete semantic result fields
across every cold and warm sample.

V3 exposes MNQ's valid 22:00 five-minute bucket even though its first observed
one-minute bar is at 22:03. That no longer drops the first partial source bucket,
so the full-history count and digest intentionally differ from the superseded V2
capture.

## Application create-and-persist path

`--sqlite-persistence` calls `create_bot_backtest`, flushes a new
`bot_backtests` row, and commits it to in-memory SQLite. Its cold sample is
instrumented and fails unless the application calls `open_candles` at least
once and never calls `load_candles`. Exact warm Runs still open the O(1) view to
derive the source/slice fingerprint, then return from the deterministic result
LRU without replaying the engine. Per-sample JSON records both method counts
under `source_calls`. SQLite isolates application CPU and
serialization; a cloud Supabase commit adds environment-specific network
latency. The timer excludes SQLite schema/config setup and semantic-digest
serialization.

The current lazy application measurements are:

| Case | First Run | Exact warm Run | Speedup | Result |
| --- | ---: | ---: | ---: | --- |
| MNQ 5m, 30 days | 0.4548 s | 0.0582 s median | 7.8x | Identical snapshots; four rows persisted |
| MNQ 5m, full history (507,226 execution bars, 26,994 trades) | 31.4522 s | 0.7800 s median | 40.3x | Identical snapshots; three rows persisted |

For reference, the application table below is the pre-lazy capture.

| Case | First Run | Exact warm Run | Result |
| --- | ---: | ---: | --- |
| MNQ 5m, 30 days | 0.267 s | 0.063 s median | Identical snapshots; six rows persisted |
| MNQ 5m, full history (507,226 execution bars, 26,994 trades) | 13.410 s | 1.155 s median | Identical snapshots; three rows persisted |

The benchmark fails if a cold persistence sample hits the result LRU, a warm
sample misses it, an input fingerprint changes, or any semantic digest differs.
JSON output records these checks under `sqlite_persistence`, including every
sample's `result_cache_hit`, SQL statement count, persisted row ID, cache stats,
and semantic SHA-256.

## Profile-guided changes

The initial profile showed repeated per-row validation, eager candle expansion,
and Decimal PnL math as avoidable replay costs. Builder-verified immutable
slices now use an O(1) context check; production opens lazy mmap sequences; and
tick-aligned replay PnL uses the engine's normalized float path. The application
result LRU is bounded separately and is keyed by engine version, full strategy
snapshot, exact source/slice fingerprint, replay window, and cost assumptions.

The reported 2,531,558-bar failure was reproduced with bot config 32. Its old
aggregate allowance had only 637,614 rows left after eager primary and
higher-timeframe loads. The lazy path now estimates 277,365,672 bytes for that
full replay, and measured full-cache initialization peaks near 201 MiB. Full
initialization is 5.152 seconds, down from 17.462 seconds after vectorizing
synchronized-stream counts.

The same exact 20-source TopBot configuration was profiled and optimized:

| TopBot case | Execution / in-session bars | Replay | Peak working set | Trades | Semantic SHA-256 |
| --- | ---: | ---: | ---: | ---: | --- |
| One session, 2026-07-10 | 82 / 76 | 1.845 s | 199.32 MiB | 2 | `29c5be6251a6af734ad6680db610d7f51eb03656d0ea2bedc9ba59d2db5c3e35` |
| Final 30 calendar days | 5,976 / 1,604 | 41.214 s | 211.70 MiB | 59 | `cf948183f9823c62424af6fc88acb91ed274c6082c162b3ef6e404f33ce3459c` |

The one-session replay was 7.182 seconds before optimization. Array-native FVG
detection, an actionable-window guard for VWAP-gap retrace, and a bounded shared
lazy-proxy/property cache reduced it by 74%. The 30-day measurement projects a
full 2019-2026 20-source TopBot cold run at about 59.4 minutes; this is an
explicit extrapolation, not a claimed full-run measurement. Exact repeated Run
requests use the result LRU and do not replay those source strategies.

The one-session TopBot application path took 1.972 seconds for a cold
`create_bot_backtest` plus in-memory SQLite commit and 0.029 seconds for an
identical warm Run, a 67.2x speedup. The warm progress event reported
`cache_hit=true`; both requests opened seven lazy streams, called
`load_candles` zero times, and persisted identical 82-bar/two-trade snapshots.

## Exact reproduction commands

All benchmark cases use a 50,000 starting balance, 1.25 commission per contract,
one tick of slippage, a 200-bar lookback, and SMA periods 9/21. These commands
capture the current default lazy direct path:

```powershell
# MNQ 5m, trailing 30 days
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 30 `
  --cold-repeats 1 --warm-repeats 3 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 --json

# ES 1m, trailing 7 days
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root ES --timeframe 1m --days 7 `
  --cold-repeats 1 --warm-repeats 3 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 --json

# MNQ 5m, trailing 365 days
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 365 `
  --cold-repeats 1 --warm-repeats 3 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 --json

# MNQ 5m, complete supplied history. Lazy mode has no materialized-row ceiling.
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m `
  --start 2019-05-05T22:00:00Z --end 2026-07-10T20:20:00Z `
  --cold-repeats 1 --warm-repeats 1 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 --json

# Explicit eager comparison. This is where --max-rows applies.
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 30 `
  --input-mode eager --max-rows 500000 `
  --cold-repeats 1 --warm-repeats 3 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 --json
```

Add the checked SQLite persistence path with these exact invocations. Five warm
runs plus one cold run produce six persisted 30-day rows; two warm runs plus one
cold run produce three full-history rows:

```powershell
backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m --days 30 `
  --cold-repeats 1 --warm-repeats 5 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 `
  --sqlite-persistence --json

backend\.venv\Scripts\python backend\tools\benchmark_databento_cache.py `
  --root MNQ --timeframe 5m `
  --start 2019-05-05T22:00:00Z --end 2026-07-10T20:20:00Z `
  --cold-repeats 1 --warm-repeats 2 `
  --starting-balance 50000 --commission 1.25 --slippage-ticks 1 `
  --lookback-bars 200 --fast-period 9 --slow-period 21 `
  --sqlite-persistence --json
```

`--profile PATH` writes one additional warm direct input-preparation-and-replay
cProfile sample. It does not profile the SQLite persistence samples.
