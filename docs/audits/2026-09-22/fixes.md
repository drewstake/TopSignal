# Audit remediation — 2026-09-22

The user authorized fixing the findings after the audit. These changes are local and uncommitted. No broker action, production write, migration, archive restore or retention change was performed.

| Finding | Correction | Verification |
|---|---|---|
| AUD-01 | Reload authoritative accounts after import, update the shell, and withhold balance reconstruction until the account and analytics refresh finish. An account switch invalidates the old refresh; a balance failure gives an explicit recovery message. | Integration tests with delayed account reads and mid-refresh account switching; real isolated UI import |
| AUD-02 | Undefined profit factor is JSON null; ProjectX summaries include an explicit gross no-loss flag. Baseline, Compact, Trades, stats exports and coaching use a shared display/comparison helper. Empty/breakeven-only, winning-only, losing-only, and fee-induced net-loss cases are distinguished. | API and pure calculation regressions; frontend helper and coaching regressions; UI ∞ with no negative-edge claim |
| AUD-03 | Bound database IDs and query offsets before binding SQL; related financial mutations and owned-account reads share validation. | Authenticated oversized query tests and normal-invalid boundary checks |
| AUD-04 | Normalize datetime filters before comparison and query construction. Naive values retain the existing UTC interpretation. | Equal-instant offsets, mixed aware/naive, DST fall-back and inverted instant tests |
| AUD-05 | Validate supported dates before timezone/calendar arithmetic, including financial create/update schemas and analytics time filters. | Minimum/maximum supported dates, leap day and out-of-domain HTTP tests |
| AUD-06 | New financial records require USD. Existing non-USD rows cause a clear 409 for affected totals, rather than silent addition. Ledger rows display their recorded currency. No existing record is converted or deleted. | Legacy EUR fixture tests across expense/payout/combined totals, including tenant isolation and unchanged records |
| AUD-07 | Add Expense now has fee presets and a general expense/refund path exposing all six categories. Entered refund amounts become negative expenses; zero refunds are rejected. | Four newly accessible categories tested; real refund saved and reloaded |
| AUD-08 | Corrected Stop Automation test wording while retaining stop-versus-emergency behavior assertions. | Full frontend suite |
| Connection errors | Explain service/connectivity recovery; failed period summaries show Unavailable instead of Loading. | Failure then Refresh recovery regression |
| Node test runtime | The standard npm test launcher disables experimental Node WebStorage in both Vitest and its workers when supported. jsdom retains real Storage/StorageEvent behavior. | Standard frontend test command on Node 25.9.0 |
| Additional test reliability | The chart session timer simulation has a 15-second per-case allowance to avoid timing out mid-act under parallel test-worker contention. Assertions and fake-clock advances are unchanged. | Isolated chart suite and full frontend suite |

## API compatibility notes

- `profit_factor` may be null when its denominator is zero; clients must not coerce null to zero. ProjectX `profit_factor_no_losses` identifies the unbounded gross-profit case, independently of net win/loss counts. Infinity is never serialized in API JSON.
- IDs must fit a positive signed 64-bit integer; offsets are 0 through 2,147,483,647. Existing ordinary negative/invalid values remain client errors.
- Financial dates and analytics filter dates support 0002-01-01 through 9998-12-31, leaving room for trailing-year and timezone arithmetic. Out-of-range requests fail with 4xx.
- Expense/payout creates accept only `USD`. Legacy unsupported currencies are preserved and must be reviewed before aggregate totals can be shown. The financial-summary query count remains two grouped reads for valid data.

## Manual verification

Used the same isolated SQLite workspace on API 8015 / UI 5185, with no credentials or outbound provider calls.

1. The pre-existing winning fixture now shows Profit factor ∞; Summary has the low-sample message and no Negative edge claim.
2. Imported a second distinct synthetic CSV row for +$198.78. Without a page reload, the path kept its $10,000 starting balance and updated its high to $10,397.56 with +$397.56 net P&L.
3. Saved a $12.34 refund through the new general entry form. The ledger displayed -$12.34, recorded spend fell from $49.99 to $37.65, and net cash flow rose from $50.02 to $62.36. Reload preserved the totals.

## Scope of performance work

The audit measured approximately linear analytics CPU cost and listed optimization candidates, rather than establishing a performance defect. No speculative caching or historical persistence change was introduced. Large-account end-to-end profiling, disposable PostgreSQL, external provider, real-authentication and Windows coverage limits remain as documented in the original audit.

## Final checks

- Backend: **2,150 passed, 9 skipped** (44.92 s). Skips still require disposable PostgreSQL or the optional market capture. The offline guard observed no external connection attempts.
- Frontend: **980 passed across 117 files** (21.99 s), using the standard test command without a caller-supplied Node workaround.
- Production build: **passed** (5.00 s); TypeScript checks passed.
- ESLint: **passed**.
- Dev-script tests: **35 passed, 5 Windows-only skipped**.
- `git diff --check`: **passed**.
- Audit probes are now normal regressions in `backend/tests/test_audit_regressions.py`; no expected-failure markers remain there.

The temporary verification browser tab and API/UI processes were closed. The isolated fixture database remains under `/tmp/topsignal-audit-20260922` for review. The user's connected application and records were not used for mutations.
