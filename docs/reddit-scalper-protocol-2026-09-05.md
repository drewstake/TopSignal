# Reddit scalper: predeclared independent proxy

This is a new bounded hypothesis recorded before inspecting its replay results.
It is one of the four strategy ideas the user asked to backtest. The original
five-second strategy cannot be replicated from public information. The
[linked post](https://www.reddit.com/r/algotrading/comments/1rtepah/how_i_improved_results_on_a_scalping_algo_mean/)
discloses mean reversion, two parameter settings and split entries, but expressly
declines to share its script. It says exits occur at bar close. The
[initial post](https://www.reddit.com/r/algotrading/comments/1r5al3o/finally_having_good_results_with_my_scalping_alog/)
clarifies that actual trading uses Nasdaq CFDs on Vantage, charted on five-second
NQ bars, with typical two-to-six-minute holds. A later author update says that
one setting's edge faded. These are unverified author claims.

The only usable local source for this exercise is authenticated Databento
GLBX.MDP3 `ohlcv-1m`, actual MNQ deliveries, format-6 cache. No genuine five-second
or tick data is available in this cache. It is not possible to recover five-second
price paths from one-minute OHLC. No interpolation or paid data purchase is used.
MNQ is materially different from the author's CFD execution venue.

## Fixed approximation

`backend/tools/fixtures/reddit_scalper.py` declares one independent proxy, not an
implementation of the withheld original. Parameters are not inferred from a
performance chart or optimized after observing results.

- At a closed one-minute candle, examine the previous close's departure from
  its contemporaneous SMA10 / ATR10 and SMA30 / ATR30. A previous close below
  mean minus two ATR followed by a rising close still below the current mean
  triggers long; invert for short. Either setting may trigger; fast wins ties.
- One whole MNQ total. No split fractional contracts, pyramiding or averaging
  down, consistent with the existing fixed-risk account comparison. Therefore
  the source's position-splitting claim is not tested by this approximation.
- Enter at the next observed minute open between 10:00 and 15:45 ET. Thirty-two
  consecutive observed minutes with the same delivery identity are required.
  The 30-minute delay after regular open allows the slow mean to form.
- Stop at two ATR20, clipped to 5–100 points and rounded outward to whole ticks;
  target at 1R. Distances are anchored to the actual slipped entry fill by the
  existing engine, not to untraded signal-close levels.
- A close across the midpoint of current SMA10 and SMA30 signals a market exit
  on the next observed minute open. Independently flatten on the first observed
  open at the earlier of six minutes after entry or known session close minus
  five minutes. In a data outage the fill is delayed, never invented. The engine
  names both clock exits `scheduled_session_flatten`; this label includes the
  six-minute deadline for this fixture.
- One-minute cooldown, 30 entries per trading day, $250 realized-loss entry
  gate including proposed stop risk. This is not a guaranteed maximum daily loss.

Use fresh $50,000 cash/positions/pending signals/risk state for every full,
development (2019–2023) and chronological diagnostic (2024–cache end) replay.
Use $0.61 per contract per side, .25-point ticks worth $.50, and separate complete
replays at one and two ticks adverse slippage per fill. OHLC ambiguity is
stop-first; fills do not model queues, bid/ask dynamics or subminute transport.
These omissions are especially material to scalping. All local history is
already exposed research data; none is untouched validation. In particular,
the later period is a reused diagnostic, not proof of future profitability.

## Reproduction and decisions

Run the native-minute wrapper around the existing offline-only tool. It changes
only this process's TopBot stream specification to one minute and replaces the
production five-minute-only configuration restriction while retaining its other
checks. Signal-session eligibility uses minute close timestamps, including the
09:59 candle's 10:00 decision. It retains the
framework's 200-bar delivery warmup, and leaves production untouched. It first captures this document, fixed
rules, code, Git diff, cache fingerprint and parameters in an immutable manifest,
then saves complete trades, replay data, session marks and cost summaries:

```powershell
backend/.venv/Scripts/python.exe backend/tools/research_reddit_scalper.py --fixture backend/tools/fixtures/reddit_scalper.py --label reddit-scalper-1m-proxy --variants reddit_scalper_1m_proxy --periods full development diagnostic --slippage-ticks 1 2 --commission-per-side 0.61 --execution-minutes 1 --bootstrap-repetitions 2000 --protocol docs/reddit-scalper-protocol-2026-09-05.md
```

Report every completed/failed case without tuning this candidate to results.
The existing research gates are useful rejection screens; even a pass would
describe only this one-minute approximation and cannot validate the original
five-second author's claim or authorize production/live integration.

## Execution correction before final comparison

The initial directory `20260905T033949.465592Z-reddit-scalper-1m-proxy-17d62c6bf364`
is preserved as interrupted, superseded evidence. Two full-history cases
completed before independent review found that the common engine's signal-session
mask used minute opens and skipped the boundary 10:00 decision. The remaining
cases were interrupted. The process-local wrapper now keys signal-session
eligibility to minute closes; an integrated test verifies the 10:00 boundary.
All six cases are rerun from fresh state with the same fixture rules and costs.
This corrects execution timing rather than changing a hypothesis after results.

The precise pending market-fill rule is the immediate next nominal minute open
if it exists; otherwise the pending signal is discarded, not filled late. The
independent six-minute/session risk clock instead fills at the first observed
open at or after its deadline. Source and accounting audit will verify this.
