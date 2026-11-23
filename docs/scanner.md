# News and price-move Scanner

The authenticated Scanner tab and workspace alert banner read public market events from the application API. One independent Windows worker collects them, queues notifications and runs AI follow-ups. It has no trading endpoints, account selector or broker credential. Demo Mode serves a fixed local example.

## Install on peteamdpc

Use the normal [immutable release procedure](releasing.md). The installer targets the verified active release. Before installation, check the local authority, database size with `SELECT pg_database_size(current_database())`, free system/data disk space and the backup role. At 100 events/day and 8 KiB/event including indexes, jobs, links and a bounded excerpt, allow roughly 285 MiB/year plus WAL and backup copies. First successful polls retain the preceding day and up to three older items per source. Health rows are overwritten in place. Price windows live in memory. There is no automatic data deletion.

From elevated PowerShell on the host, supply the normal limited service credential and the SEC identifying contact.

```powershell
& "$release\scripts\install-scanner-task.ps1" -RepoRoot $release -Credential $serviceCredential -SecUserAgent 'TopSignal Operator contact@example.org' -StartNow
Get-ScheduledTask -TaskName 'TopSignal Scanner'
```

The installer creates a protected `C:\ProgramData\TopSignal\scanner-private\config.json`, a dedicated local PostgreSQL login and seven `topsignal_local.scanner_*` tables in a transaction. It grants the API and existing backup reader access. The existing repeatable-read execution backup includes this schema, table fingerprints and sequences. Keep the protected configuration in an encrypted operator backup. A restored database requires the role grants and normal authority recovery before connecting a worker. Scanner tables use their own schema-qualified metadata, outside the public execution/mirror table set. New installation refuses an existing task, directory or role.

The boot-triggered task runs under TopSignalSvc at scheduler priority 7 with a five-minute watchdog. The API and depth recorder stay at priority 4. For maintenance, disable before stopping so the watchdog respects the stop. Re-enable and start after updating the protected JSON. For a new worker version, point the scanner task action and working directory at that verified release's backend and Python. Preserve the private file and existing database.

```powershell
Disable-ScheduledTask -TaskName 'TopSignal Scanner'
Stop-ScheduledTask -TaskName 'TopSignal Scanner'
Enable-ScheduledTask -TaskName 'TopSignal Scanner'
Start-ScheduledTask -TaskName 'TopSignal Scanner'
```

The direct foreground entry point from the release's `backend` directory is `.\.venv\Scripts\python.exe -B -m app.scanner.worker --config C:\ProgramData\TopSignal\scanner-private\config.json`. A database advisory lock limits collection to one worker. The installer is for first provisioning only.

## Configuration and delivery

Edit private JSON as an administrator. Keep the generated loopback database URL and supplied SEC contact. Optional keys can be omitted.

| Key | Meaning |
| --- | --- |
| `watchlist` | Default AMD, NVDA, AVGO, INTC and TSM. Register another ticker under `additional_issuers` with `name`, `aliases` (array), ten-digit `cik` and an official RSS `releases` URL. |
| `discord_webhook` | Dedicated private Discord webhook. Local testing accepts an HTTP receiver at 127.0.0.1. Original alerts include the publisher, ticker, headline, link and UTC receipt. Mentions are disabled. |
| `alpaca_key`, `alpaca_secret`, `alpaca_feed` | Data-only Alpaca keys. Explicitly choose `iex` (default, single exchange), `sip` (entitled consolidated) or `delayed_sip` (requests historical SIP bars 16 minutes behind). Access errors and stale or missing bars appear in source health. |
| `openai_key`, `summary_model`, `search_model` | Both models default independently to `gpt-6-luna` on Responses. Only public text and market observations enter requests. Search accepts a cited, timestamped original with a supporting passage corroborated on the publisher's page. |
| `budget_usd`, `cycle_day` | $25 per cycle, starting at 00:00 UTC on day 1 by default. Set day 1–28 to match billing. Requests atomically reserve their worst-case estimated token/tool charge. Known usage settles the reservation. Ambiguous requests retain it through restart. |
| `rates`, `search_usd` | For each configured model, specify [USD per million input tokens, USD per million output tokens, context limit]. Default Luna [0.10, 0.50, 1050000], web search $0.01/call. Standard tier, one tool call maximum. Pricing estimates count cached inputs conservatively. Review on model or tariff changes. |

A database transaction saves the raw event, its notification and its independent AI job. The shell polls new alert IDs every 2.5 seconds. Discord delivery wakes on commit and persists its result. A restart or summary retry never resets a completed dispatch. Connection refusal, 429 and server errors back off. Discord has no end-to-end exactly-once guarantee. An interrupted send with an unknown outcome is held as failed for reconciliation. For a definite HTTP 4xx rejection, fix the destination, then run the foreground command with `--retry-delivery EVENT_ID`. Unconfigured delivery and AI jobs remain pending. Source health and per-event messages expose the missing configuration. The watchdog catches unexpected exits. Last-success and heartbeat times distinguish old samples from current data.

## Collection and detection

