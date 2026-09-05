# A08: frozen opening drive fails the preliminary unseen evaluation

The unchanged opening-drive candidate **failed its predeclared preliminary
screen** on all 40 reserved ProjectX trading sessions. Both required cost cases
lost money. The audit found no source, accounting or execution inconsistency
that explains away the failure. The entire pool is now exposed and cannot be
called untouched, split for another selection, or used to retune this candidate.

These are three costs applied to the **same 12 setups**, not 36 independent
trades. Forty sessions and twelve trades cannot meet the unchanged requirement
for at least 200 trades and six months of independent confirmation. Failure of
this short-sample screen is evidence against promotion, not a statistical proof
that the strategy's true future expectancy is nonpositive. No strategy change,
new date selection or alternative candidate was chosen from these outcomes.

## Fixed result

Each case starts fresh with $50,000 and one MNQ. Fees are $0.61 per contract per
side ($1.22 round trip); slippage is adverse ticks on every entry and exit.
The cases use the original center, actual minute execution, completed five-minute
decisions, the original risk settings and the independent calendar clock.

| Slippage ticks per fill | Trades | Gross P&L | Fees | Net P&L | Profit factor | Expectancy | Maximum drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 12 | -258.00 | 14.64 | -272.64 | 0.8182 | -22.72 | 856.49 |
| 2 | 12 | -264.50 | 14.64 | -279.14 | 0.8144 | -23.26 | 858.99 |
| 4 | 12 | -877.50 | 14.64 | -892.14 | 0.4798 | -74.345 | 894.53 |

All three cases cover the same 40 sessions, including **28 zero-P&L sessions**.
Each has seven long trades and five shorts. Long/short net P&L is respectively
$135.96 / -$408.60 at one tick, $131.96 / -$411.10 at two ticks, and
-$476.04 / -$416.10 at four ticks. This side breakdown is an audit disclosure,
not a basis for changing the frozen direction rule. Win rates are 33.33%,
33.33% and 25.00%; minute exposure is 1.7201%, 1.7183% and 1.5044%.

The larger four-tick loss is a legitimate changed execution outcome. On August
3, the 14:08 UTC minute low is 28,525.00. The four-tick actual entry anchors the
stop at 28,525.25, which is reached. The one/two-tick stops are 28,524.50 and
28,524.75, which are not reached; those positions later reach their targets in
the 16:04 minute. The audit checked the first eligible exit minute, not just
whether an eventual price existed. This is why higher-cost results were rerun
under the frozen protocol instead of estimated by subtracting a fixed charge.

## Chronology, source and coverage

The [protocol](topbot-unseen-opening-drive-protocol-2026-09-04.md), candidate,
source and data hashes were frozen before evaluation. A06 and all 46 A07 cases
had completed their separate audits, retaining the original opening-drive center.
The preparation was created at **2026-09-05 01:23:39 UTC**, the root decision
receipt at **01:26:19.112626 UTC**, and execution started at **01:26:19.809636 UTC**.
The receipt binds the preparation, unchanged candidate and SHA-pinned A07 audit.
It authorized this offline evaluation only.

The immutable preparation and all original outputs are:

```text
backend/storage/research/unseen-preparations/20260905T012339.618436Z-projectx-unseen-prepared-c8255427c5af
```

Preparation manifest SHA-256:
`fe880f413c5c2cd3db1addc0507cafc2ec5cf8da09cdac0c430d8cef67d3af6f`.
Source-bundle SHA-256:
`fb5faada235c1795083e3bb7f90c22e505145c242ed76f7be377f6684087b312`.
All **77 captured source/protocol/test files** match, and the **56 original
A06 app/runner/fixture files** remain byte-identical to their earlier snapshot.
The original A06 manifest hash is
`f1e4905064a2398b48d5c74b6a701ca5339244bb7d6e61837c0733defb41cf88`;
the original center definition matches exactly. The captured Git revision is
`9616061a950c7e414f3bae45e150de79c04e6fb7`.

The six canonical ProjectX response files retain every original hash. The
collection manifest SHA-256 remains
`25e354280208ae795f402dd88155b3f87c1652b4b976c5b31002fc462dff4576`.
An independent timestamp check accounts for **all 55,240 minutes**: July 10's
40-minute tail plus every minute in all 40 complete July 13–September 4 sessions,
with no omissions or duplicates. Bar prices come from ProjectX for
`CON.F.US.MNQ.U26` / `MNQU6@2026`; instrument `42004800` is the independently
verified Databento definition crosswalk, not a ProjectX-issued numeric ID.
Replay and summary attribution correctly identify ProjectX and the original
research candidate. The pool was never inserted into the Databento cache.

