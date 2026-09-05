# Hourly range reversal: completed MNQ adaptation

The fixed adaptation lost money overall and under doubled slippage. Its later
period was slightly positive, but only 26 trades occurred there. This does not
support selecting it over the opening-range candidate. The source described
Micro Russell; these MNQ results do not establish how the original performs.

All six final simulations used one MNQ, $50,000 starting cash, $0.61 fees per
side, native completed hourly signals and observed one-minute execution.
Each period/cost starts with a fresh portfolio. The local data spans May 2019
through July 10, 2026, split at January 1, 2024 UTC. Both periods are reused
research observations, not independent validation.

| Period | Slippage per fill | Trades | Net P&L | Profit factor | Win rate | Maximum minute-close drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full | 1 tick | 156 | -$1,966.82 | 0.8017 | 40.38% | $3,304.74 |
| Full | 2 ticks | 156 | -$2,103.32 | 0.7899 | 40.38% | $3,359.58 |
| Development | 1 tick | 130 | -$2,132.60 | 0.7443 | 38.46% | $3,304.74 |
| Development | 2 ticks | 130 | -$2,247.10 | 0.7332 | 38.46% | $3,359.58 |
| Later diagnostic | 1 tick | 26 | $165.78 | 1.1052 | 50.00% | $925.74 |
| Later diagnostic | 2 ticks | 26 | $143.78 | 1.0905 | 50.00% | $941.74 |

The [predeclared protocol](reddit-hourly-range-protocol-2026-09-05.md) supplies
the exact assumptions and command. A completed hourly candle crosses the
preceding ten completed bars' range. Longs require a close below EMA100; shorts
require a close above EMA100. The EMA is SMA100 seeded on the last 200
same-delivery hourly bars. Entries are eligible at 08:00–13:59 Eastern. The
absolute opposite range boundary remains the stop, and the target is 1.5
range widths from the actual slipped fill, rounded to a quarter point.

The source's trend filter and entry confirmation were not fully specified;
the fixed interpretation above was chosen before outcomes. MNQ, the 100-point
stop-risk rejection, the $250 proposed-stop daily entry gate and daily
15:55/holiday flatten are disclosed research adaptations. Missing exact next
execution minutes cancel signals; clock exits use the next observed open.
No parameter was tuned after looking at these outcomes. The stop gate is not
a guarantee that actual losses stay within a daily dollar limit.

All six cases finished with zero replay failures after adapter corrections.
Seven synthetic/integration tests passed, including an independently written
real-engine test of the 08:00 opening-boundary signal. A separate source audit
checked all **624 saved trade records**: prior hourly range, independently
computed EMA, signal and fill times, contract identity, actual entry minute
price, absolute stop/target, stop-risk cap, exit prices, fees, P&L totals and
nonoverlapping positions. Every check passed. Full CSV ledgers accompany JSON.

Two early attempts failed before replay while adapting the production
five-minute configuration guard. The initial completed matrix then exposed
an inherited session-boundary omission in independent review; its ledgers and
audit remain preserved and explicitly superseded. The final matrix below
reruns the unchanged hypothesis with close-time evaluation eligibility. The
production engine and trading configuration were not edited.

Final run:

`backend/storage/research/experiments/20260905T034455.216080Z-reddit-hourly-range-fixed-clock-a16661889de7`

This contains the immutable manifest, complete source snapshots, per-case
replay/summary/session/trade JSON, trade CSVs, `results.json`, and
`independent-audit.json`. Source fingerprint:
`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`.
The actual candidate is identified by `candidate_definition`,
`candidate_fixture_revision` and the private hourly runner; inherited baseline
strategy labels in other engine metadata are not the research implementation.

Audit command from repository root (writes new files; use a new run directory
to repeat rather than overwriting this evidence):

```powershell
backend/.venv/Scripts/python.exe backend/tools/audit_reddit_hourly.py backend/storage/research/experiments/20260905T034455.216080Z-reddit-hourly-range-fixed-clock-a16661889de7
```

[Reddit source](https://www.reddit.com/r/algotrading/comments/1gchopm/range_breakout_strategy/)
