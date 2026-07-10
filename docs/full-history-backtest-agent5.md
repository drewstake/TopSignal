# Full-history backtest integration boundary

## Integration base

- Feature branch: `feature/full-history-backtest`
- Preserved parent: `74cc070` (`Integrate bot performance agent work for review`)
- Agent 5 should branch from the final feature commit communicated after verification, not from `74cc070` directly.

## Shared interfaces and assumptions

- `BotBacktestIn.start` and `.end` are an optional pair. Omitting both requests full history; supplying both preserves bounded compatibility and test coverage. Supplying only one is invalid.
- `create_bot_backtest(..., client: ProjectXClient | None = None, now: datetime | None = None)` is the single service entrypoint. `now` captures one deterministic closure boundary for tests and the whole request.
- The primary replay query is always scoped to user, non-live data, the configured `contract_id`, and the configured timeframe. It must never resolve, roll, or merge futures deliveries.
- Full-history resolution excludes explicit and inferred partial rows, then uses the earliest eligible stored bar through the latest bar whose nominal close is at or before the captured boundary.
- TopBot full-history requests first discover and persist the exact configured primary delivery, paging backward and forward until provider exhaustion; empty and stale caches cannot define the reported range. Provider failures propagate instead of masquerading as complete cached history.
- `run_backtest` remains provider-free and order-routing-free. TopBot data acquisition happens before it, inside the same public POST request.
- Successful results report actual execution coverage in `range`: `contract_id`, `symbol`, `timeframe_unit`, `timeframe_unit_number`, `start`, `end`, and `bar_count`.
- Fixed 366-day and 20,000 execution-bar caps were removed. No successful result may be a limited prefix; later resource controls must either process the complete resolved input or fail explicitly before persistence.

## Ownership boundary for Agent 5

Build upon these feature-owned seams in `backend/app/services/bot_backtesting.py`:

- `_ResolvedBacktestWindow`, `_load_primary_closed_candles`, `_requested_backtest_bounds`, `_resolve_backtest_window`, and the exact-primary discovery helpers
- Public orchestration in `prepare_bot_backtest_data` and `create_bot_backtest`
- Actual range provenance returned by `BacktestEngine.run`
- `_validate_settings` range policy and `_assumptions_snapshot` market-data wording

Avoid changing those public contracts or response field names without coordinating first. Agent 5 owns follow-on performance work inside replay/preparation/query internals, measured resource controls, pagination or batching, and equivalence/resource-safety tests. Preserve exact-contract filtering, the single captured closure boundary, the one-call TopBot workflow, and fail-before-persist semantics.

Frontend consumers depend on a single `POST /api/bots/{id}/backtests`; `/backtests/prepare` is intentionally not public.
