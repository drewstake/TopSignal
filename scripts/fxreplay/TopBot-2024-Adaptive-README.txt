TOPBOT 2024 ADAPTIVE — EXPERIMENTAL

Paste topbot-2024-adaptive.js into an FXReplay V2 custom indicator.
Use MNQ on the 5-minute chart. The script does not place orders automatically.

HOW TO BACKTEST
1. Start with enough history before your evaluation period. Defaults require
   21 closed candles, then 24 settled three-bar training paths (about 4 hours
   of continuous suitable candles). Gaps/high volatility can extend warmup.
2. Read the BUY/SELL marker at its RIGHT endpoint: entry is at that candle's
   opening price, with 0.5 point adverse slippage already included in the label.
   The box spans the preceding candle only for readability. Do not enter early.
3. Use the printed SL and TP. Close after 3 candles / 15 minutes if neither hits.
   If both hit in one candle, count the stop first. Exit labels become available
   when the candle closes. No new entry within 15 minutes of the previous one.
4. Model costs use MNQ $2/point, $0.61 fee per side and 0.5 point slippage per side.
   Match these assumptions in your manual results; do not charge slippage twice.
5. Default entries run 09:35-15:30 New York in 2024, excluding weekends and the
   listed 2024 holidays/short sessions. January 1 evening will show no entries.
   Turning off regular hours permits overnight entries but keeps the 2024 gate.

WHAT CHANGED FROM PRODUCTION TOPBOT
No frozen 2026 model or training cutoff is used. Each completed three-bar path
teaches hypothetical BUY and SELL outcomes. The last 240 paths are retained.
Four momentum/volatility regimes use cell averages shrunk toward the pooled
average with a weight of 20. Training includes overnight candles. This estimates
net R, not a calibrated probability or the production model's HAC confidence bound.
Stops retain the RMS-volatility rule, minimum 4 / maximum 25 points, and target
1.5 times stop, rounded up to a 0.25 tick. It omits production account/risk gates.

Default selection needs a 0.05R difference between directions. It CAN signal
when both estimated payoffs are negative. Enable 'Require positive estimated
payoff' to refuse those entries. This is a new research strategy, not evidence
that the frozen model works or that these settings are profitable.

REPRODUCIBILITY
Only closed past bars enter features/training; current open sets the entry.
Training outcomes enter the sample pool only after the full three-bar horizon.
Future appended data does not change earlier decisions with the SAME starting
history. Loading more earlier history, changing inputs, or restarting from a
different date can change learned state and signals. Keep the starting history
and settings fixed when comparing runs. Data gaps discard incomplete samples;
any affected active trade is flagged for manual review, not given a valid result.
FXReplay labels are an aid to a manual backtest, not an account performance report.
Text labels cover the latest 600 evaluated candles to stay below FXReplay's
500-drawing limit. Entry/stop/target price series are retained for older bars.

VALIDATION
Run: node scripts/fxreplay/topbot-2024-adaptive.test.cjs
Nine checks cover causality, maturity, costs, ambiguous touches, gaps, DST,
warmup, bounded memory, nonoverlap, and both signal directions.
A separate local Databento smoke check used 840 complete 5-minute candles,
January 1-4 2024 UTC, instrument 7101: 4 BUY and 46 SELL entries. This was only a
signal-generation check. FXReplay feeds/history may differ; no full-year return
or performance validation has been performed.
