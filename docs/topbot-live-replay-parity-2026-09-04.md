TopBot live/replay comparison — September 4, 2026
================================================

Engine `5.2.0-live-stop-budget-parity` aligns the numerical proposed-stop budget
calculation with the live helper. **It does not establish complete parity with
the current live runtime.** Research explicitly tests a newly designed daily
reset entry gate and, for intraday candidates, a clock-based session exit. The
user authorized changes to strategy and risk rules; these research policies do
not authorize real orders or automatic changes to live-run authorization.

Production order-routing and worker source remain unchanged. No real provider
orders, cancellations, closes, account changes, or live enablement were used in
this audit. Lifecycle probes used an isolated SQLite database and the existing
`RecordingClient` test double; their mocked order and close call counts were all
zero. No candidate has been promoted on the strength of these checks.

**Audited source**

Git base: `592791b43c57a8593451837ce2acb15a7a706465`, with the current uncommitted
research changes. SHA-256 values below hash the actual file bytes, including
their line endings, at the time of this audit.

| File | SHA-256 |
| --- | --- |
| `backend/app/services/bot_backtesting.py` | `07206662d92944643f3d65ed086a36ac072815212d445e8a94d3855bf2ef3620` |
| `backend/app/services/bot_service.py` | `b8cdc36fe407353a8c5c3e440767cd9ff9bab54e2f7502cfe375a169a79526e1` |
| `backend/app/services/bot_risk.py` | `55db10e29867273b5b04fc44d184eefe8ae757447b8a37653371a3d05176e613` |
| `backend/app/bot_worker.py` | `3448ab46677b48100f5a43d08f3960028e45b874819290f7d68bb8f5528a1a88` |

**What corrected research now models**

- Signals use only newly closed five-minute bars; every observed one-minute bar
  processes resting brackets, including minutes belonging to omitted five-minute
  aggregates. Missing next execution minutes discard pending entries.
- One MNQ contract, fresh cash/positions/counters per replay, explicit per-side
  commissions and adverse slippage on every fill. Stops are anchored to actual
  entry fills using the same whole-tick distances as broker brackets.
- Before an entry, the shared live `_proposed_order_stop_risk` helper computes
  whole-tick stop distance times tick value times quantity. The entry is blocked
  when that risk is greater than or equal to remaining daily loss capacity.
  This live calculation excludes prospective fees and slippage; replay still
  charges them to actual fills. Therefore a fill or gap can exceed the nominal
  daily budget despite passing this gate.
- Risk P&L books the complete trade net on its exit trading day, consistent with
  live preflight's closed-trade fee normalization. Cash still books entry and exit
  costs when those fills occur. At new research entries the single portfolio is
  flat, so its unrealized P&L is zero.
- Intrabar losses use the minute's close as the first time the loss is certain;
  cooldown includes the exact threshold boundary. This is a conservative time
  bound, not knowledge of the actual second of execution.
- Nonpositive cash blocks new entries; nonfinite cash rejects replay. This is
  not a model of historical initial margin, maintenance margin or broker
  liquidation thresholds.
- Corrected trades record source raw symbol, instrument ID and anchored stop and
  target. Carried rolls require a real old-delivery minute open at the exact
  switch time; otherwise the experiment fails. The legacy signal-bar mode retains
  its explicitly labeled noncausal prior-close roll approximation solely for
  reproducing old evidence.
- The baseline and candidates can be compared under the same new research risk
  policy. That comparison estimates strategy economics under that policy, not
  the uninterrupted operation of today's live worker.

**Exact current live lifecycle**

`bot_service.py::_evaluate_bot_config_impl` invokes risk gates only for actionable
`BUY`/`SELL` signals. A `HOLD` produces an audit/heartbeat, without checking the
daily loss cap or changing the run because of that cap. On an actionable signal,
`_only_transient_live_blocks` keeps the run armed only when **every** returned
code is in `_TRANSIENT_LIVE_RISK_CODES`. Severity does not determine lifecycle.
One nontransient code causes `config.enabled = False` and the run transitions to
`blocked`; it cannot resume next day without another explicit start. Submission
errors can instead transition to `error`. Disarming does not automatically
cancel resting broker brackets or flatten a position.

