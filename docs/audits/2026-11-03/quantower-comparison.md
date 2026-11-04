# Quantower / ProjectX trade comparison

The first live comparison agrees on trade counts, contract volume, delta, and
the direction of uniquely matchable prints. This is a short cross-platform
consistency check, not proof of full-session coverage or exchange-native flags.

## Capture

- Quantower 1.147.9 on the development PC, connected through ProjectX / TopstepX.
- Symbol: MNQZ6, Micro E-mini Nasdaq-100 December 2026; recorded contract
  `CON.F.US.MNQ.Z26`, `data_live=false` subscription.
- Time & Sales: real time, aggregation off, 100,000-row limit, coloring by
  aggressor flag, aggressor source **Data provider**, explicit Aggressor column.
- CSV export includes all columns. Bid/ask quote fields are zero; trade IDs,
  buyer and seller identifiers are empty. Do not use these fields as evidence.
- Exported range: November 3, 2026, 8:58:03.904–8:59:06.744 p.m. Eastern.
- Comparison interval: **8:58:05 inclusive–8:59:05 exclusive p.m. EST**,
  equivalent to November 4, 01:58:05–01:59:05 UTC.
- Quantower uses Local time; Windows time zone was checked as Eastern Standard
  Time. The CSV has no date or time zone; both were supplied explicitly.

## Results

| Metric | Quantower | Recorded ProjectX |
| --- | ---: | ---: |
| Trade prints | 1,907 | 1,907 |
| Buy prints | 931 | 931 |
| Sell prints | 976 | 976 |
| Buy contracts | 961 | 961 |
| Sell contracts | 996 | 996 |
| Unknown contracts | 0 | 0 |
| Delta contracts | -35 | -35 |

All 1,907 occurrences can be matched by provider timestamp to millisecond
precision, price, and size, preserving repeated identical prints. There are
714 unique one-to-one candidate pairs: all agree on direction, with no unknown
labels or mismatches. Another 453 matching keys have repeated occurrences and
are excluded from individual direction-agreement claims. No provider trade-ID
matching is possible from this CSV.

The copied sealed recording segments passed compressed and uncompressed
checksums, envelope scope, sequence-index, and event-count checks. No recording
boundary events or provider/receipt delays outside 0–2 seconds were found in
the compared interval. These checks do not certify exchange completeness.

## Reproduction and retained evidence

Use `backend/tools/compare_quantower_trades.py` with an explicit CSV date/time
zone, owner, delivery contract, subscription, UTC interval, and output path.
It is an offline reader with no broker, database, or trading dependency. The
three targeted tests cover retained duplicate prints, reversed labels,
exclusive end bounds, and the Eastern-to-UTC calendar-date transition.

Raw evidence is retained locally under
`backend/storage/databento/quantower-comparison/2026-11-03/`:

- `mnqz6-initial.csv` (SHA-256
  `343d1164c402c94b7e50e58b1ee32b2e75388942da02bfeb1dd96dea737e364b`)
- `comparison-initial.json`, including source hashes and partial-minute totals
- `raw/2026-11-04/1378a1df1c8449c6847fb95a78a6d1a7/`, six original sealed
  segment/manifest pairs copied read-only from PeteAMDPC

The source archives remain on PeteAMDPC. The CSV additionally remains in
Documents as `Quantower-MNQZ6-initial-20261103.csv`; it still needs an off-device
backup with the local evidence. No raw input was written to Supabase or Git.

The longer tape window closed before a second export, so this result uses only
the saved 60-second overlap. A remaining MNQZ6 Time & Sales panel was observed
streaming Buy/Sell labels afterward. No automated continuous export was installed.

Both platforms use ProjectX. This result supports the current 0=Buy / 1=Sell
mapping's consistency with Quantower's provider-mode aggressor labels for this
sample; it does not establish independent exchange provenance or warrant a
session-long bullish/bearish conclusion. Existing verification gates, bot entry
rules, configuration, and production deployment remain unchanged. Quantower on
PeteAMDPC remains stopped with its launch task disabled pending the PC upgrade.
