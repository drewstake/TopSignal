# TopBot and trade-excursion release validation

The November 1 complete release includes the TopBot workspace redesign,
continuous MNQ history and chart corrections, contrarian practice strategy,
automatic practice recovery, persistent action audit, and local trade-excursion
recording. It consolidates the previously deployed TopBot overlays into Git.

Validation before packaging:

- 1,358 frontend tests passed. The bias-cache ref synchronization was then moved
  into an effect; its focused test and lint for all changed frontend areas passed.
- 460 relevant backend tests passed, covering strategy, worker recovery, audit,
  execution safeguards, history acquisition, excursions, streaming and shutdown.
  One pre-existing mathematical contract-reason test was deselected; its failure
  also reproduces on the preceding Git revision.
- All 44 startup/deployment checks passed, as did TypeScript/Vite compilation.
- The broad backend run was not clean: initially 2,788 passed, 19 failed,
  19 errored and 9 skipped. All 26 tests in the two path-affected modules passed
  with a shorter Windows temporary path. The new timestamp-adapter compatibility
  issue and retired-strategy explanation assertion were fixed and retested.
- Remaining failures concern retired mathematical/paper strategy admission and
  research fixtures that assume its former preset. Four failures (the contract
  explanation and three FX replay parity cases) reproduced on the preceding Git
  revision. A queue timing failure did not reproduce in the focused run.
- Repository-wide lint still reports existing order-book-history errors and
  research-hook warnings outside this change. Changed frontend areas pass lint.

Production uses the existing worker account, environment and persistent cache.
No migration, bulk cloud write, historical import, account rearming, or broker
test order is part of this release. The source archive and frontend assets are
hashed against the committed revision before activation. Activation retains the
prior release for rollback and checks API, frontend, worker and readiness health.
Private manifests and activation evidence remain outside Git in the deployment
staging directory.

## Follow-up: remaining test failures resolved

The complete backend suite now passes with its normal command from `backend`:
`python -m pytest -q` reports **2,827 passed, 9 skipped, 8 subtests passed**.
Eight skips require a disposable PostgreSQL database; one requires an optional
workspace capture. No failing tests were skipped or marked expected failures.
The frontend suite reports **1,359 passed**; repository-wide frontend lint and
the TypeScript/Vite production build also pass.

Corrections:

- Restrict pytest discovery to source tests, and use short, unique temporary
  directories on Windows so hashed model paths stay within the path limit.
- Assert that retired mathematical proposals block the run and never become
  paper fills. Seed historical paper records directly when testing delivery-roll
  handling for positions that existed before retirement.
- Keep the registered opening-drive replay's original settings independent of
  the current practice preset, with a regression check for preset changes.
- Use a closed candle during an eligible session in the contract-isolation test.
  Treat out-of-policy stops as rejected Python forecasts and HOLD in FX replay,
  while retaining numerical parity checks for supported stops.
- Isolate the recorder's shutdown flag in the queue-capacity test.
- Reset history loading state in request handlers and correct research/replay
  effect dependencies. A refresh regression verifies stale results disappear
  while the replacement request is pending.

These fixes do not change TopBot's practice entry or risk-management rules.
