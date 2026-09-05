# Legacy MNQ variants: fixed fee-only reconsideration

This plan rechecks the four original variants from
`backend/tools/compare_topbot_variants.py`: baseline v4, bracket-only exits,
trend alignment, and no-chase. Their evaluator and rules are unchanged. The
earlier rejection of a variant at $1.20 per side is not treated as a result at
the corrected $0.61 base fee. Read the
[fee correction](topbot-fee-correction.md) and
[original comparison](topbot-improvement-comparison.md) for that distinction.

## Fixed matrix and interpretation

There are **48 cases**: four variants, three periods, two slippage settings,
and two explicit fees. Each period has sixteen cases and can run in a separate
process against the same prepared snapshot.

- Selection: January 1, 2020 through January 1, 2024 exclusive.
- Diagnostic: January 1, 2024 through the original cache end, July 10, 2026.
- Full: all original cached history, including sparse 2019 launch coverage.
- Fees: $1.20 and $0.61 per contract per side, both supplied explicitly.
- Slippage: one and two ticks adversely on each fill.
- One MNQ, $50,000 starting balance, original entry/session/50-point bracket
  rules, and the comparison tool's unchanged account-risk settings.

Each case starts a fresh comparison engine. Only the fee differs within each
matched old/new pair. Lower fees can change risk-gate decisions and subsequent
trades, so neither a refund calculation nor forced ledger equality is appropriate.
No parameter values or rules are selected from interim results.

These are retrospective comparisons on already examined data. They use the
**frozen current comparison tool's application-style five-minute replay** and
the verified calendar-v6 cache; they do not add the separate observed-minute
execution path. The paired $1.20 control consequently isolates the fee change
within this captured engine/calendar state, rather than promising exact identity
with every older published experiment. Legacy aggregate execution and live
parity limitations remain. No quarantined newer data is permitted.

## Prepared execution and full ledgers

`backend/tools/research_legacy_fees.py prepare` creates a unique directory with
an immutable pre-run manifest, the entire app Python source tree, the unmodified
comparison tool, unchanged v4 fixture, this driver, and the relevant fee/legacy
documents. It verifies the captured source bytes and pins the format-6 manifest,
all ten five-minute NPY arrays and their metadata, and both source archives.
Preparing the experiment reads files and hashes only; it does not import the
replay engine or evaluate market data.

Execution uses the captured driver and captured app modules. It validates
source/data hashes before every case and runs the frozen comparison tool with
one explicit variant, fee and slippage setting. An in-memory wrapper observes
`BacktestEngine.run`, writes its unmodified complete result and full trade
ledger, and returns the original object. It checks the saved ledger against the
comparison tool's own hash. No existing engine, comparison tool, fixture, or
historical report is edited.

Captured assumptions must match the explicit case fee and slippage. Every trade
must retain one contract, the matching round-trip fee, and consistent net/gross
arithmetic. Validation failures retain the full offending result and ledger,
an error message, and a traceback in the case's isolated `failure.log`.

Every case gets a unique directory with `started.json`, `comparison.json`,
`replay.json`, `trades.json`, `stdout.log`, and `completed.json` or `failed.json`.
Creation refuses overwrites. A per-period exclusive start file prevents duplicate
workers for the same period, while allowing the three different periods to run
concurrently. Network access is disabled in workers; provider variables are
removed from their environment, dotenv is disabled, and SQLAlchemy is restricted
to an in-memory SQLite URL. No services, account data, credentials, or bot runs
are required.

Preparation command from the repository root:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_legacy_fees.py prepare --label legacy-fee-only
```

After reviewing the returned manifest, use its exact directory for each worker:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_legacy_fees.py run --prepared-dir $preparedLegacyFees --period selection
backend/.venv/Scripts/python.exe backend/tools/research_legacy_fees.py run --prepared-dir $preparedLegacyFees --period diagnostic
backend/.venv/Scripts/python.exe backend/tools/research_legacy_fees.py run --prepared-dir $preparedLegacyFees --period full
```

The rates are deliberately fixed to both 1.20 and 0.61 in the pre-run case
manifest; the driver passes the explicit existing `--commission-per-side` option
to the comparison tool for each case. Planning estimate: approximately 15–25
minutes sequentially or 8–15 minutes with three period workers, depending on
contention. This is an estimate, not a measured run. No legacy fee case has been
launched by preparation.

