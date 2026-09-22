# TopBot Mathematical — selected candle model

The September 22 strategy change selects `bayesian_cells_v1`, revision
`mnq_bayesian_payoff_v1`, for **new TopBot Dry Run starts**. Its forecast now
supplies the actual BUY / SELL / HOLD signal passed to the existing router.
The API uses HOLD for the model's NO TRADE choice. This is a strategy-selection
change, not evidence of profitability or completed live validation.

`backend/app/services/topbot_mathematical.py` adapts the existing researched
model; it does not substitute a new indicator rule or fit parameters during an
evaluation. Model inputs are closed five-minute MNQ candles. It compares expected
net payoffs after costs and a day-cluster/HAC uncertainty penalty. An action needs
at least 300 training paths, 20 effective days and lower utility above $1. Risk
distances remain volatility-derived, 4–25 points, with a 1.5R target and 15-minute
scenario horizon. Missing, stale, mismatched or unsupported model/data inputs
produce NO TRADE, with the reason shown in the decision explanation.

The separate all-sessions task has extended the runtime entry schedule. Its
changes are preserved. The original regular-session research protocol does not
establish validity in overnight sessions; forward evidence across those sessions
is still needed. Mathematical-model forecasts remain explicitly uncalibrated.

## Using the selection

After loading the updated backend and frontend, start **Dry Run** from the Bot
page on the desired account. The normal start flow applies the mathematical
preset to that account's stopped TopBot config. Existing runs are not switched
in place by this change. Stop an existing run through the normal controls before
starting a new one. This implementation task did not start or stop any account run.

The card identifies **TopBot Mathematical**. Its decision panel says
**Selected mathematical strategy · Dry Run**, and shows the exact forecast
recorded for that decision. It does not read a second possibly changed artifact
to explain the first decision. EMA entry overlays are hidden for this revision;
VWAP is descriptive context. Level 2 remains separate research context and does
not filter or alter the candle-model signal.

## Fitted-model requirement

Selection alone does not supply a trained model. The bounded local loader expects
the existing research artifact envelope at:

```text
TOPSIGNAL_DATABENTO_CACHE_DIR/probabilistic-v1/models/
  <SHA256(TopSignal user ID)>/<SHA256(contract ID + "|" + data-live-flag)>.json
```

The flag is `0` for simulated data and `1` for live data. The envelope binds
`owner_hash`, `contract_id`, `data_live`, and a `PathModel.to_dict()` model. Its
version must be `bayesian_cells_v1`; its outcome cutoff must precede the decision,
and it must be at most seven days old. The loader limits artifacts to 4 MB and
rejects invalid shapes, values, scopes and freshness. Model samples stay local;
only bounded forecasts are included in decision/API records.

No qualifying new model was trained or installed by this switch. Existing
research found only ten complete sessions, and its short diagnostic does not
meet the evidence requirements. Do not install synthetic test fixtures, combine
contracts, reuse reserved final outcomes for fitting, or lower the thresholds to
force trades. Use the [registered research workflow](topbot-probabilistic-research.md)
to produce chronologically valid training and validation evidence. Standard
backtests reject this revision because replaying today's fitted model against
its own training history would leak future information; use the dedicated
probabilistic research runner.

## Execution boundary

The mathematical model is now the Dry Run decision source. Existing account,
duplicate-order, position, loss-limit and cooldown checks still decide whether a
proposal becomes a recorded Dry Run attempt. One-contract sizing, the $250 daily
loss limit and 300-second cooldown are retained. A Dry Run attempt is not a
simulated or actual fill.

New mathematical Live Run starts and direct live evaluations are rejected before
provider access. Production validation and actual bracket/15-minute exit execution
are incomplete; emitting a bracket alone would not implement the model's time-exit
assumption. Completing those requirements is separate from selecting the model.
No order, broker configuration, live gate or risk increase was performed here.

## Verification

Tests exercise actual Bayesian BUY/SELL forecasts through the new adapter,
missing/stale/invalid observations, receipt-time causality, owner and contract
isolation, immutable model limits, legacy-versus-mathematical dispatch, direct
live-entry rejection and a Dry Run router/API result using the original forecast.
UI tests distinguish selected-model decisions from the old shadow display.

Recorded verification for the combined workspace: **2,101 backend tests passed,
nine skipped**, including the target-fill research compatibility revision;
**963 frontend tests and 39 launcher tests passed**; TypeScript/Vite production
build, full frontend lint and dependency audits passed. The skips require a disposable PostgreSQL database
or an optional local capture. Backend tests blocked external connections and
disabled dotenv loading, workers and live execution. The new mathematical and
affected replay paths also passed a focused 217-test run.

```powershell
backend/.venv/Scripts/python backend/tools/run_offline_tests.py -q
npm run test:dev-scripts
# From frontend/:
npm test -- --run
npm run build
```

The frozen target-fill runner now uses the separately documented
[v2 compatibility revision](topbot-target-stress-compatibility-2026-09-22.md).
Its dependency hashes remain enforced, and frozen fixture session limits remain
in place. Original v1 protocols and historical results are unchanged. Passing
software tests does not establish trading performance for the new strategy.
