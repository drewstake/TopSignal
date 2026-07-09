# TopSignal

TopSignal is a trading analytics and journaling application for ProjectX/TopstepX-style futures accounts. It syncs account and execution data from the provider, stores it in PostgreSQL, computes account-level performance and risk metrics, and presents those results in a React dashboard with account management, trade review, expense tracking, payout logging, trading-bot controls, and a daily trading journal.

This repository contains:

- a React + TypeScript frontend in `frontend/`
- a FastAPI backend in `backend/`
- a PostgreSQL schema and raw SQL migrations in `db/`

## Why This Project Exists

ProjectX exposes account and trade data, but the raw provider API is not a good day-to-day analytics workspace by itself. TopSignal exists to solve that gap.

It is built for traders who want to:

- keep a local or cloud-backed history of their ProjectX trades
- analyze performance without re-querying the provider for every page load
- understand risk, drawdown, expectancy, and pacing in plain numbers
- journal trading days per account with autosave and trade-stat snapshots
- track real cash costs such as evaluation fees, activations, resets, data fees, and actual payouts
- test simple rule-based bot decisions against ProjectX candles with a server-side audit trail

## What The App Does

At a high level, TopSignal:

1. pulls account and execution data from ProjectX
2. normalizes and stores it in PostgreSQL
3. derives account, trade, day, and behavior metrics from stored data
4. exposes those results through a FastAPI API
5. renders them in a frontend focused on trading review workflows

Core features in the current routed app:

- Account discovery and account-state tracking
- Main-account selection
- Local account display-name overrides
- Manual trade sync from ProjectX
- Account-level performance summaries
- Analytics-only Copy Trade Mode for combined leader/follower dashboard views
- Trading-day PnL calendar
- Trade event feed with lifecycle-derived entry/exit fields
- Expense CRUD, spend summaries, and payout-minus-spend net ranges
- Payout tracking, payout totals, and spend-since-last-payout context
- Daily journal entries with autosave, optimistic concurrency, trade-stat pulls, and image uploads
- Trading bot configuration, dry-run execution controls, deterministic backtesting, signal charting, market analysis, trade-plan evaluation, and bot activity review
- Workspace theme selection with live palette previews
- Optional Supabase authentication for multi-user deployments

## Product Walkthrough

The screenshots below are representative local captures from the routed app. Account balances, trade rows, fees, and dates will reflect whichever ProjectX account and database are connected in your environment.

### Dashboard

The dashboard is the main account analytics surface. It is account-scoped by default and can switch into an analytics-only Copy Trade Mode for combined leader/follower account review.

It shows:

- headline performance and edge metrics
- drawdown and risk-control context
- long-vs-short breakdowns
- payoff and activity metrics
- sustainability scoring
- a trading-day PnL calendar
- a daily account-balance curve derived from the calendar
- a recent trade-event feed
- optional combined copied-account stats for leader/follower workflows

Dashboard overview:

![Dashboard performance, edge, risk, and sustainability panels](images/metrics.png)

Calendar drill-down:

![Trading-day PnL calendar with journal markers](images/PnL_Calender.png)

Balance curve derived from the selected trading range:

![Daily account balance curve](images/daily_account_bal.png)

Recent execution review feed:

![Recent trade events table](images/recent_trade_events.png)

Important dashboard behaviors:

- The active account comes from the global account picker in the app shell.
- The page can sync trades, change time range, and drill into a specific trading day.
- Clicking a PnL-calendar day filters the trade feed to that trading day.
- Calendar days can open or create a journal entry for that date/account.
- `Summary` opens a coach-style trading summary for the selected dashboard range, including verdict, sample quality, top levers, risks, improvements, and a short action plan.
- `Copy Full Stats` copies a text payload for the selected dashboard range.
- `Copy Trade Mode` can combine the selected leader account with up to four follower accounts for adjusted dashboard totals.
- When Copy Trade Mode is active and calculable, headline metrics, daily PnL, balance context, and calendar data use combined leader/follower results. Otherwise the dashboard shows the selected account only.
- Copy Trade Mode shows leader contribution, follower contribution, copied account count, follower-only PnL, warnings, and resettable likely-uncopy event tracking.
- Copy Trade Mode settings and uncopy-event reset timestamps are stored in browser storage. This mode does not place orders or enable live trade copying.
- The dashboard uses `summary-with-point-bases` so it can render one summary request plus point-payoff comparisons instead of fanning out multiple summary calls.

### Accounts

The Accounts page is the account-management surface for ProjectX accounts.

A user can:

- view discovered accounts
- see current balance, state, and last known trade timestamp
- toggle hidden and missing accounts into view
- mark one account as the main account
- set the active account used across the rest of the app
- override the provider account name with a local display name
- merge journal history from an older account into a replacement account
- resolve the last trade timestamp from the provider when local data is stale or absent

Accounts page:

![Accounts management page](images/accounts.png)

TopSignal tracks four account states:

- `ACTIVE`: visible and tradable
- `LOCKED_OUT`: account exists but cannot trade
- `HIDDEN`: provider returned it as not visible
- `MISSING`: previously seen, now absent from provider results after a buffer window

Account selectors prioritize the main account inside account-type groups so Express, combine, other, and practice accounts are easier to scan during trading workflows.

### Trades

The Trades page is the execution-review surface.

Trades page:

![Trade review filters, summary cards, and execution feed](images/trades.png)

A user can:

- filter trades by date range
- filter trades by symbol text
- choose a row cap
- refresh data from the local cache
- explicitly sync the selected date window from ProjectX
- inspect summary metrics for the filtered window
- review trade events with inferred entry time, exit time, duration, entry price, exit price, and PnL