Seven focused offline tests passed using synthetic engine results only. They
verify preservation of the complete returned result and ledger, restoration of
the original engine method after success/failure, refusal to overwrite an
existing case, rejection of changed frozen source/cache bytes, and refusal to
execute from the live working-tree driver. The offline guard recorded zero
external connection attempts. Python compilation also passed. These checks
validate the capture/isolation machinery, not any strategy outcome.
Both explicit fee rates are covered, along with rejection and preservation of
an incorrect round-trip ledger fee and a diagnosable synthetic engine failure.

## Initial prepared snapshot, superseded before execution

Prepared without a replay at `2026-09-05T01:01:32Z`:

```text
backend/storage/research/legacy-fee-pairs/20260905T010132.241259Z-legacy-fee-only-e4531a2c6050
```

Manifest SHA-256:
`9b2a1ddca2e46c3d2b22a1f550a4dd327183ac31614385eeaaff6233e64401cd`.
Source-bundle SHA-256:
`47c709a98a9ede94c7a32d948f4683186c24cb3fcaa4a62243d1c2b36fbcb00b`.
An independent load of the captured driver verified **61 source files and
15 cache/archive files**, all 48 fixed cases, sixteen cases per period, and
both explicit rates. At that check, there were zero worker-start artifacts and
zero case directories. This document's later registration paragraph is not
part of the already captured plan snapshot.

The original comparison tool differs from its Git baseline only in the fee
constant import, explicit fee option/validation, and plumbing that option into
settings/reporting. Candidate rule definitions and evaluator transformations
are unchanged. The prepared wrapper does not modify that source.

## Ready snapshot with failure diagnostics and fee reconciliation

The initial preparation above was preserved and superseded before any case
execution. The replacement adds error messages/tracebacks and per-trade fee
reconciliation, without changing any strategy, engine, costs, or case definitions.
All seven synthetic tests pass. Use this prepared directory for the three worker
commands:

```powershell
$preparedLegacyFees = 'C:\Users\drews\Development\TopSignal\backend\storage\research\legacy-fee-pairs\20260905T010308.671709Z-legacy-fee-only-v2-da347c739699'
```

Manifest SHA-256:
`56a2c8cebdf435d2dc98c5f2e33fb1f910bdf52d95a7e77836de47f3a4fed85d`.
Source-bundle SHA-256:
`4afbd8bfe94749de80127c7f971f629171c485eab1b627b31fbace31a1f0af51`.
Independent verification using the replacement's captured driver again passed
all **61 source-file and 15 cache/archive-file hashes**. The matrix contains
48 cases and both explicit rates. There were no worker-start files or case
directories when this preparation check completed; no experiment was launched
as part of this task.

## Completed paired audit

All **48 cases completed without failures**, covering all four fixed variants,
three periods, two slippage settings and two fees. The final read-only audit
passed with **zero errors** across **137,262 saved trade rows**. All 24 pairs
used identical frozen strategy rules, engine, data, configuration, date range,
and nonfee assumptions. The 61 frozen source hashes and 15 data/archive hashes
still matched the registered manifest above.

The selection, diagnostic and full workers completed in 247.66, 158.87 and
370.09 seconds respectively, concurrently. All three completion records were
present by `2026-09-05T01:10:37Z`. No worker was restarted. Inputs, original
reports, and both prepared snapshots remain preserved.

Audit/export command:

```powershell
backend/.venv/Scripts/python.exe backend/tools/audit_legacy_fee_pairs.py --prepared-dir backend/storage/research/legacy-fee-pairs/20260905T010308.671709Z-legacy-fee-only-v2-da347c739699
```

The completed export is at:

```text
backend/storage/research/legacy-fee-audits/20260905T011103.570147Z-legacy-fee-audit-474c0de04e3e
```

It contains `audit.json` with input hashes and all checks, `cases.csv`,
`pairs.csv`, `historical-controls.csv`, `published-replay-controls.csv`,
`report.md`, and **48 complete trade CSVs** retaining every saved ledger field.
The prepared run retains each full JSON replay, full JSON trade ledger and
comparison report. The export script reads those artifacts without changing
them and refuses a partial run. Its own SHA-256 is recorded in `audit.json`.

