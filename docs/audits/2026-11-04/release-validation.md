# Complete release validation — November 4, 2026

This release includes the live liquidity heatmap, provider-reported trade
bubbles, session order-flow summaries, side-comparison audit tools and evidence,
and removal of the daily +$200-to-$0 break-even stop. The separate +$400 daily
profit target remains active. Saved break-even latches and pending break-even
exit reasons are retired without deleting historical audit events.

Validation before deployment:

- Offline backend suite: 3,120 passed, 20 skipped, eight subtests passed. The
  skips require disposable PostgreSQL or the optional workspace capture.
- Frontend suite: 1,451 passed across 175 files with four workers. The initial
  run exposed six BotPage test failures because the new child panels introduced
  additional status elements. Mocking those children consistently with the other
  dashboard panels restored test isolation; the full suite then passed.
- Frontend TypeScript/Vite build and ESLint passed.
- All ten Windows deployment regressions passed.

Session summaries use bounded SQLite under TOPSIGNAL_DATABENTO_CACHE_DIR, with
a 64 MiB per-owner cap and a 1 GiB free-space reserve. Production has about
1.19 TB free on its data disk. No database migration, historical cloud import,
retention deletion or billing change is required. The display trade stream does
not persist market prints. Provider side labels remain distinct from verified
exchange aggressor provenance.

Deployment receipts and private configuration remain in ignored local storage.
