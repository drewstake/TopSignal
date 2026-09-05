# MNQ transaction-fee correction — September 4, 2026

Topstep's [published TopstepX fee schedule](https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees),
dated July 28, 2026 and verified September 4, lists MNQ total fees of **$1.22
round trip**, or **$0.61 per contract per side**, for Combine, Express Funded and
Live Funded accounts. The round trip includes $0.70 exchange fees, $0.02 NFA fees
and $0.50 commission. Slippage is a separate replay assumption.

The engine correctly charges `commission_per_contract` on both entry and exit.
The UI and research tools previously supplied $1.20 per side, totaling $2.40 per
contract round trip. That default overstated fees by $1.18 per completed
one-contract trade. Previous reports remain historical evidence of the stated
assumptions, but their costs do not represent the verified TopstepX rate.

The UI now defaults to $0.61 and labels the field **Fees / contract / side**,
with an explicit round-trip total. Saved results display their actual per-side
and round-trip assumptions. The API's omitted-fee default and all three TopBot
replay/research CLIs use the same corrected backend fee constant. Explicit fee
overrides, including zero for controlled tests and 1.20 to reproduce old runs,
remain available. No historical reports are relabeled or overwritten.

## Fixed comparison protocol

Only transaction fees change. Preserve the v5 entry/exit rules, one-contract
sizing, $50,000 balance, risk limits, one tick of slippage and full stored range.
Use the current format-6 MNQ cache for both rates. Its source fingerprint is:

`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.

Rerun both fee settings on the application-style five-minute replay. Separately
run the observed-minute research baseline at the corrected fee and compare to
its saved same-engine, same-cache $1.20 control. These are reused historical
diagnostics. Do not inspect the separate newer-data quarantine or tune strategy
rules in response to this comparison. No trading run or order is authorized.

Fee changes affect daily loss and proposed-stop gates, so rerun the engine rather
than adding a flat refund to the old P&L. Use distinct output paths under ignored
`backend/storage/research`; retain the original ledgers and research manifests.

The old 72-case research matrix already captured $1.20 per side in its manifests.
Changing the CLI default cannot change an existing process or saved report.
Treat that matrix as a higher-cost stress case and rerun candidate comparisons
with explicit `--commission-per-side 0.61` before selecting a strategy. This fee
correction does not itself restart or retune that research matrix.

## Reproduction

From the repository root in PowerShell:

```powershell
backend/.venv/Scripts/python.exe backend/tools/benchmark_topbot_replay.py --days 3000 --holdout --commission-per-side 1.20 --output backend/storage/research/fee-old-app.json --trades-output backend/storage/research/fee-old-app-trades.json
backend/.venv/Scripts/python.exe backend/tools/benchmark_topbot_replay.py --days 3000 --holdout --commission-per-side 0.61 --output backend/storage/research/fee-corrected-app.json --trades-output backend/storage/research/fee-corrected-app-trades.json
backend/.venv/Scripts/python.exe backend/tools/research_topbot.py --label fee-corrected-baseline --variants baseline_v5 --periods full --slippage-ticks 1 --commission-per-side 0.61 --protocol docs/topbot-fee-correction.md
```

## Results

The application-style replay completed on the same 504,185 five-minute bars,
May 7, 2019 through July 10, 2026. Its $1.20 control exactly reproduced the
previous full-history metrics. The corrected run is also visible in the app as
run #21, with $0.61 per side and $1.22 on each one-contract trade.

| Application replay | Old $1.20/side | Correct $0.61/side |
| --- | ---: | ---: |
| Trades | 4,798 | 4,800 |
| Gross P&L after slippage | $499.50 | $498.50 |
| Total transaction fees | $11,515.20 | $5,856.00 |
| Net P&L | -$11,015.70 | -$5,357.50 |
| Profit factor | 0.9551 | 0.9779 |
| Expectancy/trade | -$2.30 | -$1.12 |
| Maximum drawdown | $16,039.30 | $12,222.02 |
| Long net P&L | -$1,383.90 | $2,186.78 |
| Short net P&L | -$9,631.80 | -$7,544.28 |
| Final-20% diagnostic net P&L | -$4,279.20 | -$2,851.30 |

Net loss improved by $5,658.20. Lower fees allow two additional short entries
through the daily risk gate, so this is not just a recalculation of the same
ledger. The long slice is now positive in this historical mixed-direction run;
that is not a separately tested long-only strategy. The combined strategy remains
negative after costs. The current published fee is held constant throughout
history to model today's economics; this is not a historical fee-schedule model.

The separate observed-minute research baseline also remains negative:

| Research replay | Old $1.20/side | Correct $0.61/side |
| --- | ---: | ---: |
| Trades | 4,224 | 4,224 |
| Gross P&L after slippage | $1,704.00 | $1,704.00 |
| Total transaction fees | $10,137.60 | $5,153.28 |
| Net P&L | -$8,433.60 | -$3,449.28 |
| Profit factor | 0.9609 | 0.9838 |
| Long net P&L | $153.10 | $3,357.98 |
| Short net P&L | -$8,586.70 | -$6,807.26 |

Old research control: `20260905T003843.383966Z-parity-controls-b0cf5c51afd3`,
case `baseline_v5__full__slip-1`. Corrected research run:
`20260905T004802.745634Z-fee-corrected-baseline-1c25d5260c60`, same case.
Both are under `backend/storage/research/experiments`. The research path uses
observed-minute execution, actual old-delivery rollover prices and proposed-stop
risk reservations; the application path still uses legacy five-minute execution.
Compare fee changes within each path, not across the two engines' assumptions.

Verification: 1,550 backend tests passed, 8 PostgreSQL-dependent tests skipped;
22 focused frontend tests passed; frontend lint and production build passed.
The fee regression covers one, five, ten and twenty contracts, no-price-change
P&L/equity accounting, and an explicit zero-cost override. The app displays the
corrected completed replay and the bot remains stopped.

## Completed candidate reconsideration

The subsequent A06 continuation reran all 72 fixed observed-minute cases with
explicit $0.61 fees and matched them against the retained $1.20 stress matrix.
All pairs passed the source, data, non-fee-control and ledger audits. Seventeen
cases changed their non-fee trade paths. Read the
[complete eight-variant comparison](topbot-fee-comparison-audit-2026-09-04.md).
The separate [48-case legacy comparison](topbot-legacy-fee-audit-2026-09-04.md)
also reran the original rejected filters under both rates, with full ledgers.

Opening drive alone passed the measured historical shortlist gates and moved
to its predeclared robustness checks. This is not a profitability certification,
an independent validation result or authorization to trade. The complete
continuation is recorded in the experiment log and current handoff.