| Source | Method | Interval |
| --- | --- | --- |
| Hunterbrook | Newsletter RSS and main website RSS, GUID and canonical article URL | 30 s / 60 s |
| AMD, Intel | Official IR press-release RSS | 120 s |
| NVIDIA | NVIDIA's own GlobeNewswire distribution feed and NVIDIA Newsroom press-release RSS, polled independently | 30 s each |
| Broadcom | Official newsroom financial and product-release JSON listings, release ID and publication time | 120 s / 300 s |
| TSMC | Official Latest News HTML listing, article node and machine-readable publication time | 120 s |
| SEC | Per-CIK EDGAR submissions JSON, accession and acceptance timestamp. Material 8-K, 10-K, 10-Q, 6-K, 20-F and amendments alert. Routine forms remain browseable. | 90 s/CIK, shared limiter below 3 requests/s |

Sources use ETag and Last-Modified when available. Failures are isolated and exponential backoff respects Retry-After. SEC blocks trigger a shared cooldown of at least ten minutes. Initial baselines are silent. After restart the collector reprocesses available feed history and deduplicates. A publisher's finite feed limits recovery after extended outages. Delay flags use the larger of five minutes or two poll periods. Publication, first receipt and dispatch are separate UTC instants. The UI displays Eastern time. A timestamped related headline establishes available coverage, not proof of causation.

On November 23, NVIDIA dated its Jensen Huang announcement at 8:55 a.m. ET. Its GlobeNewswire feed reported a build at 8:55:41 and its newsroom feed at 8:58:09. The scanner then polled only the newsroom for NVIDIA releases, at 120-second intervals. Peteamdpc received the headline at 9:00:52 and recorded successful Discord dispatch at 9:00:53. These build times identify publisher-side work. The exact instant each feed became visible to peteamdpc was not recorded. The scanner now alerts on the first live copy from either source. A matching title in the same publication minute links the other copy as related coverage. [NVIDIA wire feed](https://rss.globenewswire.com/rssfeed/organization/Z9ZspHFg7E0Lf8obA4Udeg==) · [NVIDIA newsroom announcement](https://nvidianews.nvidia.com/news/nvidia-board-reinstates-jensen-huang-as-president-ceo-and-board-director)

Alpaca historical bars are fetched directly with an explicit feed and a bounded regular-session warm-up window. The default `window_minutes=1` compares first open to last close, in either direction. `price_percent=1` and `relative_volume=2` must both be met. Relative volume compares mean per-minute volume over the move to the previous `baseline_minutes=20` consecutive completed minutes for the same ticker, feed and session. The default requires 21 contiguous valid bars. SOXX uses the identical move window when available. `cooldown_minutes=15` suppresses follow-on events/searches per ticker across restarts. Gaps, stale or incomplete bars and delayed SIP cannot create live price alerts. Same-session trailing volume is sensitive to an already elevated tape.

## November 19 AMD replay

Run `backend\.venv\Scripts\python.exe backend\tools\replay_scanner_amd.py --download` from the repo root. The first run downloads public Yahoo Finance 1-minute AMD and SOXX charts. Subsequent runs reuse the preserved JSON under `TOPSIGNAL_DATABENTO_CACHE_DIR/scanner-replay/2026-11-19` (default `backend/storage/databento`). Public intraday chart retention is limited. Preserve the inputs with the cache. Replay uses the live parser, detector, writer, linker, outbox and webhook sender, a disposable SQL database, a controlled clock and a loopback receiver.

Through the close of 10:45 ET, zero completed bars passed both default thresholds. The 10:25 AMD minute was −1.335% on 418,247 shares (0.698× preceding mean), versus SOXX −0.544%. Yahoo gives $530.49 to $523.41, while the supplied replay gives $530.48 to $523.41. Both clear the price threshold. The 10:45 close was $486.60. Data from a live IEX-only feed can differ from this public chart.

The newsletter was published at 10:26:30 ET. Replay simulates receipt at 10:26:32. The article timestamp supplied for the website is 10:27:52, versus 10:27:51 in its RSS. Replay uses 10:27:52 and simulates receipt at 10:27:55. One raw headline was dispatched locally before any summary attempt. The website copy added related coverage. The command prints separately measured wall-clock receipt-to-loopback-dispatch latency and the simulated publisher-to-collector delays (2 and 3 seconds). Focused tests separately exercise a qualifying move, in-flight search, budget contention and restart.

Source references include [Hunterbrook newsletter](https://newsletter.hntrbrk.com/feed), [Hunterbrook site](https://hntrbrk.com/feed), [AMD](https://ir.amd.com/news-events/press-releases/rss), [NVIDIA](https://investor.nvidia.com/investor-resources/rss/default.aspx), [Intel](https://www.intc.com/news-events/press-releases/rss), [Broadcom](https://www.broadcom.com/company/news/releases), [TSMC](https://pr.tsmc.com/english/latest-news), [SEC access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [Alpaca coverage](https://docs.alpaca.markets/us/docs/historical-stock-data-1), [Luna pricing](https://developers.openai.com/api/docs/models/gpt-6-luna) and [tool pricing](https://developers.openai.com/api/docs/pricing).
