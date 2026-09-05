# Frozen Reddit hourly range test

The user's September 4 request authorizes offline tests of all four shortlisted
Reddit ideas. This is the second idea, adapted to available MNQ history because
the cache has no Micro Russell data. It is not an exact reproduction of the
author's performance. Source:
https://www.reddit.com/r/algotrading/comments/1gchopm/range_breakout_strategy/

Freeze one hypothesis before looking at outcomes: native completed hourly bars;
range of the preceding ten completed hours; current candle opens inside the
relevant boundary and closes outside; long only below EMA100 and short only
above EMA100. EMA100 is SMA seeded on the trailing 200 same-delivery hourly
bars. This supplies an explicit interpretation of the unspecified trend filter.
Enter at the next observed minute open, between 08:00 inclusive and 14:00
exclusive Eastern. No entry is fabricated when the required minute is absent.
The stop remains at the opposite range boundary. Target distance from actual
slipped entry is 1.5 times the range height, rounded outward to a quarter point.
Reject entries whose stop risk exceeds 100 points, at both signal and fill.

Use one MNQ, $50,000 starting cash, $0.61 per contract per side, and separate
one- and two-tick adverse-slippage replays. Keep the $250 proposed-stop daily
entry gate, at most 30 entries/day, no averaging or scaling. Flatten at 15:55 ET
or five minutes before a known early close, using the first observed minute.
This added intraday account policy is shared with the other futures research;
the source did not specify it. Stop-first handling resolves ambiguous minute
bars; roll liquidation uses observed old-contract prices. All source revisions,
parameters, data fingerprints and ledgers are saved by the existing runner.

Run all six cases: full cache; 2019–2023 development; 2024–cache-end diagnostic,
each with fresh cash and portfolio state at each of the two costs. Existing
history has already been used for research, so the later period is retrospective
and is not an untouched holdout. No search or parameter selection follows these
outcomes. Report losses and limited samples as well as gains. Existing strict
historical screens may be shown but do not establish independent profitability.

The first six-case attempt failed before replay because the production TopBot
validator only accepts five-minute candles. Its directory
`20260905T033859.078125Z-reddit-hourly-range-7ff57c4168db` is preserved. The
offline wrapper now validates an explicitly hourly research configuration and
uses the existing validator for its remaining period checks. Its stream spec
is hourly only within this process; production files are unchanged. This is an
execution adapter repair, with no hypothesis changes or inspected outcomes.
The second attempt (`20260905T033923.915074Z-reddit-hourly-range-0690926328f4`)
also failed before replay: the existing configuration view cannot override
timeframe fields. The adapter now passes a minimal validation-only namespace;
a synthetic test verifies both valid hourly settings and invalid-period rejection.

Independent review then found the inherited engine gates evaluation by the
execution minute's open time. It skipped signals closing exactly at the declared
08:00 start. The six completed cases and source audit in
`20260905T033946.470237Z-reddit-hourly-range-59ec775ee3bd` are preserved as
superseded, not final evidence. Their audit checked every recorded fill but did
not detect omitted opening-boundary signals. The private engine now gates
evaluation by minute close; actual fills still must be inside the original
entry window. A separate real-engine boundary regression covers the omission.
All six cases are rerun after this execution repair, without changing the
original hypothesis, sizing, risk, cost or period choices.

Command from repository root:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_reddit_hourly.py --fixture backend/tools/fixtures/reddit_hourly_range.py --label reddit-hourly-range --variants reddit_hourly_range_mnq --periods full development diagnostic --slippage-ticks 1 2 --commission-per-side 0.61 --execution-minutes 1 --bootstrap-repetitions 1000 --protocol docs/reddit-hourly-range-protocol-2026-09-05.md
```