If an account is currently `MISSING`, the page still shows locally stored data and does not claim live provider sync is available.

### Expenses And Payouts

The Expenses page tracks paid account costs, operating costs, and recorded payouts.

Expenses and payouts page:

![Expenses, combine spend tracker, and payout logging page](images/expenses.png)

A user can:

- create, list, filter, paginate, and delete expenses
- group spend by date range and category
- track evaluation fees, activation fees, reset fees, data fees, and other costs
- optionally associate an expense with an account, plan size, and account type
- record payouts separately from expenses
- view payout totals, averages, and counts
- see recorded spend, spend since the latest payout, and net after payouts
- compare payout-minus-spend cards for 1 month, 3 months, 6 months, YTD, 1 year, anniversary years from the first cash-flow date, and all time

The page also contains a combine spend helper that:

- infers active combine accounts from account-name prefixes
- keeps a client-side spend ledger in browser storage
- can sync inferred evaluation purchases into the `expenses` table
- reconciles generated rows with manually logged or imported combine expenses
- prefers a manual combine expense over a generated row for the same account

This combine tracker is implemented on the frontend and is not a standalone backend subsystem.

### Journal

The Journal page is an account-scoped daily trading journal.

A user can:

- create one journal entry per account per date
- filter entries by date range and mood
- edit title, mood, tags, and notes
- rely on debounced autosave
- archive or unarchive entries
- paste images into the entry workspace
- pull a trade-stat snapshot into the journal entry
- generate or append a Gemini-backed AI Recap for the selected trading date and account
- copy journal content for recent entries
- merge one account's journal history into another account without deleting the source account history

Journal workspace:

![Trading journal page](images/journal.png)

Notable journal behavior:

- Autosave uses optimistic concurrency with a `version` column.
- If a stale save collides with newer server state, the API returns `409 version_conflict` and the UI can reload the server version.
- Journal images are stored either locally on disk or in Supabase Storage, depending on configuration.
- Trade stats can be pulled by explicit trade IDs, explicit date range, or the entry's trading day.
- AI Recap generation uses the backend Gemini client and the existing `GEMINI_*` environment variables listed in this README.
- AI Recap skips days with no trades instead of creating or updating a journal entry.
- Journal merge matches entries by `entry_date`. `skip` keeps the destination entry for that date; `overwrite` replaces the destination entry content with the source entry.
- Journal merge copies entries into the destination account and leaves the source account untouched. When image copying is enabled, new destination image records and files are created so source images are not orphaned or shared.

### Bot

The Bot page is the account-scoped rule-execution workspace for ProjectX market data.

Bot control page:

![Bot signal chart, strategy configuration, and activity panels](images/bot.png)

A user can:

- create, edit, select, and delete named bot configurations
- bind a bot to a ProjectX account and contract
- search ProjectX contracts from the configuration form
- choose from multiple strategy types, including SMA Cross, EMA Scalping, Support/Resistance, Donchian Breakout, FVG Sweep + MSS, Liquidity Sweep + Retest, Supertrend Pivot, RVOL Breakout, relative-strength strategies, Bollinger/VWAP/Fisher mean reversion, ORB variants, and pullback/trap strategies
- set risk controls such as order size, max contracts, max daily loss, max trades per day, max open position, trading session, cooldown, and max data staleness
- start a dry-run bot run, evaluate the strategy once, or stop the latest run
- run a bounded historical replay with configurable starting balance, per-contract commission, slippage, and final-position handling
- inspect backtest summary metrics, equity/drawdown charts, daily and monthly results, warnings, and the complete trade ledger
- review the latest decision, candle timestamp, decision reason, risk blocks, and order-attempt status
- inspect market bias, scenario weights, expected move, invalidation, nearby levels, volatility, volume, reasoning, and risk notes
- review trade-plan grades when a strategy produces entry, stop, and target prices
- inspect recent decisions, order attempts, risk events, and run history

The page also includes a Signal Chart backed by ProjectX candles. It supports selectable chart timeframes, live/last price display, strategy overlays, VWAP, buy/sell signal markers, computed buy-side and sell-side liquidity levels, drawing tools, refresh, data-gap repair, and y-axis fit controls.

The Analysis panel consumes the versioned backend market-analysis contract when available. It reports the closed and partial candle counts, source timeframe, latest closed-candle timestamp, data age, staleness threshold, detected gaps, missing inputs, and an explicit data-confidence read. Indicator features use closed bars only and include trend strength, regime, ATR/volatility percentile, relative volume, VWAP location, multi-timeframe alignment, and nearby support/resistance. The panel separates setup quality, market direction, execution risk, and data confidence so a high directional tilt is not confused with a safe or complete setup.

If backend analysis is absent, the UI may show a clearly labeled **Local fallback analysis** derived from the closed candles already loaded in the chart. The fallback follows the same scenario-weight terminology and never substitutes a partial bar for missing closed history. `probability_method: "heuristic_scenario_weight"` identifies the weighting method; bullish, bearish, and sideways scenario weights are normalized to exactly 100 and are not calibrated outcome probabilities, predictions, or financial advice.

When a strategy emits a complete trade idea, bot evaluation can attach a versioned trade-plan readout with a 0-100 score, letter grade, `take`/`wait`/`avoid` decision, and separate setup-quality, direction, execution-risk, and data-confidence dimensions. It validates long/short entry-stop-target geometry, normalizes prices to the instrument tick when metadata is known, and exposes risk points/ticks, reward points/ticks, R multiple, break-even win rate, estimated dollar risk/reward, and available daily-loss or drawdown context. The response also includes category maximums and awarded points, explicit penalties and score caps, missing inputs, and the strongest positive and negative drivers. Unknown account, instrument, or news inputs remain unknown rather than being synthesized.

