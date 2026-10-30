# Order book history

The [microstructure research workspace](microstructure-research.md) adds raw-event
features, heatmap/replay, event studies, a cost-aware tester and a local experiment
notebook. Open **Liquidity research** or **Event study** in navigation. This
compact history view remains available for longer summary windows.

Open **Order book history** in the workspace navigation, or follow the link from
Logs. `/order-book-history?account=<owned ProjectX account>` displays recorded
MNQ mid-price, bid/ask resting contract totals, and normalized imbalance on a
shared timeline. It does not run a strategy or send trading commands.

Select up to one hour using local-time inputs, choose all observed levels or the
nearest 10/20/50/100 levels, and load the window. Drag across the chart to zoom;
click a moment or use the focused chart's arrow keys to inspect a sample.
**Inspect preceding 5 minutes** loads five minutes before and one minute after
that sample. CSV export contains only the visible window, including scope,
quality flags, capture ID, and generation.

The selected-moment card compares mid-price after 5, 15, and 60 seconds using the
first subsequent observation at or after that horizon, with at most one second
of tolerance. Comparisons stop at flagged observations, contract/run/generation
changes, or gaps over two seconds. They only use the loaded window. Chart lines
also break at those boundaries; blank intervals are not interpolated. Observed
side freshness does not establish full exchange-book completeness or predictive
value. Quantity comparisons include actual level counts for both sides.

## Local and host data sources

`GET /api/accounts/{account_id}/order-book-history` requires the normal signed-in
user and account ownership checks. Market depth is user/instrument data, not a
different dataset for every account. The owner hash is derived from the
authenticated identity; the caller cannot choose an owner, filesystem path, SSH
host or command. Responses use `Cache-Control: no-store`.

By default the reader uses
`TOPSIGNAL_DATABENTO_CACHE_DIR/depth-recordings-v1/totals`. It reads only committed
summary gzip files, checks compressed/raw SHA-256 and receipt/scope metadata, and
skips partial files. At most one hour, 20,000 points and 64 MiB of decompressed
segments can be read per request. Inventory is capped at 50,000 manifests; larger
inventories require archival review. Corrupt files are skipped with a visible
warning. Two concurrent history reads are allowed per backend process.

For this development PC, `backend/storage/depth-history-source.json` (ignored by
Git) configures the existing private SSH connection to peteamdpc. Its fields are
`label`, `ssh_target`, `identity_file`, `python_path`, `reader_path`, and
`recording_root`. This is server-owned configuration, never HTTP input. The host
reader is installed at
`C:/ProgramData/TopSignal/tools/depth-history-v1/read_depth_history.py`, using the
existing pinned `dashboard-18` Python interpreter. The reader is standalone and
read-only; it neither imports the trading backend nor needs a host service
restart. Source files stay on the host and no history is written to Supabase.

Each request sends only the authenticated owner's hash and bounded time/scope
query over SSH. Existing key/host-key trust is reused with batch mode, strict
host-key checking, forwarding disabled, and a 25-second process timeout. OAuth
tokens and brokerage credentials are not sent to the reader. An unavailable
host produces a visible error, not a silent fallback to stale local data.

Only sealed segments appear, normally about a minute behind collection. Totals
history currently begins October 30, 2026 at 13:04:50 Eastern. Earlier raw depth
was not backfilled as part of this page.

## October 30 validation

35 backend and 8 frontend tests passed, including owner isolation, account checks
before host access, committed-file selection, corruption/traversal rejection,
range bounds, host-command restrictions, cancellation on account changes, and
gap-aware price comparisons. The frontend production build passed.

The authenticated local page loaded 3,392 real measurements from peteamdpc with
zero skipped segments. Browser checks verified drag zoom and selected-moment
readouts. A CSV export of the zoomed interval contained 877 rows spanning
17:07:30–17:11:22 UTC. Deployment used the existing SSH identity and did not
change host tasks, recording, trading settings or cloud persistence. A screenshot
is retained under the ignored local cache audit directory
`backend/storage/databento/audits/order-book-history-20261030/`.
