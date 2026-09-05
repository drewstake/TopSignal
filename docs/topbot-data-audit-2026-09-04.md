# TopBot MNQ data and calendar audit — September 4, 2026

Audited from commit `592791b43c57a8593451837ce2acb15a7a706465`, with the
calendar and research roll-reader changes described below in the working tree.
The initial local audit used no broker credentials or provider requests. The
separately authorized ProjectX capture and structural QA are recorded below;
no orders, purchases, or subscription changes were made.

## Available data and prior exposure

The original cache was `backend/storage/databento`; its initial format-4 state
is now preserved at `backend/storage/databento-format4-reference`. The default
directory now uses format 6, as documented in the migration section below.
Its only root is MNQ. Both source ZIPs remain in the default directory and were
rehashed against their original manifest:

| Schema | Source archive | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| OHLCV-1m | `sources/GLBX-20260904-PSANB6M5GT/GLBX-20260904-PSANB6M5GT.zip` | 63,183,367 | `1872f93fd37c59b35ca7f72f972c3644a5d1b91301ff3b46f78d9c2e4925a5d1` |
| Definition | `sources/GLBX-20260904-G7KQLJS9K9/GLBX-20260904-G7KQLJS9K9.zip` | 4,698,122 | `911a74ef40ced1f0c9be513227d9fe082a2a8dde0355c06f0474a1f7008df675` |