The audit JSON SHA-256 is
`8cf44c4009a2b9acc8d2bc565ff031fcff7276aac362b49d0636d7d21b6a8483`.
An additive `export-verification.json` records hashes for all 55 export files,
including an exact copy of the audit script. All 24 table rows below were
checked against the audited values, and all 137,262 CSV trade rows were checked
field by field against their original JSON values; both checks passed.

### Results at both fees

All dollar amounts are for one MNQ and the original $50,000 starting balance.
Fees are per contract per side; slippage is adverse ticks on **each** fill.
PF and drawdown below use the corrected $0.61 fee. Trade counts show
`$1.20 → $0.61`. Drawdown is the engine's reported maximum, not an independently
reconstructed tick-level measure. Both full metrics and all trades are retained
for each fee. The three periods overlap; their results must not be added.

Selection, January 2020–December 2023:

| Variant | Slippage | Net at $1.20 | Net at $0.61 | PF at $0.61 | Drawdown at $0.61 | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline v4 | 1 | -5,352.80 | -1,851.74 | 0.9862 | 6,559.64 | 2,967 → 2,967 |
| Baseline v4 | 2 | -8,335.90 | -4,744.14 | 0.9650 | 7,524.85 | 2,961 → 2,962 |
| Bracket only | 1 | -2,704.80 | 436.36 | 1.0033 | 5,545.98 | 2,662 → 2,662 |
| Bracket only | 2 | -5,942.20 | -2,799.86 | 0.9792 | 6,590.18 | 2,663 → 2,663 |
| Trend alignment | 1 | -5,465.90 | -2,502.92 | 0.9782 | 5,563.60 | 2,511 → 2,511 |
| Trend alignment | 2 | -8,239.50 | -5,283.60 | 0.9544 | 7,304.58 | 2,505 → 2,505 |
| No chase | 1 | 810.40 | 2,945.02 | 1.0395 | 3,726.78 | 1,809 → 1,809 |
| No chase | 2 | -957.00 | 1,172.90 | 1.0156 | 4,301.06 | 1,805 → 1,805 |

Diagnostic, January 2024–July 10, 2026:

| Variant | Slippage | Net at $1.20 | Net at $0.61 | PF at $0.61 | Drawdown at $0.61 | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline v4 | 1 | -10,864.90 | -8,564.14 | 0.9151 | 10,612.06 | 2,036 → 2,037 |
| Baseline v4 | 2 | -12,470.10 | -10,194.20 | 0.8997 | 12,032.78 | 2,034 → 2,035 |
| Bracket only | 1 | -8,915.50 | -6,623.84 | 0.9343 | 9,492.10 | 1,945 → 1,947 |
| Bracket only | 2 | -10,661.30 | -8,363.84 | 0.9177 | 11,032.21 | 1,947 → 1,947 |
| Trend alignment | 1 | -6,592.40 | -4,591.12 | 0.9446 | 6,447.72 | 1,696 → 1,696 |
| Trend alignment | 2 | -8,044.10 | -6,131.90 | 0.9267 | 7,798.22 | 1,694 → 1,695 |
| No chase | 1 | -6,182.80 | -5,029.94 | 0.8904 | 6,194.49 | 977 → 977 |
| No chase | 2 | -7,018.40 | -5,866.72 | 0.8734 | 6,949.69 | 976 → 976 |

Full cached period, including sparse 2019 launch coverage through July 10, 2026:

| Variant | Slippage | Net at $1.20 | Net at $0.61 | PF at $0.61 | Drawdown at $0.61 | Trades |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline v4 | 1 | -16,658.60 | -10,466.20 | 0.9573 | 14,090.68 | 5,334 → 5,335 |
| Baseline v4 | 2 | -21,506.90 | -15,248.66 | 0.9384 | 17,896.75 | 5,326 → 5,328 |
| Bracket only | 1 | -11,015.70 | -5,357.50 | 0.9779 | 12,222.02 | 4,798 → 4,800 |
| Bracket only | 2 | -16,198.30 | -10,531.94 | 0.9571 | 15,688.29 | 4,802 → 4,802 |
| Trend alignment | 1 | -11,549.30 | -6,248.74 | 0.9697 | 9,492.86 | 4,492 → 4,492 |
| Trend alignment | 2 | -15,995.10 | -10,790.70 | 0.9482 | 13,314.54 | 4,484 → 4,485 |
| No chase | 1 | -5,551.20 | -1,889.66 | 0.9855 | 7,207.53 | 3,103 → 3,103 |
| No chase | 2 | -8,405.70 | -4,750.06 | 0.9638 | 8,110.81 | 3,098 → 3,098 |

### Fee accounting and original controls

Every trade has quantity one and the actual round-trip commission is exactly
**$2.40** in the old-cost cases or **$1.22** in the corrected-cost cases. Gross
P&L reconciles with entry/exit prices and MNQ's $2 point value; net P&L reconciles
with gross less fees. Trade chronology, total gross/net/fees, long/short counts
and net P&L, daily/monthly totals, and final equity all passed. Full saved
ledgers match the comparison tool's own ledger hashes.

Fifteen pairs retain identical trades after excluding commission and net P&L.
Nine pairs change execution rows; their trade-count changes are shown above.
The retained comparison engine books fees in daily risk state, so lowering fees
can change subsequent admission decisions. All 24 net-P&L improvements reconcile
as **gross-P&L change plus total fee reduction**. These are rerun results, not
refund estimates. For example, full baseline at one tick adds one trade, changing
gross P&L by -$100.50 and reducing total fees by $6,292.90, for a $6,192.40 net
improvement. Full bracket only adds two trades, changes gross by -$1.00, and
reduces fees by $5,659.20, for a $5,658.20 net improvement. `pairs.csv` records
execution rows added/removed and this decomposition for all pairs.

The **11 original saved comparison controls all reproduce every metric and
their complete trade-ledger hash exactly** at $1.20, despite their older v4
cache identity. They comprise four selection cases, baseline/no-chase later
and slippage cases, and bracket-only later/slippage/full cases. The original
v4 and v5 full replay reports also reproduce every metric exactly: respectively
**5,334 trades / -$16,658.60** and **4,798 trades / -$11,015.70**. Those two compact
replay reports contain trade counts rather than full ledgers; no ledger match is
claimed from them. Original report SHA-256 values and the original/current
source fingerprints are preserved in the control CSVs and audit JSON.

### Reconsideration of the earlier rejections

**No chase improves materially at the corrected fee.** Its selection-period
two-tick case changes from a loss to a profit, and it has the highest selection
PF of the four variants at both slippage settings. Its full-period losses and
drawdowns are also smaller than the other variants in this legacy replay.
However, its later diagnostic period remains negative at both slippage settings.
Its diagnostic PF remains below baseline's (0.8904 versus 0.9151 at one tick;
0.8734 versus 0.8997 at two ticks), as does per-trade expectancy (-$5.15 versus
-$4.20; -$6.01 versus -$5.01). Taking roughly half as many trades reduces its
total dollar loss, without establishing positive expectancy. The earlier
conclusion cannot simply be copied from $1.20, but corrected fees still do not
make this fixed no-chase rule a profitable later-period result.

**Trend alignment also deserves a more precise description than a blanket
rejection based on the old fee.** At $0.61 it has the highest diagnostic PF of
the four variants at both slippage settings (0.9446 and 0.9267), and the smallest
diagnostic dollar loss at one tick. At two ticks no chase loses less in dollars
because it takes fewer trades. Trend alignment still loses in every selection,
diagnostic, and full-period case. Its selection PF remains below baseline's and
its full-period PF remains below bracket only's. These results support reporting
its relative diagnostic improvement; they do not establish a profitable rule.

All four variants remain negative over the full period and over the later
diagnostic period at both slippage settings. This retrospective reconsideration
selects no strategy change. It uses the frozen **legacy five-minute application
comparison**, whose aggregate execution/parity limitations remain; it must be
kept separate from the corrected observed-minute research. All dates here were
already exposed. No quarantined prices, returns, or strategy outcomes were
inspected, and this experiment consumes none of the reserved newer evaluation
pool.