The following table covers every research-engine signal block and its relevant
live equivalent, plus live conditions directly reachable by the candidate rules.
Some rows contain multiple codes because live risk evaluation reports more than
one simultaneous problem.

| Condition / live code | Current live outcome | Corrected research behavior / limitation |
| --- | --- | --- |
| `proposed_stop_risk_exceeds_daily_loss_budget` | Transient; run stays running if this is the only class of block | Same numeric entry gate; later decisions may qualify and next-day capacity resets |
| `max_daily_loss` | Nontransient; next actionable entry stops the run permanently until manual rearm | Blocks entries on that trading day, then resets; deliberately different research policy |
| `max_trades_per_day` | Transient | Daily entry-count gate; live counts account-wide entry fills |
| `cooldown_after_loss` | Transient | Same threshold comparison; actual live fill time versus conservative replay minute-close time |
| `cooldown_after_rejection` | Transient | Not simulated; live reads recent blocked/rejected/error order attempts, while research has no provider attempt lifecycle |
| `max_contracts`, `max_open_position`, `max_account_gross_position` | Transient | Research enforces its isolated quantity limits; live also includes other account positions and working orders |
| `outside_session`, `exchange_session_closed`, `stale_market_data`, `missing_market_data` | Transient | Research uses configured hours, observed minutes and strict signal timing; live wall-clock/data-age scheduling differs |
| Replay `missing_next_execution_minute` / `stale_session_signal` | No identically named live code; live may evaluate an older closed candle while within its age limit | Research deliberately discards rather than postpones these entries |
| `authoritative_target_already_reached` with a valid bracketed same-side entry | Transient by itself | Replay ignores same-side duplicates; live still runs final risk checks and creates a blocked attempt |
| `atomic_reversal_not_supported` | Nontransient; run stops | Replay blocks that signal and continues. Baseline v5 can emit opposite target entries while held. Position-aware intraday candidates return HOLD while held |
| `authoritative_target_direction_conflict`, `authoritative_target_invalid`, `authoritative_target_size_invalid`, `authoritative_target_audit_unavailable`, `partial_reduction_not_supported` | Nontransient | Not normal outputs of valid fixed-one-contract fixtures; possible after external position changes, stale observed state or malformed inputs |
| `prior_delivery_exposure` | Nontransient for new entries while an old delivery still has positions or orders | Research explicitly liquidates a carried position using raw old-delivery data; live requires exact-contract management and verification |
| `contract_not_allowed` | Nontransient | Replay blocks and continues; valid MNQ preset should not trigger it |
| Replay `invalid_quantity`; live `invalid_order_size`, `order_size_too_large`, `fractional_contract_size`, `invalid_resulting_position` | Nontransient | Replay rejects individual invalid signals. Valid fixtures emit only one whole contract |
| Replay `invalid_signal_plan` / `proposed_stop_risk_unavailable`; live stop/target/entry geometry, missing bracket, missing instrument metadata or invalid risk-data codes | Nontransient | Replay blocks individual malformed signals or rejects malformed data. Candidate validation must prevent these; they are not ordinary daily gates |
| Scheduled exit target zero, but provider is already flat | `authoritative_target_already_reached` plus **nontransient** `protective_stop_bracket_missing` and `proposed_stop_risk_unavailable`; run stops | Research skips a clock exit when already flat. Live needs an authoritative idempotent no-op exit path |
| `working_order_direction_conflict`, `working_order_exposure`, `working_order_reconciliation_unavailable`, `recent_live_submission_settling`, `order_reconciliation_settling` | Transient | No modeled network/working-order settlement lifecycle |
| `unresolved_order_submission`, unconfirmed cancel/close, invalid provider position/order responses | Nontransient or error outcome | Research cannot simulate broker uncertainty; operational safety and reconciliation tests remain mandatory |
| Replay `nonpositive_cash` | No identical live balance gate; provider tradability, account state and margin determine eligibility | Research blocks further entries, without inventing historical margin data |

