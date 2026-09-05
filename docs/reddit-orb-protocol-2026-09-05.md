# Fixed Reddit ORB test, declared before results

One new fixed hypothesis, `reddit_orb15_long`, is tested once per full,
development (before 2024) and chronological diagnostic (2024 onward) period,
at one and two adverse slippage ticks per side. Each run starts flat with
$50,000, one MNQ, and $0.61 fees per contract per side. All local observations
have already been exposed. These are retrospective diagnostics, not holdouts.
No parameter tuning follows the results in this task.

Source: https://www.reddit.com/r/algotrading/comments/1j9pxsr/backtest_results_for_the_opening_range_breakout/
The post was re-read September 5, 2026 UTC. Its disclosed code excerpt requires
the breakout candle to open at or below the range high and close above it.
The author explicitly permits additional trades when new setups occur after
the previous trade closes. The original test used S&P 500 CFDs; this is an
MNQ market adaptation, not a reproduction of those claimed CFD results.

Rules are fixed: 09:30–09:45 Eastern high/low define the range. Aggregate only
complete strict five-minute triples into 15-minute candles. Enter long at the
open after a crossing candle closes, strictly before noon. Thus the earliest
entry is 10:00 ET and the latest is 11:45 ET. A complete observed five-minute
session prefix is required. The stop remains exactly the opening-range low.
The target is 1.5 times risk measured from the actual slipped entry, with
target distance rounded up to a quarter point. There are no shorts or pyramids.

The dedicated runner adapts the shared replay's relative-bracket behavior to
preserve that absolute stop. Actual entry risk above 100 points, or below one
tick, is rejected rather than moving the stop. The existing $250 realized-loss
entry gate reserves proposed stop risk before entry; it is not a guaranteed
maximum loss. Thirty daily entries and zero cooldown do not prevent repeated
eligible 15-minute setups. Positions flatten at 15:55 ET or known holiday
close minus five minutes; if that open is absent they flatten at the next
observed open. This flat-at-close rule is a declared MNQ operational adaptation.
Missing exact entry minutes reject the signal instead of inventing a fill.
Stops win intraminute ambiguities; observed gap opens execute stops, targets
receive no gap price improvement, and every fill incurs adverse slippage.
The source post does not establish actual spread, queue priority or transport
latency. No live behavior is changed or armed.

Preserve immutable manifests, sources, failed attempts, full ledgers, year
breakdowns, drawdown, net expectancy and bootstrap diagnostics. Use the existing
September 4 protocol's numerical screen as descriptive context; this six-case
test alone cannot complete four-tick/parameter/independent-validation gates.

Command from repository root:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/research_reddit_orb.py --fixture backend/tools/fixtures/reddit_orb.py --variants reddit_orb15_long --periods full development diagnostic --slippage-ticks 1 2 --commission-per-side 0.61 --execution-minutes 1 --protocol docs/reddit-orb-protocol-2026-09-05.md --label reddit-orb15-fixed
```
