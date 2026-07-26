# Topstep Live trade export compatibility

TopSignal imports completed Topstep Live trades into a local CSV-import account. Imports never update an existing trade's financial values.

## Supported files

- CSV encoded as UTF-8, with or without a UTF-8 BOM. Comma, semicolon, tab, and pipe delimiters are detected strictly from the header/sample.
- Excel Open XML workbooks with the `.xlsx` extension. The active worksheet is read with cell-format metadata so date-only timestamp cells can be rejected safely.
- Maximum upload size: 10 MiB.
- Maximum trade rows: 5,000.

Compatibility is fixture-backed for UTF-8 CSV with and without a BOM, comma/semicolon/tab/pipe delimiters, and `.xlsx` OpenXML uploads. The XLSX golden archive is stored as base64 text so the binary input remains reviewable; tests strictly decode it before parsing. Macro-enabled `.xlsm` packages are rejected because no genuine Topstep XLSM export has been verified.

UTF-16 and Windows-1252 CSV files are intentionally rejected. No verified Topstep fixture in those encodings is available, so accepting them by heuristic detection would risk silently changing identifiers or financial fields.

## Required fields

The canonical Topstep headers are:

`Id`, `ContractName`, `EnteredAt`, `ExitedAt`, `EntryPrice`, `ExitPrice`, `Fees`, `PnL`, `Size`, `Type`, `TradeDay`, and `Commissions`.

`TradeDuration` is optional. Friendly aliases already covered by regression tests (for example `Trade Id`, `Contract Name`, `Entered At`, `P&L`, `Quantity`, and `Direction`) are also accepted. Missing IDs or financial fields are not synthesized.

## Validation and identity rules

- Entry and exit values must include a date and time.
- Timestamps carrying a UTC offset are unambiguous and preferred.
- Naive timestamps are interpreted as `America/New_York`; nonexistent spring-forward wall times and ambiguous fall-back wall times are rejected.
- `TradeDay` must match the shared futures trading-day calculation: 6:00 PM ET begins the following trading day.
- `Size` must be a positive whole number from 1 through 10,000. Integer-shaped spreadsheet values such as `3.0` are accepted.
- Exact repeated identities with identical normalized economic fields are harmless duplicates.
- A repeated source ID (or proven order-ID/exit-time fallback identity) with any different timestamp, contract, symbol, direction, quantity, price, P&L, fee, commission, net P&L, or trading day is a blocking conflict.
- No verified Topstep split-fill export establishes that repeated IDs should be aggregated. Repeated IDs with different economics are therefore flagged for review and never silently merged or overwritten.

## Preview, confirmation, and recovery

Preview parses the uploaded file once and persists an expiring normalized manifest. The browser receives an opaque token; only its SHA-256 digest is stored. Tokens are authenticated and scoped to the owning user and account, expire after 30 minutes, and retain status metadata for seven days.

Confirmation sends only the preview token. It rechecks the deduplication snapshot, refuses conflicts or stale previews, and atomically commits the import batch, trade rows, and recoverable outcome. The status endpoint can recover a committed outcome after a browser disconnect or restart:

`POST /api/accounts/{account_id}/trade-imports/status`

The status request carries `{ "preview_token": "..." }` in its JSON body. The token never appears in the URL, query string, or application logs. A `pending` response with `confirmation_retryable: true` means the previous attempt rolled back or never reached the server and confirmation may be retried with the same token. A `committed` response includes the durable result; retrying confirmation is idempotent and cannot insert a second copy.

Logs contain only timings, counts, and outcome categories. They never include filenames, file hashes, preview tokens, trade IDs, account/user IDs, P&L values, credentials, URLs, or row contents.