The predeclared requested interval is July 12 22:00 UTC–September 4 21:00 UTC
exclusive. The eight tail bars plus 192 overnight bars provide exactly 200
completed five-minute bars at July 13 14:00 UTC. The retained first execution
minute starts at 13:59 UTC, permitting that first 10:00 ET decision. The known
initial warmup deferral is 959 minutes; every case processes the same **54,241
execution minutes** through the final session close and reports all 40 sessions.
There is no outcome-dependent start-date shift, warmup trade or carried position.

## Independent audit and uncertainty

The aggregate audit passed with **zero errors**, and all **108 input files**
checked before and after it retained their hashes. It reconciled the manifest,
root receipt and passed A07 evidence; raw files and coverage; original rule and
source freeze; case settings; full trade hashes; quantity; price-based gross P&L;
round-trip fees and net arithmetic; side totals; daily/monthly totals; forty
session equity changes; and initial/final equity. Each case starts at $50,000
with zero realized/unrealized P&L; the captured factory creates fresh portfolio
and risk state for each cost, also covered by the earlier synthetic tests.

A separate source-minute audit verified all **36 saved trade records** using
decimal arithmetic: the six opening bars and displacement/stop/target geometry,
known-bar count, actual entry open, adverse fill price, anchored brackets, first
eligible causal exit, fees, P&L, MAE/MFE and holding bars. Across the three cost
views there are 25 stop exits, eight target exits and three calendar-clock exits.
There are no actual gap fills or both-touched bracket exits in this sample.
This verifies consistency with the specified OHLC model, not broker queue
priority or an observed within-minute price path.

The aggregate audit also independently marked the **already recorded positions**
against every source minute close, using the separately verified exit phase.
It reproduced every saved equity sample, maximum minute-close drawdown and
exposure. This was accounting on fixed ledgers; no strategy was replayed.
Drawdown is still a minute-close measure, not an intraminute or tick-level loss
bound. Original sampled chart outputs are clearly labeled as sampled in exports.

Concentration and the saved bootstrap estimates reproduce. At base cost, the
best trade is $398.28, accounting for 32.46% of positive trade P&L. Removing it
leaves -$670.92; removing the best five trades leaves -$1,359.54. There is only
one calendar year, so the mechanically reported zero after removing that entire
year has no useful diversification meaning.

The following independently reproduced 95% block-bootstrap intervals are for
**mean marked session P&L**, using 2,000 circular-block resamples and the frozen
seed. They are conditional descriptions from forty observations, with limited
block information; they are not probabilities of future profit.

| Slippage ticks | 5-session blocks | 20-session blocks |
| --- | ---: | ---: |
| 1 | -44.12 to 37.90 | -39.97 to 25.54 |
| 2 | -44.29 to 37.75 | -40.09 to 25.35 |
| 4 | -52.04 to 3.61 | -40.31 to -4.30 |

Both required point estimates are negative, so the terminal status correctly
remains `fails_predeclared_preliminary_screen`. Neither uncertainty nor a
profitable side subgroup changes that registered decision.

## Reproduction and retained exports

The full audit/export directory is:

```text
backend/storage/research/unseen-audits/20260905T013111.271967Z-a08-audit-1359ab18f0cc
```

Its `audit.json` SHA-256 is
`7bb3766b6d1d768aef30b393e7322f83e90b93f4fa86e469da5c49208b5f8b36`.
The independent trade proof remains at the preparation's
`audit-trade-execution-independent/verification.json`, SHA-256
`c3c8862bc01ae5d2ab7481ebd02df9586055b824ebf4885ae0c9bf681051db38`.
`exit-phases.json` binds to that proof, and its `verify.py` reproduces the
fixed-ledger source audit without evaluating a strategy.

The export retains three **complete trade CSVs**, three complete forty-session
CSVs, sampled equity/drawdown CSVs, `cases.csv`, `audit.json`, a copy of the
independent fill proof and a concise `report.md`. All 156 trade/session CSV rows
were checked field by field against their original JSON records. Full original
replay, trade, session and summary JSONs remain unchanged in the preparation.

Reproduce the read-only aggregate audit from the repository root using the
existing backend environment (Python, NumPy and `tzdata`):

```powershell
backend/.venv/Scripts/python.exe backend/tools/audit_projectx_unseen.py --prepared-dir backend/storage/research/unseen-preparations/20260905T012339.618436Z-projectx-unseen-prepared-c8255427c5af
```

An exact copy of this script is preserved in the export directory; SHA-256
`65fbaaefce12a2b77cb74c761bfe50f0e93ffae17c27657c45cf48673dd39864`.
The audit requires the retained preparation, source snapshots, original
ProjectX files, A07 evidence, and independent fill/exit-phase proofs. It writes
a new unique export directory, never overwrites inputs, makes no provider call
and **does not run the replay engine**. No new strategy or historical performance
experiment was performed during this audit.
