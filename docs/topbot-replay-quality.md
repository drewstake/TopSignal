# Replay quality reporting

Engine `4.2.1-replay-diagnostics` separates `warnings` (coverage or sample concerns)
from `notes` (warmup completed, chart sampling, delivery rolls, risk blocks, holdout
context and source provenance). Both are persisted and included in analysis JSON.
Older saved results remain readable; rerun to compute the new diagnostics.

Warmup is counted from closed bars in the current delivery at the first replay
evaluation, after any initial deferral. Coverage starts at the first stored
execution candle **before** that deferral, so consuming warmup bars no longer
claims that those candles were missing. Genuine missing requested boundaries
still produce warnings.

`data_quality.gaps` counts gaps and absent complete-bar slots across the replay,
including the subset overlapping configured entry hours. Annual counts use the
year of the first absent slot in Eastern time. The 20 largest examples are
bounded independently from the totals. The scalar and mmap paths use the same
gap candidates and calendar checks.

These counts are coverage diagnostics, not a determination of source corruption.
[Databento OHLCV documentation](https://databento.com/docs/knowledge-base) states
that intervals with no trades produce no record. The existing strict resampler
also excludes aggregates that lack an open-session minute. No OHLCV prices were
interpolated, no partial aggregates were admitted, and no risk limits were removed
to make the report appear clean.

The current recurring-session calendar is not a complete archive of historical
exchange status. One-off closures, varying holiday schedules and emergency halts
can still appear among the largest gaps. OHLCV and instrument definitions alone
cannot establish the cause of every absent interval; resolving those requires
authoritative historical session/status data or a verified replacement source.
For example, CME documented the January 9, 2025 equity early close at 08:30 CT in
its [National Day of Mourning announcement](https://www.cmegroup.com/media-room/press-releases/2025/12/30/cme_group_announcestradinghoursforusnationaldayofmourningtohonor.html).

Local verification against the unchanged imported MNQ cache:

- All 5,334 v4 closed trades are exactly equal to the prior ledger.
- All full-replay metrics remain equal, including net P&L of -$16,658.60.
- The first replay evaluation has 200/200 closed warmup bars.
- The existing calendar reports 1,570 gaps, 1,491 beginning in 2019; 24 overlap
  configured entry hours. These remain visible, with their limitations explained.
- The fresh full replay plus final-20% diagnostic took approximately 25 seconds.

Generated reports: `backend/storage/databento/topbot-v4-diagnostics-report.json`
and `backend/storage/databento/topbot-v4-diagnostics-trades.json` (ignored local
storage). The command-line tool accepts explicit requested dates, so a request
beginning before the first complete candle legitimately retains a leading
coverage warning. The UI's full-history run starts at its first available complete
candle and does not report that synthetic boundary mismatch.