Important bot behaviors:

- The routed page lives at `/bot` and accepts the same active-account query parameter used by the app shell.
- New configurations default to dry-run mode and are saved disabled.
- The current UI only starts dry-run runs; live order routing is not exposed by the page controls.
- Bot decisions, runs, order attempts, and risk events are persisted server-side for auditability.
- Completed backtests are persisted separately in `bot_backtests` with their engine, configuration, assumptions, market-input fingerprint, and result snapshots.
- Candle reads use ProjectX market-data endpoints, backend `projectx_market_candles` storage, and a small frontend candle cache for chart responsiveness.

#### Strategy registry

`backend/app/services/bot_strategy_registry.py` is the authoritative strategy catalog. Each immutable registry entry records the strategy identifier, parameter normalizer, configuration validator, required timeframe or timeframes, conservative and hard minimum-history requirements, evaluator, auxiliary-data requirements, and whether registry-level backtesting is supported.

The registry contains all 20 configured strategy identifiers:

`sma_cross`, `support_resistance`, `liquidity_sweep_retest`, `donchian_breakout`, `opening_rvol_breakout`, `bollinger_rsi_reversal`, `bollinger_mean_reversion`, `macd_support_resistance`, `delayed_orb_confirmation`, `orb_fibonacci_pullback`, `supertrend_pivot`, `ema_trend_pullback`, `ema_scalping`, `vwap_atr_mean_reversion`, `vwap_gap_retrace`, `fisher_transform_mean_reversion`, `atr_adjusted_relative_strength`, `relative_strength_spy`, `pullback_trap_reversal`, and `fvg_sweep_mss`.

Dispatch is being replaced incrementally to protect characterized strategy behavior. Supported-strategy validation and evaluator selection now come from the registry. Strategy-specific candle acquisition and argument assembly remain explicit where a strategy needs fixed, derived, daily, benchmark, session, position, or other auxiliary data. Existing formula helpers remain in `bot_service.py` and are invoked lazily through the registry rather than being broadly rewritten.

#### Deterministic backtesting

The Bot page and authenticated `POST /api/bots/{id}/backtests` endpoint run the event-driven engine in `backend/app/services/bot_backtesting.py`. The endpoint selects the bot by both ID and authenticated `user_id`, then reads only that user's stored `projectx_market_candles` rows for the configured contract and exact timeframe where `live=false` and `is_partial=false`. It never fetches provider data, creates an order attempt, or calls live/dry-run routing code.

The currently replayable strategies are exactly:

- `sma_cross`
- `ema_trend_pullback`
- `pullback_trap_reversal`
- `bollinger_mean_reversion`
- `bollinger_rsi_reversal`
- `vwap_atr_mean_reversion`
- `orb_fibonacci_pullback`

These paths call the same evaluator functions used by normal bot evaluation. Every other configured strategy fails explicitly with `strategy_not_supported_for_backtesting`; none is approximated:

| Unsupported strategy | Exact replay capability still required |
| --- | --- |
| `support_resistance` | Synchronized closed 4-hour and 1-hour candle streams |
| `liquidity_sweep_retest` | Synchronized closed 4-hour and 1-hour candle streams |
| `macd_support_resistance` | Synchronized higher timeframes and exact trailing-stop replay |
| `opening_rvol_breakout` | Its fixed 5-minute multi-session dataset |
| `delayed_orb_confirmation` | Its fixed 1-minute stream and per-session loss-stop state |
| `supertrend_pivot` | Synchronized signal-timeframe and closed daily candles |
| `fvg_sweep_mss` | Synchronized FVG and lower-timeframe structure streams |
| `atr_adjusted_relative_strength` | An exactly aligned benchmark candle stream |
| `relative_strength_spy` | An exactly aligned SPY candle stream |
| `vwap_gap_retrace` | Fixed 1-minute data and alternative exit-path replay |
| `donchian_breakout` | Stateful channel, trailing-stop, sizing, and entry-plan replay |
| `ema_scalping` | Exact strong-opposite-candle exit replay |
| `fisher_transform_mean_reversion` | Exact Fisher-neutral exit replay |

Replay assumptions are deliberately explicit and are returned and persisted with every result:

- A strategy is evaluated only after each bar closes and receives only bars whose close time is at or before that event. Partial bars are excluded. A signal from bar T can fill no earlier than the next available bar's open.
- Market fills default to next-bar open. Configured slippage ticks move every entry and exit adversely, and commission per contract is charged on both entry and exit.
- At a new bar, an existing position's resting stop/target gap is resolved before a queued market reversal; the queued fill is then processed, followed by that bar's intrabar stop/target range and finally its close signal. Pending signals expire at the configured session boundary.
- P&L is `(exit - entry) / tick_size * tick_value * quantity`, with direction inverted for shorts. Tick size and tick value come from required `instrument_metadata`.
- Bracket stops and targets come from the real evaluator payload. As in live routing, each level is converted to a whole-tick distance from the signal price and that distance is anchored to the actual next-bar fill. All supported strategies except `sma_cross` require a valid stop and target; an incomplete or invalid signal plan is blocked and reported instead of inferred.
- If stop and target are both touched inside one OHLC bar, the stop is assumed to fill first. An adverse stop gap fills at the bar open; a target receives no favorable price improvement.
- Opposite signals close and reverse at the next bar open. Same-side signals do not pyramid. Entry size uses the evaluator's target quantity when supplied, otherwise the bot order size; a size over the max-contract or max-open-position setting is blocked rather than silently reduced.
- Entries obey the bot's New York session, after-loss cooldown, max-trades-per-day, and realized daily-loss limits. These counters reset at the requested start, and the loss gate uses only this isolated simulation's realized net P&L rather than pre-existing account-wide P&L. Missing bars are not interpolated; the next stored bar is used and a gap warning is returned. Strategies that depend on a session prefix fail explicitly if the required opening candle is absent.
- Open positions are force-closed at the final bar's close by default. If disabled, the open position remains excluded from closed-trade metrics and the result includes a warning. A final-bar signal is never filled without a following bar.