The OHLCV archive holds 3,843,155 records: 3,780,543 outright rows and 62,612
spread/other rows. Parquet retains the outright rows; the continuous minute
series selects 2,532,300 rows across 1,858 trading dates. There are 60,306
definition rows and no local Statistics, Status, Trades, or quote/depth schema.
Thus OHLCV cannot independently prove spread, queue position, exact fill
ordering within a minute, or whether an absent minute is a feed gap versus no
trades. Databento documents that OHLCV timestamps identify interval starts and
that intervals with no trades have no record. Its daily schema uses UTC dates,
so this app instead aggregates minute bars by exchange session.
[Databento schema documentation](https://databento.com/docs/schemas-and-data-formats)

Actual source candle starts span **2019-05-05 22:03 UTC through 2026-07-10
20:19 UTC**, with exclusive end 20:20 UTC. Archive request metadata starts in
2010, but that does not mean the archive contains pre-2019 MNQ candles.
CME listed MNQ effective Sunday May 5 for trade date May 6, 2019; earlier NQ
data would be a different instrument/proxy.
[CME initial listing notice](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2019/04/SER-8360.pdf)

All original dates were already examined by previous strategy research. New
splits, corrected resampling, or a corrected replay do not make these dates an
untouched evaluation set. The Downloads directory contains duplicates of these
two ZIPs and a portable copy of the OHLCV archive, rather than additional dates.

The old source-validation JSON says `awaiting_definition`; that is an earlier
staging record. The current cache manifest and existing Definition ZIP establish
that definitions are now present. That source report also lists 16 provider-
degraded dates inside the candle range. Its structural checks do not certify
exchange-feed completeness. Preserve those quality flags when evaluating results.

## Calendar defect, correction, and isolated rebuild

The original calendar applied a 16:15–16:30 ET equity trading halt to every year.
CME eliminated that halt from **June 28, 2021**. Section 5 of the dated exchange
notice is explicit; a currently accessible general Micro FAQ still describes
the older halt and must not override the dated change.
[CME SER-8788, section 5](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2021/06/SER-8788.pdf)

The local feed independently agrees: the former halt contains 18,800 minute
bars from June 28, 2021 onward. Five earlier records are timestamped 16:29 ET
in 2020, and two records are timestamped 17:59 ET in 2020; these boundary cases
remain raw observations requiring explanation, not permission to manufacture
missing candles.

The stale calendar caused strict resampling to reject real, otherwise complete
session tails. The daily cache had only 409 days and just 8–9 per year after
2021. `trading_day.py` now applies the historical halt only before June 28,
2021. Daily 17:00–18:00 maintenance remains. This correction does **not** certify
every holiday or exceptional exchange closure; the rest of the recurring
calendar remains an approximation.

Cache format was raised from 4 to 5 and the resampling fingerprint revised so
old materializations cannot be silently reused by current code. The source
fingerprint includes cache format, so its new value denotes unchanged archives
processed under corrected rules, rather than newly acquired history.

An offline rebuild into the separate `backend/storage/databento-calendar-v5`
directory completed in 73.24 seconds, using 279,882,617 bytes. The original
cache and archives were preserved. New fingerprint:

```text
e2ad891fa28bc28694e24ad910487cb74ec68e79a242c314e440ae56549f2086
```

Original fingerprint:

```text
cd56b8dbe08abc26b6bbbb9351e337984c603fe2562942ecb85ad0b9383a897d
```

| Materialized series | Original rows | Corrected rows | Timestamp comparison |
| --- | ---: | ---: | --- |
| 1 minute | 2,532,300 | 2,532,300 | Identical |
| 5 minute | 504,384 | 504,384 | Identical |
| 15 minute | 167,208 | 167,208 | Identical |
| 1 hour | 41,467 | 41,467 | Identical |
| 4 hour | 9,338 | 10,588 | 1,250 restored |
| Session day | 409 | 1,655 | 1,246 restored |

Corrected daily counts by year are 13, 232, 251, 257, 256, 258, 253, and 135
for 2019 through partial 2026. Daily research still has substantial 2019
exclusions; the calendar fix alone does not remove sparse early trading or
other source/holiday issues. All six original timestamp arrays were strictly
increasing. Lower-timeframe row equality does not mean replay behavior is
unchanged, because replay session checks also use the calendar.

The original v5 build command, run from the repository root with the v5 source
state, was:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/build_databento_cache.py `
  backend/storage/databento/sources/GLBX-20260904-PSANB6M5GT/GLBX-20260904-PSANB6M5GT.zip `
  backend/storage/databento/sources/GLBX-20260904-G7KQLJS9K9/GLBX-20260904-G7KQLJS9K9.zip `
  --cache-dir backend/storage/databento-calendar-v5 --json
```

Later dated holiday corrections raised the cache format to 6; see the
[calendar audit](topbot-calendar-audit-2026-09-04.md). Reproducing the original
baseline still requires its original source revision and preserved format-4
cache. The current default has since been migrated as follows.

## Verified default-cache migration

The default `backend/storage/databento/current.json` now publishes format 6,
source fingerprint
`e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a`, version
`versions/e900ae486308-c79709614b36`. It was rebuilt offline from the same two
original archives. The separate research cache remains
`backend/storage/databento-calendar-v6`, version
`versions/e900ae486308-02fc39a270fc`.

Before publication, the old `current.json` and its entire referenced version
were copied to the new `backend/storage/databento-format4-reference` directory.
The resolved destination was verified inside the workspace and did not exist;
the copy refused overwrites. Its **244 files, 279,712,883 bytes**, match the
original hashes, including both copies of the original manifest. Archives were
not duplicated: the preserved manifest keeps their original absolute paths in
`backend/storage/databento/sources`. The original version also remains in place
at `backend/storage/databento/versions/cd56b8dbe08a-63dce188103d`.

Verification established:

- All **60 NPY files across six timeframes** in the new default are byte-for-byte
  identical to research v6. Their manifests differ only in build time and the
  generated version-directory name.
- The current cache reader opens all six default timeframes. Counts are
  2,532,300 (1m), 504,384 (5m), 167,208 (15m), 41,472 (1h), 10,593 (4h), and
  1,659 (session day).
- All **272 pre-existing default files other than `current.json`** remain
  unchanged, including source archives, old version files, and reports. No
  pre-existing file was deleted or moved.
- The entire research-v6 cache inventory remains unchanged. Settings,
  credentials, services, and frozen source code were not changed for migration.
- Exact cache/calendar modules from Git revision
  `592791b43c57a8593451837ce2acb15a7a706465`, loaded in memory in an offline
  process, successfully read all six original timeframes from the preserved
  format-4 reference. This was a reader check, not another strategy replay.

The first build failed when Windows denied the final new-directory publication;
the default pointer remained on format 4. A bounded wrapper collected temporary
objects before publication and allowed up to eight short permission retries.
The second build succeeded in **60.90 seconds**, on its first publication
attempt. This does not establish whether the first failure was an unreleased
handle or a transient external lock. The wrapper changed no frozen source.
Network access was disabled for both builds.

The preserved failure log, successful build log, source hashes, complete
before-inventory, and both reader/hash verification reports are under
`backend/storage/research/default-cache-v6-migration-20260904`:
`build.log`, `build-retry.log`, `before.json`, `verification.json`, and
`legacy-reader-verification.json`. Current pointer hashes are:

| Pointer | SHA-256 |
| --- | --- |
| Default v6 | `bb0e33e59f9cba705a872af16f63c22471005fc7883b4a9062c25d55dc91c8ee` |
| Preserved v4 | `ad83c33ff31978dfbbc12cfb48d23219a9c51d3d4c7e0adb6aadaaded29b76a4` |
| Separate research v6 | `14f9ac4c53b72c81c4c8900e3b22489c1b4111cf8cd921bc875b1bbfc79e9074` |

For the original baseline, use a separate clean checkout of
`592791b43c57a8593451837ce2acb15a7a706465`, the existing interpreter, and the
preserved reference cache. From that original checkout's root, choose fresh
output names and run:

```powershell
$legacyPython = 'C:\Users\drews\Development\TopSignal\backend\.venv\Scripts\python.exe'
$legacyCache = 'C:\Users\drews\Development\TopSignal\backend\storage\databento-format4-reference'
& $legacyPython backend/tools/benchmark_topbot_replay.py --cache-dir $legacyCache --days 3000 --holdout --output backend/storage/research/original-format4-baseline.json --trades-output backend/storage/research/original-format4-trades.json
```

That original CLI hardcodes **$1.20 per side** and has no commission override
flag. It reproduces the historical higher-cost baseline, not today's base fee.
For current-source comparisons, use format 6 and explicit **$0.61 per side**;
the [fee correction](topbot-fee-correction.md) supplies current commands and
paired $1.20 stress controls. Changing cache paths alone cannot reproduce an
old engine, calendar, or risk policy.

## Contract selection and causal liquidation support

The cache selects each session's delivery using the prior completed session's
volume, with definitions restricted to those known by the new session open.
The raw Parquet retains both deliveries around a roll. The exchange allows
participants to choose when to roll; its customary lead-month date is not a
mandatory execution instruction. A retrospective volume-driven switch must
not be implemented by selling at an earlier close after learning the switch.
[CME roll guidance](https://www.cmegroup.com/trading/equity-index/rolldates.html)

`backend/tools/research_rolls.py` provides
`RawContractRollResolver(cache_root)`, callable as `(previous_candle,
roll_timestamp) -> old_contract_minute | None`. It pins one immutable cache
manifest, checks the previous candle's source fingerprint, and filters raw
Parquet for the old symbol **and** instrument ID at the exact requested minute.
It will not substitute the new contract, an earlier close, or a later minute;
duplicates raise an explicit error. The caller must use only that minute's
opening price at roll time. High/low/close belong to the subsequent minute and
are not available for deciding or pricing that opening liquidation.

All **29** actual continuous one-minute delivery transitions were checked:
every transition has a raw old-contract minute at the new-delivery first
timestamp. This removes the raw-data obstacle to the replay agent's causal
roll exit. It does not establish real fill quality; opening-trade fills still
need conservative slippage, commissions, and resting-bracket treatment.

MNQ is $2 per index point with a 0.25-point minimum tick, implying $0.50 per tick.
[CME contract specifications](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.contractSpecs.html)

## Additional free data: preserved, unvalidated sample

A direct download on the vendor's MNQ product page provides a free sample with
newer dates. It is preserved privately under
`backend/storage/research/quarantine/firstratedata-mnq-20260904`, with a manifest.
Only names, documentation, header, and timestamp strings were inspected; no
price values, returns, or strategy outcomes were examined.
[FirstRate Data MNQ page](https://firstratedata.com/i/futures/MNQ)

- Archive: `frd_sample_futures_MNQ.zip`, 290,693 bytes.
- SHA-256: `5b9552d9ecf4a6f768a8f4e8f7b9dfaa5d8f44f13f24f87f206ed6e78fd70e53`.
- [Original public sample URL](https://frd001.s3.us-east-2.amazonaws.com/frd_sample_futures_MNQ.zip).
- 16,236 minute rows, **2026-08-19 00:00 through 2026-09-03 18:35 US Eastern**;
  14 calendar dates, no duplicate minute timestamps.
- Readme identifies timestamps as period starts and omits zero-volume intervals.

It is **not approved as an executable final evaluation set**. The sample does
not identify the delivery per row or the adjustment type, contains no contract
mapping, and has a stale readme field naming MNQZ24 as the last individual
contract. Vendor documentation says continuous rolls use custom rules and
midnight changeovers, so MNQU6 throughout the sample cannot merely be assumed.
[FirstRate adjustment method](https://firstratedata.com/about/price_adjustment#futures)

To clear quarantine, obtain the exact dated outright mapping and adjustment
type, confirm timestamp and price alignment against a verified source, and
freeze the candidate plus acceptance criteria before inspecting returns. Keep
source data private and attribute published analysis as the license requires.
[FirstRate data license](https://firstratedata.com/about/license)

Even after verification, roughly twelve trading sessions would be a small
confirmation sample, not adequate standalone evidence of durable profitability.
It must not become another tuning set while still being called untouched.

Databento advertises new-user historical credits, but an existing account's
eligibility/balance was not inspected and no billable API was called. Metadata
being free does not imply OHLCV downloads are free. Additional authorized,
verified MNQ outright OHLCV and definitions after July 10 remain the best path
to a larger chronological evaluation set.
[Databento pricing](https://databento.com/pricing),
[historical API documentation](https://databento.com/docs/api-reference-historical)

CME's DataMine API serves purchased data. Databento's PCAP page offers limited
samples and says larger recent samples require contacting it; no message or
request was sent. Neither was demonstrated to provide a sufficiently long,
free, directly usable new MNQ replay series in this audit.
[CME DataMine API](https://www.cmegroup.com/datamine/datamine-api.html),
[Databento PCAP samples](https://databento.com/pcaps)

## Focused verification

The following offline checks passed with no external connection attempts:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py `
  tests/test_trading_day.py tests/test_databento_local_cache.py -q
# 48 passed
& backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py `
  tests/test_bot_replay_arrays.py tests/test_trading_day.py tests/test_research_rolls.py -q
# 31 passed
```

Tests cover the last pre-change Friday and first post-change Monday, preservation
of maintenance, strict rejection of real missing minutes, scalar/array gap
agreement under both calendar regimes, exact old-delivery lookup, absent-minute
refusal, and source-fingerprint/time-boundary guards. The raw lookup was also
read-only checked against all 29 real rolls. These checks verify data handling,
not strategy profitability. Full replay metrics belong in the experiment log.

The first complete backend run exposed three additional tests that assumed a
2026 daily halt. Their historical closure fixtures were moved to 2020 and a
current-2026 order-book test now verifies that the stream remains connected and
continues accepting depth through 16:15–16:30 ET. The targeted 12 cases passed.
The complete offline rerun then passed **1,510 tests**, with **8 PostgreSQL
integration tests skipped** because no disposable PostgreSQL was configured;
external connection attempts were zero. The original failure log is preserved
as `backend/storage/research/audit-backend-tests-20260904.log`; the successful
rerun is `backend/storage/research/audit-backend-tests-20260904-corrected.log`.

## Additional ProjectX data: mapped and quarantined, not evaluated

With explicit authorization to use the existing entitlement, a restricted
read-only probe authenticated through the project's established credential
mechanism. The database transaction was read-only. Only `Auth/loginKey`,
`Contract/searchById`, and `History/retrieveBars` were permitted; no accounts,
trade history, orders, bot startup, credential writes, or subscription changes
were involved. Credentials and tokens remained in memory and are absent from
saved responses and reports. Historical API access worked without a purchase.
Topstep documents historical data as part of its API subscription; this audit
did not inspect billing records or establish a general free-data entitlement.
[Topstep API access](https://help.topstep.com/en/articles/11187768-topstepx-api-access)

The dated lookup returned `CON.F.US.MNQ.U26`, name `MNQU6`, description
`Micro E-mini Nasdaq-100: September 2026`, symbol ID `F.US.MNQ`, tick size
0.25 points, and tick value $0.50. These agree with the Databento definition
for `MNQU6@2026`, instrument ID `42004800`, expiration timestamp
`1789738200000000000` nanoseconds, and $2 per index point. A bounded request
for **2026-07-09 13:30–13:59 UTC** returned 30 minutes, all exactly matching
the already-exposed raw Databento contract's timestamps and OHLCV. This supports
the requested dated-contract mapping and period-start timestamp convention for
the observed interval; it is not a guarantee of every historical API response.
[Dated contract lookup](https://gateway.docs.projectx.com/docs/api-reference/market-data/search-contracts-by-id/),
[Historical bars](https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/)

The initial newer request returned **16,236 minutes**, from **2026-08-19
04:00 UTC through 2026-09-03 22:35 UTC**. A timestamp-only comparison found
exact equality with all 16,236 FirstRate sample timestamps after interpreting
the latter as `America/New_York`. Neither source had duplicates, missing
regular-session minutes, or out-of-session minutes in this interval. **The
initial capture inspected timestamps only.** The structural QA described below
subsequently checked numeric validity without logging price or volume values;
no new returns or strategy performance were evaluated.
FirstRate's undocumented adjustment/contract mapping remains unresolved; equal
timestamp coverage alone does not resolve it.

The capture was then extended in six requests of at most ten calendar days,
each below the documented 20,000-bar response limit, through the latest fully
closed trading day. The resulting separate pool contains **55,240 unique
minute bars**, starting **2026-07-10 20:20 UTC** and ending **2026-09-04 20:59
UTC** inclusive. This is exactly **40 complete sessions of 1,380 minutes each,
plus July 10's last 40 minutes**. All expected regular-session timestamps are
present, with no duplicates within or across request windows. This bounded
July 10–September 4 interval has no scheduled US holiday session; its timestamp
check is not a validation of the general historical holiday calendar. The same
dated metadata was verified again, and every requested timestamp precedes the
known contract expiration.

All artifacts remain in ignored local quarantine:

- `backend/storage/research/quarantine/projectx-mnqu26-20260904/manifest.json`
  records the first schema-guard stop. The provider returned undocumented `d`
  and `k` bar fields alongside the documented OHLCV fields.
- `attempt02/` contains the successful July 9 comparison and initial
  August–September capture, with a timestamp-only comparison report.
- `complete-newer-pool/` contains six non-overlapping response files, the dated
  lookup, and `manifest.json`, recording request bounds, hashes, retrieval
  times, per-window coverage, and counts by exchange trading day.

Responses are canonical JSON projections of the documented `t/o/h/l/c/v`
fields, not raw HTTP bytes. Undocumented `d/k` values were discarded without
analysis; hashes of the unprojected parsed responses are recorded separately.
The scripts record only fixed error categories, never provider error text or
authentication responses. Existing captures were preserved rather than
overwritten. All stored artifact hashes were rechecked successfully.

**The entire newer pool remains one reserved evaluation pool, with no tuning
splits and no strategy/return evaluation permission before candidate freeze.**
Nothing was inserted into application candles or the Databento replay cache.
This source does not silently replace Databento historical replay. Before a
separate evaluation, freeze the candidate and acceptance criteria and validate
the new source's execution assumptions. Forty sessions
cannot by themselves satisfy the research protocol's six-month/200-trade
requirement, and the FirstRate overlap provides no additional independent
market observations.

### Structural QA of the reserved pool

An additional offline pass checked all **55,240 stored bars** and saved the
additive `complete-newer-pool/structural-qa.json`. All checks passed: finite,
positive OHLC; consistent high/low bounds around open and close; exact 0.25-point
tick alignment; finite, nonnegative integer volume; exact documented row and
response schemas; minute-aligned unique timestamps; completed bars within
their request bounds and before contract expiry; and matching dated source
metadata and artifact hashes. **Zero-volume rows: 0. Structural errors: 0.**
The checker also passed twelve fixed synthetic validity/corruption checks.

Only counts, booleans, fixed error categories, file references, and hashes were
logged. No price or volume magnitudes, returns, distributions, or strategy
results were exposed or evaluated. No repairs were applied; the pool remains
reserved. The recorded bar source is **ProjectX**, with Databento used only for
the separate dated-definition/reference comparison. No reusable loader, cache
integration, frozen engine change, or further provider request was made for
this QA step.
