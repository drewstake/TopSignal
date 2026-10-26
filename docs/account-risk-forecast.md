# Account Risk Forecast

Open **Dashboard → Account Risk Forecast → Configure forecast**. The panel is
available in both dashboard layouts, scoped to the selected account. It loads
data only when opened and is unavailable in Demo mode.

Choose recorded account trades, one of the latest 20 saved account backtests,
or a completed temporary TopBot historical run owned by the signed-in user.
Temporary runs are strategy scenarios applied to the selected account's limits;
they are not treated as that account's executed history. They expire with the
existing backtest job retention (one hour or a backend restart). Refresh sources
after completing a new backtest. Sources are never pooled together.

Confirm the starting balance and enter the **remaining account loss buffer**:
current balance minus the current account failure floor. The nominal account
size is not the remaining buffer. Limits are scenario inputs; the panel does
not infer provider rules from account names or change any bot configuration.

## Inputs and outputs

- Forecast horizon: 1–60 **active trading days**, default 20.
- Recorded-trade lookback: 90 days through 10 years, default one year.
  Backtest sources use their complete recorded trade list.
- Fixed account floor, end-of-day trailing floor, or a floor updated after each
  recorded trade close. Optional floor cap is the maximum absolute balance
  level the floor may reach, not a maximum drawdown amount.
- Optional daily loss limit: stops the simulated day at the first closed trade
  crossing the limit; resets daily. The crossing trade's entire loss is retained.
- Optional added cost per closed trade, on top of the source's existing costs.
- Three exposure scenarios: 0.5×, 1× and 1.5× historical exposure. These scale
  net outcomes and costs linearly; they are not whole-contract orders.

The panel reports account-floor and daily-stop hit frequencies, drawdown
percentiles, ending-balance percentiles, and a median/middle-80% balance chart.
Account failure stops the simulated path, including subsequent recovery.
Changing an input clears the old forecast; changing accounts aborts requests
and resets all account-specific inputs and results.

## Method and limits

`account-risk-session-blocks-v1` draws 2,000 paths per exposure scenario from
overlapping blocks of three consecutive observed active days. It preserves
within-day trade order and short clusters of active days. Every exposure
scenario uses the same sampled blocks for comparability. The seed is derived
from a SHA-256 digest of the ordered source outcomes, making identical requests
reproducible. Blocks never wrap the last historical day onto the first.

Only completed trading days are included, using the existing 18:00 New York
rollover and imported trade-date handling. Open-leg and voided provider events
are excluded. Recorded trades use the same round-turn fee and commission
treatment as dashboard analytics. Backtest trade counts and net P&L must
reconcile with the saved summary; summary-only or truncated results are rejected.

At least 20 active days and 30 closed trades are required. Fewer than 60 days,
old source data, and sources without losing trades receive additional warnings.
Missing or non-finite outcomes fail validation instead of becoming zero returns.

This is a conditional historical scenario analysis, not a calibrated forecast
of future market performance. Inactive or missing sessions are not synthesized.
It does not model unrealized intratrade equity, actual liquidation, deposits,
withdrawals, payout rules, cross-account copying, or unseen loss regimes.
Historical entries already reflect their original sizing and risk controls.
All limit crossings are checked at recorded closes. Trade-close trailing is
explicitly not continuous intraday broker trailing.

The chart bands are path percentiles. The reported 95% Wilson intervals measure
Monte Carlo sampling noise only; they do not account for uncertainty in the
historical sample or future market conditions. Zero simulated failures never
imply zero risk.

## API and storage

- `GET /api/accounts/{account_id}/risk-forecast/sources`
- `POST /api/accounts/{account_id}/risk-forecast`

Both routes enforce authenticated user and account ownership. Saved backtests
must also match the selected account. Temporary jobs must match the user and
be completed and unexpired. Requests are bounded to 50,000 source trades,
2,000 closes per source day, and 60 forecast days, with one concurrent forecast
per backend process. Working matrices are limited to 128 paths at a time.

Forecasts perform no database writes, imports, provider synchronization, order
calls, model promotion or migrations. Input candles are not loaded. Historical
caches remain on the backend filesystem under the existing Supabase storage
policy. Responses contain only bounded summaries and at most 61 chart points
per scenario; no source trade list or input candle series is returned.

Validation covers numerical agreement with a sequential account ledger,
threshold equality, daily resets, absorbing account failure, trailing caps,
costs and exposure scaling, insufficient history, source ownership/expiry,
fee reconciliation, and UI account-switch and stale-response behavior.
