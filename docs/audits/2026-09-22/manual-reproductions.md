# Manual exercise record

Actual UI at `http://127.0.0.1:5185`, API 8015, isolated SQLite. Computer-use accessibility state and screenshots were inspected during execution; no claim is made that screenshots are stored in this folder. Values below are fixture-only.

| Workflow | Actions and observations | Status |
|---|---|---|
| Empty app | Six active routes; no-account picker disabled; Accounts refresh explains missing ProjectX credentials | Passed |
| CSV account | Negative initial balance blocked; $10,000 accepted; literal `<script>alert(1)</script>` name escaped | Passed |
| First import | Repository UTF-8 CSV preview: 1 new, 0 duplicate, net $198.78; confirm produced one trade | Passed |
| Balance refresh | Before reload start $9,801.22/high $10,000 despite API balance $10,198.78; reload repaired display | Failed AUD-01 |
| Duplicate import | Same file recognized as 1 duplicate; nothing new added; still one trade after reload | Passed |
| Malformed import | CSV `unexpected,columns` / `not,a-trade` rejected with required-column list; subsequent valid upload recognized duplicate | Passed |
| Profit factor | Baseline and Trades PF 0.00 for 100% winning sample; compact shows infinity; Summary says Negative edge despite positive expectancy | Failed AUD-02 |
| Dashboard filters | 1D, 1W, 1M, 6M, All state changes; Demo calendar day selected 2 trades/$169.96 | Passed observed state changes |
| Dashboard details | Expanded all nine metric cards; Summary opened and Escape closed; Copy Full Stats returned 3,191 fixture characters | Passed |
| Compact/mobile | Daily and cumulative chart switch; 390x844 viewport screenshot; viewport/body/document widths all 390 | Passed; other mobile pages not fully exercised |
| Accounts | Rename saved/reloaded; only-Main archive blocked; synthetic second account made Main; CSV archive hid it, Show archived revealed it, restore preserved $10,198.78 | Passed |
| Account display | Show hidden/missing/archived switches; stale provider metadata explicitly labeled | Passed observed states |
| Trades | Imported row present; INVALID symbol yielded no matches; clear restored; row-limit control 1,000 selected | Passed |
| Expenses | Negative non-refund amount blocked; $49.99 expense and $100.01 payout saved; net $50.02 | Passed |
| Expense categories | Add dialog only Evaluation/Activation; ledger filter includes Reset/Data/Other/Refund | Capability gap AUD-07 |
| Ledger filters | Refund filter gave zero expenses; January calendar selected zero expenses and payouts; all-recorded overview remained $50.02; Clear month restored row | Passed |
| Backend loss | Stopped only audit API, Refresh showed Unavailable/Failed to fetch; restarted same SQLite API, Refresh restored $49.99/$100.01/$50.02 and payout row | Passed recovery; error wording improvement |
| CSV Bot | Dry/Live/Stop disabled; chart load shows missing credentials and offers Retry | Passed guard/error presentation |
| Demo Bot | Mutation controls disabled; 749 sample candles; 1m/5m/15m/1H/4H/1D timeframes; EMA9/21,VWAP,volume,buy/sell,liquidity overlays toggled | Passed demo UI only |
| Chart tools | Compute liquidity, Fit Y axis, line tool with two chart points, Cursor; Clear drawings enabled after full reload | Passed line persistence; other tools untested |
| Themes | All 12 palettes selected and checked; keyboard Right moved Daylight to Market; reload retained Market | Passed |
| Retired routes | `/journal?account=...` and `/data?account=...` redirected to dashboard preserving query | Passed |
| Copy Trade Mode | CSV correctly disables; synthetic ProjectX toggle shows analytics-only confirmation; subsequent UI/dialog operations repeatedly time out | Blocked; acceptance and final state unknown |

Browser-control failure was reproducible on state reads after the copy-mode dialog; `getJsDialog()` returned undefined and documented recovery did not restore control. Closing the audit tab also timed out. Browser viewport reset succeeded. No real browser tab or production server was stopped. Remaining controls are explicitly listed in coverage.md rather than marked passed by inference.

To restart the manual sandbox, run the launcher in two terminals with modes `backend` and `frontend`. Its existing local fixture database is intentionally retained in `/tmp/topsignal-audit-20260922` for review; it is not a production backup and may be removed by normal OS temporary-file cleanup. The fixture CSV is already versioned in backend/tests/fixtures. No historical archive is needed.
