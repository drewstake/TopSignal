# Bounded historical CME equity calendar audit

Recorded September 4, 2026 New York time, before the corrected risk-parity
hypothesis batch. This fixes identified session defects in the reused MNQ
history. It is not a complete certification of every historical exchange hour.

## Findings and source confidence

All implementation times below are America/New_York, with daylight saving
handled by the time zone. The source schedules use Chicago/Central time.

| Dates | Equity Globex rule | Evidence and confidence |
| --- | --- | --- |
| July 3, 2019 | Close 13:15; reopen 18:00 | Dated [Paragon 2019 schedule](https://www.paragonglobalmarkets.com/wp-content/uploads/2019/06/PGM_Independence-Day-Holiday-Schedule_2019.pdf), ES/NQ/YM Globex row: Wednesday close 12:15 Chicago. Provider publication, not a recovered original CME notice. |
| July 3, 2023 | Close 13:15; reopen 18:00 | [DTN 2023 service hours](https://iqhelp.dtn.com/fourth-of-july-service-hours-2023/) explicitly identify CME Globex equities closing 12:15 Central and reopening 17:00. Primary publication about DTN service; corroborates the calendar, but is not a CME archive. |
| July 3, 2024 | Close 13:15; reopen 18:00 | [DTN 2024 service hours](https://iqhelp.dtn.com/fourth-of-july-service-hours-2024/), same explicit equity times. Provider-level evidence. |
| July 3, 2025 | Close 13:15; reopen 18:00 | [DTN 2025 service hours](https://iqhelp.dtn.com/fourth-of-july-service-hours-2025/), corroborated by [Paragon 2025 schedule](https://www.paragonglobalmarkets.com/wp-content/uploads/2025/07/PGM_Independence-Day-Holiday-Schedule_2025.pdf). Provider-level evidence. |
| April 10, 2020 | Closed, including Thursday evening; normal Sunday reopen | [AMP April 9, 2020 notice](https://www.ampfutures.com/news/holiday-trading-schedule-good-friday-2020) and its [dated table](https://www.ampfutures.com/hubfs/CME%20Group%20Globex%20Good%20Friday%20Holiday%20Schedule%20-%20April%209%2C2020%20to%20April%2013%2C%202020.png). Equity row visually verified: Thursday regular close 16:00 Central, Friday closed, Sunday 17:00 reopen. AMP attributes this summary to the Globex Control Center; the original CME-hosted notice was not recovered. |
| April 15, 2022 | Closed, including Thursday evening; normal Sunday reopen | [AMP 2022 notice](https://www.ampfutures.com/news/holiday-trading-schedule-good-friday-2022). Dated broker service publication, corroborated by the empty observed session. |
| March 29, 2024 | Closed, including Thursday evening; normal Sunday reopen | [DTN 2024 service hours](https://iqhelp.dtn.com/good-friday-holiday-hours-2024/) explicitly say no CME overnight session on Thursday and closed Friday. |
| April 18, 2025 | Closed, including Thursday evening; normal Sunday reopen | [DTN 2025 service hours](https://iqhelp.dtn.com/good-friday-holiday-hours-2025/) explicitly identify Thursday April 17 without overnight CME trading and Friday April 18 closed. Unrelated template dates elsewhere in the notice are not relied upon. |
| April 7, 2023 | Abbreviated session, close 09:15 | **Original CME schedule recovered:** [Good Friday schedule](https://www.cmegroup.com/files/good-friday.pdf), retrieved September 4, 2026, is still the April 6–7, 2023 table. EQUITIES reopen Thursday 17:00 Central and close Friday 08:15 Central. |
| April 2, 2021 and April 3, 2026 | Abbreviated session, close 09:15; existing rule retained | [Paragon 2021 table](https://www.paragonglobalmarkets.com/wp-content/uploads/2021/03/PGM_Good-Friday-Trading-Schedule_2021.pdf) and [Ironbeam 2026 notice](https://www.ironbeam.com/good-friday-2026-futures-trading-hours/) specify 08:15 Central. Provider-level evidence corroborated by observed MNQ minutes. |
| January 9, 2025 | Close 09:30; reopen 18:00 | **Original CME evidence:** [SER-9499R](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2025/01/ser-9499r.pdf) and [Globex mourning schedule](https://www.cmegroup.com/content/dam/cmegroup/trading-hours/files/day-of-mourning-january-9-2024.pdf). The latter filename says 2024, but its document explicitly says January 9, **2025** and U.S. equities close 08:30 Central. |

The July 3 rules are explicit date overrides, not an inferred annual pattern.
Contemporaneous provider notices and matching observed shutdowns support their
use for this bounded research correction. Attempts to recover the corresponding
original CME holiday trading tables were unsuccessful. CME's
[2025 independence settlement table](https://www.cmegroup.com/tools-information/holiday-calendar/files/2025/us-independence-day-settlement-times-2025.pdf)
lists noon Central equity **settlement** on July 3; that is not the 12:15
Central trading close and was not used to set a trading deadline.

Good Friday is not uniformly a full closure. The dated 2021, 2023 and 2026
sessions include employment-report trading. The implementation adds full-close
overrides only for the four audited dates, preserving the known abbreviated
sessions. It does not invent future first-Friday or employment-release rules.
An original notice should be checked before extending this calendar to a new
year; the pre-existing recurring approximation is not such verification.

The earlier calendar-v5 correction also remains: the 16:15–16:30 New York
equity halt applies only before June 28, 2021, per
[CME SER-8788 section 5](https://www.cmegroup.com/content/dam/cmegroup/notices/ser/2021/06/SER-8788.pdf).
The regular 17:00–18:00 maintenance break remains.

## Observed data corroboration and material effect

Read-only inspection of the existing cache, midnight through 17:00 New York:

| Date group | Observed minute starts |
| --- | --- |
| July 3, 2019 | 782 records, 00:01 through 13:14; sparse early history has other absent minutes |
| July 3, 2023/2024/2025 | 795 records each, 00:00 through 13:14 |
| Good Friday 2020/2022/2024/2025 | Zero records each |
| Good Friday 2021/2023/2026 | 555 records each, 00:00 through 09:14 |
| January 9, 2025 | 570 records, 00:00 through 09:29 |

These observations corroborate dated notices. Absence of OHLCV alone cannot
establish a holiday, an outage, an unscheduled halt, or a no-trade minute.

The interrupted opening-range run's trade 1431 entered July 3, 2025 at 10:05
New York and was flattened at the next observed 18:00 open because the earlier
calendar omitted that afternoon closure. With the verified 13:15 close, the
already registered research rule's deadline is 13:10. A direct fixture test
verifies false at 13:09 and true at 13:10. No old ledger is edited and no new
profitability outcome is claimed by this calendar audit.

New date exceptions are restricted to recognized CME equity roots. Unknown
products retain the existing generic fallback; their product-specific holiday
hours are not established by this work. Other historical dates and special
halts have not undergone a complete archival audit. Live holiday rules and a
future untouched evaluation require their own current schedule verification.

## Cache identity and verification

Both source archives remain unchanged:

- Definitions SHA-256: `911a74ef40ced1f0c9be513227d9fe082a2a8dde0355c06f0474a1f7008df675`.
- OHLCV SHA-256: `1872f93fd37c59b35ca7f72f972c3644a5d1b91301ff3b46f78d9c2e4925a5d1`.

| Cache | Source fingerprint |
| --- | --- |
| Original v4 | `cd56b8dbe08abc26b6bbbb9351e337984c603fe2562942ecb85ad0b9383a897d` |
| Historical halt v5 | `e2ad891fa28bc28694e24ad910487cb74ec68e79a242c314e440ae56549f2086` |
| Dated holiday v6 | `e900ae486308de577f0945e21cd54821ed2b206c027761d1973563a9085b4d6a` |

Cache v6 lives in `backend/storage/databento-calendar-v6`; version directory
`versions/e900ae486308-02fc39a270fc`. Format is 6; resampling policy is
`globex_session_anchored_complete_ohlcv_v4_verified_holiday_dates`.
The separate rebuild completed in 54.14 seconds. Older cache directories and
experiment source snapshots were preserved.

The default `backend/storage/databento` has subsequently been rebuilt and
verified at the same format-6 fingerprint, version
`versions/e900ae486308-c79709614b36`. All 60 NPY files across its six timeframes
match the separate research-v6 cache byte-for-byte; that research directory and
all original archives remain unchanged. The original format-4 reference is
preserved at `backend/storage/databento-format4-reference`, with its existing
absolute archive paths and verified original-reader compatibility. See the
[default migration record](topbot-data-audit-2026-09-04.md#verified-default-cache-migration)
for paths, hashes, the preserved Windows publication failure, and baseline
reproduction instructions.

Every NPY array, including timestamps, OHLCV, delivery and session metadata,
was SHA-256 compared for the 1m, 5m and 15m materializations: all are byte
identical to v5. Calendar-aware longer complete bars change as expected:

| Timeframe | v5 records | v6 records |
| --- | ---: | ---: |
| 1m | 2,532,300 | 2,532,300 |
| 5m | 504,384 | 504,384 |
| 15m | 167,208 | 167,208 |
| 1h | 41,467 | 41,472 |
| 4h | 10,588 | 10,593 |
| 1d | 1,655 | 1,659 |

Full-period engine initialization, without evaluating or replaying any strategy,
recomputed coverage against the new calendar. The comparison uses the original
baseline entry hours, 09:30–15:45 New York, and its identical observed streams:

| Coverage measure | v5 | v6 |
| --- | ---: | ---: |
| 1m gaps / absent scheduled slots | 3,050 / 11,157 | 3,041 / 6,162 |
| 1m gaps / absent slots in entry hours | 47 / 1,083 | 42 / 103 |
| 5m signal gaps / absent scheduled slots | 1,615 / 4,394 | 1,606 / 3,395 |
| 5m signal gaps / absent slots in entry hours | 24 / 247 | 19 / 47 |

The remaining gaps are not silently repaired or interpolated. Full records and
per-array hashes are in the ignored local audit directory
`backend/storage/research/calendar-audits/20260905T003311.120782Z-historical-closures-5ef3bda4e165`:
`v5-observations.json`, `v6-materialization.json`, and
`v6-coverage-comparison.json`.

Focused offline verification passed **76 tests** across trading-day rules,
Databento local caching, causal roll resolution and the research runner. Tests
cover exact holiday boundaries, Thursday-night full closures, Sunday/evening
reopens, abbreviated Good Fridays, the research flatten deadline, and avoiding
unsupported date/product extrapolation. The wider risk/latency test suite is
recorded separately by the replay audit.
