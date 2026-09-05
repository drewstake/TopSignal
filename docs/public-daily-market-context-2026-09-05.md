# Public daily market context

The authenticated Data Hub can manually collect the Federal Reserve Board's H.15 daily 10-year Treasury constant-maturity yield. It uses the existing `projectx_market_candles` storage with a separate `PUBLIC.FED.H15.US10Y` identifier and `federal_reserve_h15` source. No schema migration or provider key is needed. No execution worker or order API calls this collector.

## Sources and permitted use

- [Federal Reserve H.15 data downloads](https://www.federalreserve.gov/datadownload/Choose.aspx?rel=H15) provide the Treasury Constant Maturities preformatted CSV. The collector selects the exact daily series `RIFLGFCY10_N.B`, checks percent-per-year units and multiplier 1, and requests at most 365 completed calendar dates. H.15 attributes the underlying constant-maturity yields to the U.S. Treasury. The [Federal Reserve website disclaimer](https://www.federalreserve.gov/disclaimer.htm) permits copying and distribution of Board information in the public domain unless otherwise indicated, with attribution. Source attribution is retained in storage and API output.
- [H.15's current release](https://www.federalreserve.gov/releases/h15/) is scheduled at 4:15 p.m. on publication business days. The historical CSV does not provide the first publication or revision timestamp for each observation. No timestamp is inferred from the nominal release clock.
- [Cboe's VIX historical data page](https://www.cboe.com/tradable_products/vix/vix_historical_data) supplies daily index OHLC. The adapter is disabled by default (`CBOE_VIX_ENABLED=false`). An operator can set `CBOE_VIX_ENABLED=true` only with use rights appropriate to the intended storage and use under [Cboe's terms](https://www.cboe.com/terms). No VIX download occurs through this collector while disabled. Copyright and provenance remain attached to stored rows and API output.

The Treasury's documented annual and monthly XML endpoints timed out during research, so the implemented US10Y collector uses the working official H.15 download directly. There is no alternate-site fallback, access-denial workaround, or automatic retry. The Federal Reserve has announced future retirement of its Data Download Program; a changed or unavailable endpoint produces an explicit failure without fabricating data.

## Time and value semantics

`candle_timestamp` represents midnight at the beginning of the observation date in America/New_York, converted to UTC. It is a date marker, not a market opening tick. The end-of-day bound is the following local midnight; DST dates therefore span 23 or 25 hours when applicable. Only dates completed before the request are accepted. `published_at` remains null because the CSV does not identify historical publication times.

`fetched_at` is recorded after the complete response arrives. The original timestamp and values never change on refresh. Repeated matching rows count as unchanged; conflicting later revisions count as conflicts and are not imported. This is a first-collected history, not a vintage-complete economic database. A historical value downloaded today cannot become available to a decision yesterday: public context always requires `fetched_at <= as_of`, regardless of the generic candle API's retrospective mode. `available_at` is the later of first collection and the end-of-day bound. Freshness uses the underlying observation date, so downloading old history does not make that history current.

US10Y is a yield in percent per year, not a bond price. The existing table's four mandatory price columns contain the same single yield observation and carry explicit `yield_point_in_required_price_columns` provenance. Context exposes only the value in `close`, `value_unit=percent_per_year`, `observation_kind=daily_yield_observation`, and change in basis points through `change_bps`. It omits volume and price-percent change. Public daily context applies independently of the selected ProjectX live/paper mode and is tagged `data_mode=public_daily`.

## API

`GET /api/market-data/public-status` returns `generated_at` and `sources`, with `symbol`, `source`, `label`, `enabled`, `status` (`ready`, `stored`, or `disabled`), `stored_rows`, `latest_observation_date`, `last_collected_at`, `source_url`, and `data_notice`. `stored` means local observations exist; it does not assert a successful current connection or real-time freshness.

`POST /api/market-data/refresh-public` accepts `{"symbols":["US10Y","VIX"],"days":365}`. Dates are bounded to 1–365 days; symbols are restricted to these two. It returns `started_at`, `finished_at`, and per-source `items` with `status` (`updated`, `unavailable`, `failed`, or `disabled`), row counts, sanitized `detail`, and `data_notice`. It shares the Data Hub mutation lock. Authentication and per-user storage apply to both endpoints.

Each enabled symbol makes one request to its fixed official endpoint, without redirects or arbitrary URLs. Connection/read timeouts are 5/12 seconds, response size is limited to 2 MB, and CSV input is limited to 15,000 rows and 64 columns. The collector reads the response in bounded chunks and enforces an elapsed-time check. The VIX historical download is a whole-history CSV because the official endpoint has no date query; only the requested completed date window is parsed into observations.

## Verification

The isolated real H.15 smoke completed in approximately 3.7 seconds on September 5, 2026 UTC. It inserted 250 daily observations over the requested 365-calendar-day window. The latest returned value was 4.77% for September 3, following 4.79% on September 2: a change of -2 basis points. Cboe reported disabled and made no request. No operator database was modified. The output is stored at `backend/storage/public-market-context-smoke.json`.

The following command passed 52 tests, covering parsers, series/units, missing dates, revision conflicts, first collection, DST, stale data, tenant boundaries, disabled-source behavior, fixed endpoint requests, route authentication, validation and locking, plus existing Data Hub and decision-context behavior:

```powershell
& backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py tests/test_public_market_context.py tests/test_market_data_workspace.py tests/test_market_context_bundle.py -q
```