Requests are limited to 366 days and 20,000 execution bars, require at least two closed execution bars, and are rejected when the range multiplied by the evaluator history window would exceed the 10,000,000 bar-visit computation budget. The strategy's hard minimum history must already be closed at the first replay event (except ORB's intentional in-session opening-range formation); earlier closed bars are loaded only as evaluator warm-up, with a warning when the configured/conservative warm-up is incomplete. Results include gross/net P&L, fees, count, win rate, profit factor, expectancy, average win/loss, payoff ratio, dollar/percent drawdown, MAE/MFE, long/short breakdowns, streaks, exposure, equity/drawdown series, daily/monthly aggregates, a trade ledger, and sample/data-quality warnings. Drawdown uses bar-close mark-to-market equity, exposure is the percentage of replay bars with any position exposure, MAE/MFE are tick-value-aware dollars before commission, and daily/monthly rows group closed trades by their New York trading-day exit.

Each `bot_backtests` row records engine version `1.0.0`, the bot configuration at run time, all execution assumptions, requested/actual range, instrument metadata, and a SHA-256 fingerprint of the ordered warm-up and execution candle inputs. Reproducing the same numbers requires the same engine version, configuration and assumptions snapshots, and candle input set. The model intentionally does not simulate order-book liquidity, queue position, latency, partial fills, or intrabar paths beyond the conservative OHLC rules above.

#### Execution safety and idempotency

Omitting `dry_run` always resolves to dry-run. A live order can reach the provider only when all live gates pass: the request explicitly sends `dry_run=false`, the saved bot configuration has `execution_mode=live`, the request explicitly confirms live routing, `TOPSIGNAL_LIVE_EXECUTION_ENABLED` is enabled, the process is not running under tests, the account is active and tradable, the funded/live-account restriction does not match, and all ordinary contract, position, session, staleness, cooldown, trade-count, and loss gates pass. Setting the environment flag alone is never sufficient. The `/evaluate` endpoint remains dry-run-only.

Every actionable BUY or SELL signal receives a server-generated, versioned idempotency key derived from this tuple:

`(user_id, bot_config_id, closed_candle_timestamp_utc, action, execution_mode)`

The key is stored on the signal decision and order attempt. A partial unique database index on `(user_id, bot_config_id, idempotency_key)` is the final concurrency guard. If two evaluations race, only the winner can create the actionable attempt. The losing evaluation is preserved as a `duplicate_skip` decision and returns the original attempt ID; it never routes a second provider order. Provider custom tags are derived from the same key so an operator can reconcile the local attempt with ProjectX without exposing user or account data in the tag.

Evaluation responses include a top-level `status`, `correlation_id`, optional `idempotency_key`, and optional `duplicate_of_order_attempt_id`. Status is one of `evaluated`, `held`, `risk_blocked`, `duplicate_skipped`, `dry_run_attempt`, `submitted`, or `error`. Correlation and idempotency identifiers are also attached to the relevant audit rows and structured, allow-listed logs; credentials and raw provider payloads are not included in those logs.

Run transitions are constrained to `running -> stopped`, `running -> blocked`, or `running -> error`; terminal runs do not transition back to running. Starting again creates a new run, and a partial unique index permits at most one running row per user and bot configuration. Heartbeats, `last_evaluated_at`, `stop_reason`, and sanitized `last_error` make terminal and failure state inspectable. A blocked or failed run disables its bot configuration, and a failed start is retained as an error audit rather than being left running.

There is no continuous TopBot runner in this release. Start and evaluate are request-driven, single-evaluation operations, and the optional ProjectX streaming runtime does not schedule bot evaluations. If continuous execution is added later, it must remain disabled by default, dry-run-only initially, and advance only on newly closed candles.

### Themes

The Themes page is the routed appearance workspace for choosing the app's visual palette.

Themes page:

![Theme palette gallery and live preview](images/themes.png)

A user can:

- preview each built-in workspace palette
- apply a theme across the routed app
- compare status, metric, control, and surface colors before switching

Themes are stored client-side and applied through CSS variables, so they affect the shell, dashboard, trade review, journal, bot, and supporting controls without a backend migration.

### Routed Pages

The current router includes these product surfaces:

- `/`: dashboard
- `/accounts`: account management
- `/trades`: execution review
- `/expenses`: expenses and payouts
- `/journal`: daily journal
- `/bot`: bot configuration, dry-run review, and deterministic backtesting
- `/themes`: appearance and palette selection

There are no separate `overview/` or `analytics/` prototype route directories in the current tree.

## Architecture

```mermaid
flowchart LR
    A["React frontend"] --> B["FastAPI backend"]
    B --> C["ProjectX API"]
    B --> D["PostgreSQL"]
    B --> E["Supabase Auth (optional)"]
    B --> F["Supabase Storage (optional, journal images)"]
    G["ProjectX streaming runtime (optional)"] --> B
    B --> H["Derived metrics + cached trade history"]
    H --> A
```

