# Continuous observed contract totals

Enabled October 30, 2026 at approximately 13:05 Eastern in the independent host
depth recorder. The main TopSignal scheduled task XML and backend listener PID
were unchanged across activation. Only the recorder was stopped gracefully and
restarted; its old run sealed before the new stream connected. No broker routes,
bot configuration, cloud tables, storage budgets, or retention rules changed.

Three recorder Python files were staged and SHA-256 checked before installation.
A synthetic two-sided book and compressed-file verification passed against the
actual pinned `dashboard-18` host Python environment. Original recorder files
and configuration were copied and hash verified before replacement; recovery
copies remain in the bundle's `staged-totals-20261030/originals/` directory.
The new `record_contract_totals` configuration flag enables the compact stream.

At 17:05:00 UTC, status showed 7,032 raw events and 40 summary records, zero
errors, and fresh two-sided observations. The first completed summary segment
covered 17:04:50–17:05:50 UTC: 228 records, 336,872 uncompressed bytes and 39,643
compressed bytes. Its gzip content, both SHA-256 hashes, receipt order and scope
passed verification on the host and after copying the completed segment to the
development PC. The active partial file remained on the host.

That first minute extrapolates to about 55 MB per 23-hour day for summary data,
excluding manifests and backups. This is a short observation, not a full-session
forecast. The compact stream is additional to existing raw capture. Existing
scheduled backups include completed summary segments through their unchanged
segment-manifest selection logic.

Validation covered 97 distinct tests across order-book reconstruction, recorder,
summary calculations, and cache backup. Cases include volume rather than
currentVolume, quantity replacement/removal, nearest-level sorting, sampling,
reconnects, reset/epoch boundaries, stale timestamps, storage refusal and backup
integrity. Summary measurements remain explicitly incomplete/unverified at the
provider-snapshot level; no predictive claim or model integration was made.

Private deployment receipts, activation/smoke scripts, and the verified first
summary segment are retained under
`backend/storage/databento/audits/depth-totals-20261030/` (ignored by Git).
See [the recording guide](../../topbot-depth-recording.md#contract-totals-and-imbalance-history)
for fields, timing, quality flags, paths, and verification.
