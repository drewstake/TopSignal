# Conditional overnight hypothesis protocol — September 4, 2026

Prepared before any overnight historical replay. First finish the corrected-fee
comparisons in A06, including reconsideration of the earlier rejected filters.
If they fail the existing research gates, this is the next bounded hypothesis
set. The [original evidence and acceptance protocol](topbot-research-protocol-2026-09-04.md)
continues to apply without relaxed thresholds. No newer reserved prices or
returns have informed these rules.

Economic hypothesis: an overnight long return may cover realistic costs with
one scheduled entry rather than frequent intraday signals. This is unproven;
holding across maintenance and overnight gaps can exceed the planned stop.
Test one MNQ entered after the closed five-minute bar ending at exactly 16:00
Eastern, Monday through Thursday, with an independent clock exit at 09:25 on
the next local date. If an execution minute is missing, the normal exact-minute
entry rule discards the entry; an existing position's clock exit uses the next
actually observed open. The exit deadline never resets at midnight or across
an outage. Resting stop and target orders retain priority.

Four variants are fixed in `backend/tools/fixtures/topbot_research_overnight.py`:

| Variant | Direction | Stop points | Target points |
| --- | --- | ---: | ---: |
| overnight_long_75 | Long | 75 | 150 |
| overnight_long_50 | Long, tighter neighbor | 50 | 100 |
| overnight_long_100 | Long, wider neighbor | 100 | 200 |
| overnight_short_control_75 | Short directional control | 75 | 150 |

All use 200 closed five-minute bars, one contract, at most one entry per futures
trading day, $50,000 starting cash, and the same $250 proposed-stop daily risk
gate. Skip known current early/full closures that prevent 16:00 entry and next
dates that are fully closed or close before 09:30. No Friday entry, weekend
position initiation, additional trend filter, averaging down or size sweep is
part of these variants. Existing calendar limitations remain disclosed.

Each variant receives full, development and diagnostic fresh portfolios at
1/2/4 ticks slippage and **explicit `--commission-per-side 0.61`**: 36 cases.
Use the same observed-minute engine and format-6 Databento cache as A06. The
fixture revision is `mnq_overnight_drift_fixed_phase2_20260904_v1`; its reviewed
SHA-256 is `74159352b94c9ebb7a1ef87a59ea7ccf5a72c3a52c22c7481265db4feae18f59`.
Every run also captures its exact sources, settings, costs and periods before
replay. Thirty-two synthetic tests passed for clock, calendar, gaps, brackets
and the corrected-fee daily risk calculation; these are software checks, not
strategy performance evidence.

Report all four variants. A profitable neighbor alone does not justify retuning
the center or suppressing failures. Retrospectively promising results still
need delayed-entry and fill-assumption stress, consistent runtime behavior,
and a frozen evaluation on genuinely unseen data. The reserved newer pool
remains unused at this preparation stage. Three earlier v4 alternatives and
seven A04 hypotheses have already been declared; these four add to the visible
search history, even though two are parameter neighbors and one is a control.