### Frontend Stack

| Layer | Implementation |
| --- | --- |
| Framework | React 19 |
| Language | TypeScript |
| Routing | React Router 7 |
| Build tool | Vite 7 |
| Styling | Tailwind CSS + custom UI primitives |
| Auth client | `@supabase/supabase-js` when Supabase env vars are present |
| Tests | Vitest |

### Backend Stack

| Layer | Implementation |
| --- | --- |
| API framework | FastAPI |
| ORM | SQLAlchemy 2 |
| Validation | Pydantic v2 |
| DB driver | `psycopg` |
| Auth verification | PyJWT + JWKS or shared secret |
| Tests | Pytest |

### Database

TopSignal is PostgreSQL-first. The schema is defined in `db/schema.sql`, with incremental SQL migrations in `db/migrations/`.

Important implementation detail:

- The current app's main analytics dataset is `projectx_trade_events`, not the legacy `trades` table.
- The legacy `/metrics/*` endpoints and `/trades` endpoint still read from `trades`.
- The account dashboard, trade review, PnL calendar, and journal trade-stat flows use `projectx_trade_events`.
- Bot configuration and audit history use `bot_configs`, `bot_runs`, `bot_decisions`, `bot_order_attempts`, and `bot_risk_events`.
- Completed replay snapshots use `bot_backtests`; their source ProjectX candles remain in `projectx_market_candles`.
- ProjectX market candles are cached in `projectx_market_candles` for bot charting and backtesting reads.
- Expense rows include `source_id` so imported or generated rows can be deduplicated by source identity without colliding with a manual row that has the same date, amount, category, and account fields.

### External Integrations

| Integration | Purpose |
| --- | --- |
| ProjectX API | Account discovery, provider auth, trade history sync, last-trade lookup, contract search, market candles, and optional bot order routing |
| Supabase Auth | Optional JWT-based user auth |
| Supabase Storage | Optional journal image storage backend |
| ProjectX market/user hubs | Optional streaming lifecycle tracking |

## System Structure

### Frontend

- `frontend/src/app/`: app shell and router
- `frontend/src/pages/`: routed product pages
- `frontend/src/pages/bot/`: bot configuration, backtest panel, signal chart, candle cache, and chart data helpers
- `frontend/src/pages/themes/`: theme gallery and live appearance preview
- `frontend/src/lib/api.ts`: shared API client, request helpers, caches, and in-flight dedupe
- `frontend/src/lib/types.ts`: frontend API types
- `frontend/src/utils/`: metric helpers and formatting

### Backend

- `backend/app/main.py`: FastAPI app and route definitions
- `backend/app/models.py`: SQLAlchemy models
- `backend/app/bot_schemas.py`: bot API request/response schemas
- `backend/app/trade_plan_schemas.py`: trade-plan evaluation request/response schemas
- `backend/app/services/bot_strategy_registry.py`: immutable strategy metadata, validation adapters, and evaluator dispatch
- `backend/app/services/bot_candle_acquisition.py`: strategy-specific candle and auxiliary-series acquisition
- `backend/app/services/bot_market_analysis.py`: canonical closed-bar market-analysis contract and deterministic feature model
- `backend/app/services/bot_risk.py`: pure, ordered execution-risk policy
- `backend/app/services/bot_execution_safety.py`: idempotency keys, run transitions, live gates, correlation IDs, and safe logging
- `backend/app/services/bot_serialization.py`: bot audit and API serialization
- `backend/app/services/bot_backtesting.py`: closed-bar replay for registry-marked supported strategies; it has no order-routing dependency
- `backend/app/services/bot_service.py`: compatibility facade, execution orchestration, and still-characterized strategy formulas awaiting safe incremental extraction
- `backend/app/services/trade_plan_evaluator.py`: versioned trade geometry, risk, and scoring model
- `backend/app/db.py`: engine/session setup and startup schema compatibility patches
- `backend/app/auth.py`: auth middleware helpers and JWT validation
- `backend/app/services/`: ProjectX sync, analytics, journaling, image storage, payout, streaming, bot evaluation, and backtesting helpers

### Database

- `db/schema.sql`: current schema for fresh database setup
- `db/migrations/*.sql`: additive schema evolution

## Data Flow

### 1. Account Sync Flow

When the frontend requests `GET /api/accounts`:

1. the backend creates a `ProjectXClient` for the current user
2. it calls ProjectX account search
3. it normalizes provider account flags into TopSignal account states
4. it upserts local `accounts` rows
5. it marks older accounts as `MISSING` if they disappear from provider results for longer than the configured buffer
6. it joins locally stored last-trade timestamps from `projectx_trade_events`
7. it returns a frontend-friendly account list

This means the accounts endpoint is both a read endpoint and the main account-state reconciliation step.

### 2. Trade Sync Flow

#### Initial sync

If an account has no local trade data and the app requests summary, trades, or calendar data, the backend can backfill history from:

- `now - PROJECTX_INITIAL_LOOKBACK_DAYS`
- up to the requested end time or current time

#### Incremental sync

If local history already exists and the request does not specify a custom start:

- the backend checks the earliest and latest local timestamps
- it may backfill older history if the local earliest timestamp is newer than the configured lookback floor
- it always adds an incremental sync window from `latest_local - 5 minutes` to `now`
- it refreshes a recent trailing window controlled by `PROJECTX_RECENT_REFRESH_DAYS` so late provider changes can fill in updated PnL, fees, or lifecycle fields