All authorization, account-state/classification, shared-account ownership,
worker-lease, emergency-latch, malformed-data and unsupported-action failures
also remain relevant production controls. Research does not claim to simulate
them. Unknown or ineligible account classifications cannot authorize orders;
some unavailable-state codes are retryable, while actual ineligibility, changed
tradability, disabled bots and ownership conflicts stop or refuse execution.

Two lifecycle details materially limit the interpretation of the original v5
control. Opposite target signals while a position remains open can terminate a
real run even though research continues. Same-side target no-ops are transient
but still go through final entry checks: other simultaneous conditions, such as
an account daily loss breach, can make that evaluation terminal.

The new proposed-stop gate makes an ordinary stopped-out trade less likely to
exceed remaining daily capacity, but does not eliminate a terminal live breach.
Fees, slippage, gap losses, carried-position P&L or unrelated account activity can
cross the limit. Whether that breach stops the run then depends on a subsequent
actionable signal. If only HOLD signals arrive until the next trading day, there
is no daily-loss risk check during that interval. A properly classified
position-reducing exit bypasses entry-specific daily/session/data-age gates.

`_cooldown_block` examines blocked/rejected/error attempts as well as losses.
Consequently even a transient final-preflight rejection can affect later live
decisions and their timing. This is not represented by the pure research
entry-filter counters and must not be described as full lifecycle equivalence.

**Observed mock evidence**

These probes called the real `start_bot_run` / `evaluate_bot_config` routing
logic with an in-memory database and mocked broker. The values below were
observed, not inferred solely from code membership.

| Scenario | Risk codes | Run afterward / config enabled |
| --- | --- | --- |
| $200 realized loss, proposed $100 stop, $250 cap | `proposed_stop_risk_exceeds_daily_loss_budget` | `running` / true |
| $251 realized loss, next BUY | `max_daily_loss`, `proposed_stop_risk_exceeds_daily_loss_budget` | `blocked` / false |
| Same $251 loss, HOLD | none | `running` / true |
| SELL target -1 while provider is long +1 | `atomic_reversal_not_supported` | `blocked` / false |
| SELL exit target 0, provider already flat | `authoritative_target_already_reached`, `protective_stop_bracket_missing`, `proposed_stop_risk_unavailable` | `blocked` / false |
| BUY target +1, provider already long +1 | `authoritative_target_already_reached` | `running` / true |
| Account already has configured maximum entry fills | `max_trades_per_day` | `running` / true |

Existing regression evidence includes
`tests/test_bot_execution_safety.py::test_max_daily_loss_is_sticky_and_terminal_for_continuous_run`,
`::test_live_preflight_outage_is_transient_for_continuous_run`,
`::test_outside_session_is_transient_for_continuous_run`,
`::test_bracketed_atomic_reversal_fails_closed`, and
`::test_topbot_blocked_short_target_only_closes_a_provider_long`.
The last two tests already prove routing is blocked but do not themselves
assert all continuous-run lifecycle outcomes shown above.

The numerical replay change passed 124 tests across
`test_bot_minute_execution.py`, `test_bot_backtesting.py`,
`test_bot_replay_arrays.py` and `test_bot_risk_hardening.py`. This includes a
real-production-evaluator warmup test, proposed-stop boundary comparison with
the pure live risk policy, two losing trades followed by a rejected third
entry, bankruptcy after a gap, exit-day fee attribution and actual delivery
fields on a causal roll trade. These are numerical/data tests, not proof of
operational equivalence.

**Declared differences to test before promotion**

1. **Daily reset policy.** Research assumes the daily loss cap gates further
   entries and resets next trading day. If a promising candidate is retained,
   implement that policy deliberately in production as a daily entry lock,
   while preserving position reductions, manual stop, emergency latches,
   authorization and restart/rearm requirements. Do not silently revive already
   stopped/blocked live runs. Test next-day capacity recovery without new order
   permission being inferred from a historical run.
