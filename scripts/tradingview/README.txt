TopBot Adaptive — TradingView Pine v6

Current source: topbot-adaptive.pine
Chart levels: blue = entry, red = stop, green = target. Each trade has separate
horizontal segments, ending at its exit candle's close. Recent drawings are
retained within the label-history window and Pine's 500-line limit. Hide levels with the input
'Show entry / stop / target lines'. This drawing fix does not alter signals.
Signal labels sit beyond the recent five-candle high/low with an adjustable ATR
gap and a dotted connector to entry. Compact BUY/SELL text is the default; hover
for entry, SL, TP and exit rule, or disable 'Compact signal labels' to show
the full text. Current candle prices affect label placement only.
Exit labels are anchored to the candle that supplied the exit, rather than the
following confirmation candle. They appear only when the next candle begins.
Only one simulated position can be active. It stays open until SL or TP,
including overnight and across market closures. There is no timed exit.
Previous three-candle-exit version: topbot-adaptive-three-candle.pine
Previous five-minute-only source: topbot-adaptive-5m.pine
Original 2024-only port: topbot-2024-adaptive.pine

The current indicator learns and generates signals from the selected standard
time-based chart interval. It does not project the original 5-minute signals
onto other intervals. Changing interval recalculates training and all signals.
Warmup needs 21 closed chart candles and then 24 settled training paths by default.
Invalid data and the volatility cap may lengthen warmup.

Entries use the current candle's open and information from earlier closed candles.
Stops/targets are tested on subsequent closed candles, conservatively taking the
stop when both levels touch in the same bar. Minimum entry spacing remains three
chart candles, but never closes a position. An open trade blocks all new entries.
Training paths also wait for both hypothetical legs to hit SL/TP before scoring.
At 500 unresolved training paths, new training paths are skipped until capacity
is available; existing paths and the active position are not timed out. Warmup
can therefore take longer. An unresolved final trade remains open.
Invalid candles clear uncertain training paths but do not fabricate a position
exit. The active position resumes evaluation on the next valid candle; any exit
during missing data is unknown and results spanning that gap require review.

The previous minimum/maximum stop distances (4 and 25 MNQ points) scale with
sqrt(timeframe seconds / 300), rounded up to the 0.25 MNQ tick. The RMS volatility
stop calculation (including its existing sqrt(3) distance multiplier) and 1.5R
target remain. The distance multiplier is not a holding limit. This is a heuristic timeframe adaptation,
not a new optimization or proof of performance.

The regular-hours input applies only to intraday charts. Daily, weekly, and
monthly candles are eligible at their opening time. Training includes overnight.
The old 2024 holiday exceptions apply only to 2024; other years use the weekday
and intraday time filter plus whatever traded candles the data feed supplies.
There is no entry-year restriction. The script is an indicator, not a strategy
that places orders or produces TradingView Strategy Tester results.

Features and entry cooldowns count consecutive AVAILABLE chart
bars across session breaks. This also changes 5-minute behavior around breaks
relative to the previously tested implementation. Missing scheduled candles
cannot be distinguished from market closures by this bar-count implementation.
The earlier full-year 2024 backtest does not validate this new version.

MNQ assumptions remain $2/point, $0.61 commission per side, and 0.5 point adverse
slippage per side. Signal generation can favor a direction with negative expected
net payoff unless 'Require positive estimated payoff' is enabled.

Pine timeframe conversion reference:
https://www.tradingview.com/pine-script-docs/concepts/timeframes/