That five-minute overlap makes ingestion more robust around provider timing drift and duplicate delivery.

#### Chunking and deduplication

Trade history requests are chunked by `PROJECTX_SYNC_CHUNK_DAYS` and paged by `PROJECTX_DAY_SYNC_LIMIT`. Ingested events are deduplicated by:

- `(user_id, account_id, source_trade_id)` when the provider gives a stable execution ID
- otherwise `(user_id, account_id, order_id, trade_timestamp)`

Voided or canceled provider rows are ignored. Existing local rows can be updated when ProjectX later returns completed PnL, fee, or lifecycle fields for rows that were previously incomplete.

#### Single-day cache behavior

For single-day trade-range requests, TopSignal uses `projectx_trade_day_syncs` to decide whether to re-sync:

- today: normal dashboard reads use the local cache; explicit sync refreshes from provider
- yesterday: refresh only if missing, partial, stale, or explicitly requested
- older days: use the local cache when the day was previously marked `complete`, unless explicitly refreshed

Repeated or truncated provider pages keep the day marked `partial` rather than `complete`, which lets later sync attempts repair the day. This keeps normal navigation cheap while still handling late-arriving fills around today and yesterday.

### 3. Trade Analytics Flow

Trade analytics are derived from normalized execution events.

Key rules in code:

- rows with `pnl = null` are treated as open-leg or half-turn events and do not count as closed trades
- open-leg rows also do not reduce net PnL through fees in the summary logic
- trading-day grouping uses a New York trading session boundary of `6:00 PM ET -> 5:59:59 PM ET next day`
- entry and exit timing for the trade feed is inferred from execution history rather than stored directly by the provider

The backend computes summaries from `projectx_trade_events`, then the frontend computes several additional display-only metrics from the returned summary and trade feed.

### 4. Journal Data Flow

Journal entries are keyed by `(user_id, account_id, entry_date)`.

Typical journal workflow:

1. frontend creates or loads an entry for a specific trading date
2. the user edits title, mood, tags, and notes
3. a debounced autosave queue sends `PATCH` requests after `800ms`
4. the backend validates the expected `version`
5. on success, the entry version increments
6. on conflict, the API returns `409` with the server copy

Image flow:

1. the user pastes an image into the editor
2. frontend uploads it
3. backend validates size and MIME type
4. backend stores the file locally or in Supabase Storage
5. backend persists a `journal_entry_images` row and returns a backend-served URL path

Trade-stat snapshot flow:

1. the user asks to pull stats into a journal entry
2. the backend optionally refreshes the relevant trade window from ProjectX first
3. it computes a snapshot from closed trades in the selected window
4. it stores that snapshot in `journal_entries.stats_json`

AI Recap flow:

1. the user clicks AI Recap for the selected account and trading date on the Journal page
2. the frontend flushes pending journal autosave work, then calls the account-scoped AI recap endpoint
3. the backend loads closed trades for that account and trading date
4. if the day has no trades, the backend skips recap generation and does not create or update a journal entry
5. if trades exist, the backend calls Gemini using the existing `GEMINI_*` backend environment variables listed in this README
6. the recap is created as a new journal entry or appended to the existing entry as a managed AI recap section

Journal merge flow:

1. the user chooses an old account and a new account from the Accounts page
2. the frontend submits `POST /api/journal/merge` with `skip` or `overwrite`
3. the backend validates that both accounts belong to the current user
4. it copies source entries into the destination account by `entry_date`
5. if `include_images=true`, it copies image files and creates new `journal_entry_images` rows for the destination entry
6. it returns a merge summary with transferred, skipped, overwritten, and copied-image counts

### 5. Expense And Payout Flow

Expenses are CRUD records in the `expenses` table. Totals are aggregated server-side by date range, category, and account.

Payouts are stored separately in the `payouts` table and summarized through payout-specific endpoints.

The Expenses page combines those two tables into cash-flow summaries:

- recorded spend
- spend since the latest recorded payout
- net after payouts
- payout-minus-spend ranges for fixed windows, anniversary years, and all time

The combine spend helper is separate from core expense storage:

- it lives in browser storage
- it infers combine purchases from active account names
- it can create missing evaluation-fee rows in the backend
- it reconciles account-inferred purchases with expense-derived purchases
- it uses `source_id` and tags to avoid duplicate imported or generated combine rows

### 6. Bot Flow

Bot configurations are user-owned records tied to a ProjectX account and contract.

Typical bot workflow:

1. the frontend loads selectable accounts and `GET /api/bots`
2. the user searches ProjectX contracts and saves a named bot configuration
3. the Signal Chart requests ProjectX candles for the bot contract and selected chart timeframe
4. `POST /api/bots/{id}/backtests` replays a bounded range of already stored, closed non-live candles and persists the reproducibility and result snapshots
5. `POST /api/bots/{id}/evaluate` computes one selected-strategy decision in dry-run mode, persists it, and returns market analysis plus optional trade-plan evaluation
6. `POST /api/bots/{id}/start` creates a run, evaluates the selected strategy once, and records any dry-run attempt, live submission, duplicate skip, risk block, or error
7. `POST /api/bots/{id}/stop` stops the latest running bot run
8. `GET /api/bots/{id}/activity` returns recent runs, decisions, order attempts, and risk events for the activity tables

Risk checks can block execution for disabled bots, non-active accounts, disallowed contracts, stale data, daily trade limits, session windows, position limits, cooldowns, and daily loss constraints.