2. **Clock exits.** Share the same holiday-aware deadline function between
   research and production. The research hook executes on an observed minute
   independently of complete five-minute bars, and retains an old position's
   original deadline across outages. Production's worker must check that clock
   independently of its new-candle gate and market-history fetch. Never invent
   candles to satisfy `_actionable_candle_timestamp`; introduce an audited
   server-generated clock action identity. Reuse the existing verified
   contract-close path, including bracket cancellation, fresh position checks,
   mutation fencing and final flat verification. An already-flat provider
   response must be a safe completed no-op, not an unprotected-entry error.
3. **Observed positions.** Live evaluation must receive account-scoped position
   quantity and entry time from verified provider data, then recheck the target
   at final preflight. `ProjectXClient.search_open_positions` exposes
   `creation_timestamp`. Missing/ambiguous position age requires reconciliation;
   do not substitute the latest strategy candle. Position-aware entry HOLDs
   must suppress unsupported atomic reversal attempts.
4. **Execution latency.** Worker defaults are a five-second poll, two-second
   candle-close grace and zero-to-three-second schedule jitter, followed by
   history/provider/preflight requests. Immediate next-minute open is an
   execution approximation. Run at least an additional one-minute delay
   scenario with unchanged signal information, next observed executable price,
   normal brackets and all costs. Slippage-only sensitivity does not substitute
   for delay sensitivity. Close/cancel latency and races also need mocked and
   forward Dry Run checks.
5. **Contract choice.** Historical selection uses prior completed-session
   volume. `resolve_current_market_contract` instead follows the provider's
   currently active contract; its switch dates need not match. An intraday flat
   strategy limits carried-roll exposure but does not prove identical bar
   selection near a roll. Preserve prior-delivery blocking, capture live raw
   contract identities, and choose/verify one documented contract-selection
   policy before claiming signal parity. Never flatten old exposure using new
   contract prices or silently route against the saved generic contract label.
6. **Broker and portfolio state.** Research assumes an isolated one-contract
   account. Production risk is account-wide and includes other positions,
   working orders, manual fills, unrealized P&L, actual provider fees,
   tradability/classification, rejected attempts and settlement uncertainty.
   Validate the intended isolated-account assumption; retain those protections.
7. **Data and uncertainty.** OHLCV cannot resolve target queue priority, the
   within-minute path, intrabar equity extrema or whether every absent minute
   represents no trades versus unavailable source observations. Stop-first
   ambiguity and adverse cost assumptions remain approximations. The recurring
   calendar has corrected historical halt dates but is not complete historical
   status data for extraordinary closures. All 2019–July 2026 history is reused
   evidence; none becomes untouched validation through these engine changes.

Research may continue under these explicitly declared new policies without
changing live behavior. Promotion requires a promising robust candidate, the
shared production implementation, appropriate lifecycle and latency testing,
and evidence beyond the previously examined history. Passing engine tests alone
does not meet that standard.

**Optional latency stress added after the numerical 5.2 audit**

Engine `5.3.0-entry-latency-stress` and the research CLI now support
`entry_delay_minutes=0` (default) or `1` for observed-minute execution. Delay one
retains the original signal/decision information, waits for the exact observed
minute at decision plus one minute, and anchors the bracket to that delayed
open. It discards the entry when that minute is missing, the session ends, or a
delivery switch invalidates the pending signal. Explicit exits keep normal
timing. This deliberately coarse delay stress exceeds ordinary worker grace and
jitter and does not claim to reproduce exact broker latency. Run it on promising
rules after the default-delay historical screen; do not mix delay modes in one
compatibility group. The exporter records and separates their identities, with
missing delay fields in older manifests treated as zero. Corrected-mode ledgers
also retain the actual original five-minute signal timestamp rather than the
last minute of that signal bar; this audit-field correction does not change
default-delay trade economics.

A remaining target-fill assumption requires separate later stress: touching a
limit price in OHLC does not guarantee an exchange fill or queue priority.
Charging adverse slippage to the assumed target fill is economically
conservative conditional on a fill, but does not prove that fill occurred.
A promising candidate must survive a trade-through/target-nonfill scenario or
be checked against verified finer-grained execution data. This is a declared
limitation alongside latency, not a reason to hide or discard default-screen
experiments. No production API, worker control or live order path was wired to
these research-only options.