An order-attempt row in `pending` means the durable local claim was written before provider routing, but no final provider result was committed locally. This state is intentionally not auto-retried: the provider may have accepted the order before a process or database failure made the outcome ambiguous. Reconcile the deterministic provider custom tag and the local correlation/idempotency identifiers against ProjectX before taking any manual recovery action. No background retry or reconciliation worker currently exists.

Trade-plan evaluation is also exposed directly through `POST /api/trade-plan/evaluate`. Legacy fields such as `score`, `total_score`, `category_scores`, `risk_reward_ratio`, and the three `*_probability` analysis keys remain available for compatibility. New consumers should use the scoring-model version, category maximum/award maps, cap and penalty records, missing-input list, driver lists, `r_multiple`, and `scenario_weights` fields.

ProjectX market-data reads have two refresh modes:

- `refresh=true` forces a provider read for the requested edge of the chart window
- `repair=true` forces a full-window fetch so interior candle gaps can be filled

If a provider fetch fails but cached candles cover the request, the backend can return cached candles as a fallback. The chart also uses `/api/projectx/market-price/stream` when the optional streaming runtime is enabled, while keeping REST candles as the canonical closed-bar source.

### 7. Frontend Caching

The frontend has small in-memory caches in `frontend/src/lib/api.ts`:

- account lists: cached for 10 minutes
- account-scoped summary, trades, and PnL-calendar reads: cached for 10 minutes
- journal day markers: cached per account and query
- bot chart candles: cached in browser storage by market and timeframe
- duplicate in-flight requests are deduplicated

Mutation calls invalidate affected cache entries.

## Local Development

### Prerequisites

- Node.js 20+
- Python 3.11+
- npm
- PostgreSQL 16 locally or a hosted PostgreSQL/Supabase database
- Docker, if you want the included local Postgres container

### Fastest Local Setup

The simplest path is:

1. start the local Postgres container
2. create backend and frontend env files
3. install backend and frontend dependencies
4. apply the schema
5. run the root dev command

#### 1. Start PostgreSQL

```powershell
docker compose up -d db
```

#### 2. Create env files

Backend env file: `backend/.env`

Frontend env file: `frontend/.env.local`

Recommended minimum backend variables for local anonymous mode:

```dotenv
DATABASE_URL=postgresql+psycopg://topsignal:topsignal_password@127.0.0.1:5432/topsignal
PROJECTX_API_BASE_URL=https://api.topstepx.com
PROJECTX_USERNAME=your_topstepx_username
PROJECTX_API_KEY=your_topstepx_api_key
AUTH_REQUIRED=false
```

Recommended minimum frontend variables:

```dotenv
VITE_API_BASE_URL=http://localhost:8000
```

If you want authenticated cloud mode, also set the Supabase variables shown in `.env.example`.
When `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are configured, the frontend
bootstraps a Supabase session before rendering the routed app. If there is no active
session, it shows a Google OAuth sign-in screen first.

#### 3. Install dependencies

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
npm install
npm --prefix frontend install
```

#### 4. Apply the database schema

```powershell
Get-Content .\db\schema.sql | docker exec -i topsignal_db psql -U topsignal -d topsignal
```

#### 5. Run the app

```powershell
npm run dev
```

That starts:

- backend on `http://localhost:8000`
- frontend on `http://localhost:5173`

`npm run dev` runs a small supervisor that prefixes backend/frontend logs, restarts processes that exit early during startup a limited number of times, and stops the sibling process if one side exits permanently.

`npm run dev:backend` loads `backend/.env` before starting Uvicorn and defaults `TOPSIGNAL_DB_SCHEMA_INIT=skip` for faster startup. On Windows, the wrapper manages reload itself to avoid Uvicorn reload control-event issues; set `TOPSIGNAL_DEV_BACKEND_UVICORN_RELOAD=1` to force Uvicorn's native reload there.

### Environment Variables

The repo-level `.env.example` is the source of truth for starter env profiles. Important variables include:

#### Backend variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database connection URL |
| `PROJECTX_API_BASE_URL` | Base URL for ProjectX API |
| `PROJECTX_USERNAME` | Legacy env-based TopstepX username used for ProjectX API auth |
| `PROJECTX_API_KEY` | Legacy env-based TopstepX API key generated from `Settings -> API` |
| `AUTH_REQUIRED` | Forces API auth on or off |
| `SUPABASE_URL` | Enables Supabase-aware auth and optional storage |
| `SUPABASE_JWKS_URL` | Custom JWKS endpoint for JWT validation |
| `SUPABASE_JWT_ISSUER` | Expected JWT issuer |
| `SUPABASE_JWT_AUDIENCE` | Expected JWT audience |
| `SUPABASE_JWT_SECRET` | Shared secret for local HS-signed tokens |
| `CREDENTIALS_ENCRYPTION_KEY` | Fernet key for encrypting stored provider credentials |
| `ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS` | Allows env credentials as fallback in authenticated deployments |
| `ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY` | Allows local-only encryption-key fallback |
| `PROJECTX_INITIAL_LOOKBACK_DAYS` | First-sync history window |
| `PROJECTX_RECENT_REFRESH_DAYS` | Recent trailing sync window used to catch late provider updates |
| `PROJECTX_SYNC_CHUNK_DAYS` | Trade-sync chunk size |
| `PROJECTX_DAY_SYNC_LIMIT` | Per-page trade-day fetch limit |
| `PROJECTX_YESTERDAY_REFRESH_MINUTES` | Staleness threshold for yesterday refresh |
| `PROJECTX_ACCOUNT_MISSING_BUFFER_SECONDS` | Delay before absent accounts become `MISSING` |
| `PROJECTX_LAST_TRADE_LOOKBACK_DAYS` | Provider lookback for last-trade resolution |
| `GEMINI_API_KEY` | Server-side Gemini API key used by AI journal recap generation |
| `GEMINI_MODEL` | Gemini model for AI journal recap generation; defaults to `gemini-3.1-flash-lite` |
| `GEMINI_API_BASE_URL` | Optional Gemini API base URL override |
| `GEMINI_TIMEOUT_SECONDS` | Optional Gemini request timeout override |
| `GEMINI_RETRY_ATTEMPTS` | Optional total attempts for retryable Gemini HTTP errors; defaults to `3` |
| `GEMINI_RETRY_BACKOFF_SECONDS` | Optional base delay for Gemini retries; defaults to `0.75` |
| `ALLOWED_ORIGINS` | Exact CORS allowlist |
| `ALLOWED_ORIGIN_REGEX` | Regex-based CORS allowlist |
| `ALLOW_QUERY_BEARER_TOKENS` | Allows `access_token` query param auth for special cases |
| `TOPSIGNAL_DB_SCHEMA_INIT` | `full` runs startup schema compatibility patches; `skip` bypasses them for faster dev startup |
| `TOPSIGNAL_LIVE_EXECUTION_ENABLED` | Enables one server-side live-routing gate when set to a true value; defaults disabled, is never sufficient by itself, and is ignored in tests |
| `TOPSIGNAL_DEV_BACKEND_UVICORN_RELOAD` | On Windows, set to `1` to use Uvicorn's native reload instead of wrapper-managed backend reload |
| `JOURNAL_IMAGE_STORAGE_BACKEND` | `local` or `supabase` |
| `JOURNAL_IMAGE_STORAGE_DIR` | Local journal image directory |
| `SUPABASE_STORAGE_BUCKET` | Storage bucket for journal images |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-side key for Supabase Storage operations |
| `PROJECTX_STREAMING_ENABLED` | Enables optional streaming runtime |
| `PROJECTX_MARKET_HUB_URL` | Market SignalR/websocket hub URL |
| `PROJECTX_USER_HUB_URL` | User SignalR/websocket hub URL |
| `PROJECTX_MARKET_HUB_SUBSCRIBE_MESSAGE` | Optional custom subscription payload |
| `PROJECTX_USER_HUB_SUBSCRIBE_MESSAGE` | Optional custom subscription payload |

#### Frontend variables

| Variable | Purpose |
| --- | --- |
| `VITE_API_BASE_URL` | Backend base URL |
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase anon key |
| `VITE_PERF_LOGS` | Enable frontend API perf logging |

Frontend auth behavior:

- If both `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are present, the app treats Supabase auth as enabled.
- With Supabase auth enabled, the app requires Supabase session bootstrap before the routed app is shown.
- If session bootstrap does not find an active session, the user sees a Google OAuth sign-in screen.
- Google must be enabled in Supabase Auth providers for sign-in to work.

### Common Commands

| Command | Purpose |
| --- | --- |
| `npm run db:init` | Run backend schema compatibility initialization explicitly |
| `npm run dev` | Run backend and frontend together |
| `npm run dev:backend` | Run backend dev script |
| `npm run dev:frontend` | Run frontend dev script |
| `npm --prefix frontend run build` | Production frontend build |
| `npm --prefix frontend run lint` | Frontend lint |
| `npm --prefix frontend run test` | Frontend tests |
| `cd backend && .venv/bin/python -m pytest tests` | Backend tests (macOS/Linux) |
| `cd backend; .venv\Scripts\python -m pytest tests` | Backend tests (Windows PowerShell) |

## Current Limitations

- The main routed app is strong around dashboard, trades, journal, expenses, payouts, themes, and bot dry-run workflows.
- There is backend support for per-user ProjectX credentials, but there is no dedicated frontend credentials-management screen in the current routed UI.
- The repository still carries the legacy `trades` table and `/metrics/*` routes beside the newer `projectx_trade_events` pipeline.
- The accounts endpoint performs provider sync inline, which can make the first load noticeably slower on large account sets.
- The optional streaming lifecycle runtime persists position data, but the current UI does not expose those records directly.
- The bot page exposes dry-run start/evaluate/stop controls, while live order routing remains backend-gated and intentionally absent from the current UI.
- TopBot has no continuous evaluation worker, and ambiguous `pending` attempts require provider reconciliation rather than automatic retry.
- There is no formal migration runner; schema evolution relies on raw SQL plus startup compatibility helpers.
- Expense combine tracking is partly client-side, so it is not a fully server-authoritative accounting subsystem.

## Documentation Map

- `db.md`: database design, persistence model, and trade-ingestion notes
- `db/README.md`: fresh schema and migration application instructions
- `docs/perf-notes.md`: dashboard and accounts performance findings and fixes
- `frontend/README.md`: frontend architecture, routes, API client behavior, and local frontend development

## Summary

TopSignal is best understood as a local-first trading intelligence layer on top of ProjectX:

- ProjectX is the upstream data source
- PostgreSQL is the local analytics cache and journal/expense store
- FastAPI is the normalization and metrics layer
- React is the trader-facing review workspace

If you are onboarding to the codebase, start with the dashboard flow and trace it through:

- `frontend/src/pages/dashboard/DashboardPage.tsx`
- `frontend/src/lib/api.ts`
- `backend/app/main.py`
- `backend/app/services/projectx_trades.py`
- `backend/app/services/projectx_metrics.py`

That path shows most of the application's real architecture.
