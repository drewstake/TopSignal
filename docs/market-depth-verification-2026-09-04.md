# Topstep market-depth verification — September 4, 2026

## Provider support and observed data

The official [ProjectX realtime API documentation](https://gateway.docs.projectx.com/docs/realtime/)
documents `SubscribeContractMarketDepth` on the market hub and `GatewayDepth`
updates. Its payload represents volume at a price level.

[Topstep's market-data guide](https://help.topstep.com/en/articles/8284120-level-1-and-level-2-market-data)
distinguishes included Level 1 best bid/ask data from the Level 2 depth upgrade,
listed at $38/month when checked. This documentation does not establish the
operator's current billing entitlement.

A bounded, read-only probe used the operator's stored ProjectX credential,
kept the credential/token in memory, and connected to the documented market
hub for `CON.F.US.MNQ.U26`. The database transaction was read-only. A restricted
client permitted only `/api/Auth/loginKey`; the only hub invocations were
`SubscribeContractMarketDepth` and `SubscribeContractQuotes`. Closing the socket
ended the subscriptions. No order endpoints, worker startup, bot arming, or
configuration writes were involved.

During the 12-second observation:

- Authentication and the SignalR handshake succeeded.
- Both subscriptions were accepted.
- 42 `GatewayDepth` events and 41 `GatewayQuote` events arrived.
- Depth payloads included Reset, Trade, BestAsk, BestBid, High, and Low types.
- No explicit Ask/Bid depth-level messages (types 1/2) were observed.

This confirms working realtime top-of-book access. It does **not** establish
Level 2 entitlement or the availability/completeness of a full depth ladder.
Check the Depth of Market Bundle under Topstep Dashboard → Accounts → Add Ons
to establish subscription status. No purchase or subscription change was made.

## Local fixes

1. The shared SignalR handshake helper already consumed the acknowledgement.
   The order-book session waited for a second acknowledgement before subscribing,
   causing a timeout/reconnect loop when the server waited for subscriptions.
   The session now consumes one acknowledgement and preserves other frames
   received in that same WebSocket message.
2. Best-price changes previously accumulated as historical price levels, creating
   apparent depth from a Level 1 feed. Each side now retains only its current
   quoted best price unless an explicit depth update established another level.
   Best-price changes remove incompatible levels, preserve supported deeper
   levels, and publish replacement snapshots so clients remove obsolete rows.
   Stale updates cannot resurrect discarded quotes. Quote-only price history
   does not accumulate in the timestamp map. Reset/reconnect clears provenance.
3. The panel explains the difference between Level 1 and Level 2 availability.

The original probe's reconstructed 8 bid/7 ask counts exposed the old
accumulation bug; they were not evidence of Level 2 depth. After the fix, the
actual local UI was observed **Connected**, displaying one current best bid and
one current best ask while TopBot remained disabled and in dry-run mode.

## Verification

Seven initial regression cases failed against the old implementation, then
passed after the fixes. Additional tests cover zero-size best quotes, mixed
depth/quote updates, stale best updates, and provider reset/reconnect behavior.
The fake WebSocket now emits exactly one acknowledgement instead of inventing
an acknowledgement for every receive call.

Commands used from the repository root unless stated otherwise:

```text
backend/.venv/Scripts/python.exe tmp/audit-market-depth-20260904/probe.py
backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py tests -q -ra
```

Frontend directory:

```text
npm test
npm run lint
npm run build
```

Final results: **1,409 backend tests passed, 8 PostgreSQL acceptance tests
skipped** (24.44 seconds); the offline guard recorded no external connection
attempts. **765 frontend tests passed across 97 files** (11.60 seconds).
Frontend ESLint and the TypeScript/production build both exited 0. A second
read of the actual UI showed updated bid/ask prices and sizes while remaining
connected after the final changes.

Local probe output and backend test logs are in
`tmp/audit-market-depth-20260904/`. No secrets are included in the probe output.
The Python Ruff module was unavailable in the current virtual environment;
this turn does not establish a clean broad backend lint baseline. The broader
unattended-trading NO-GO and PostgreSQL acceptance blockers remain unchanged.

## Follow-up: Friday market closure and connection stability

The reported Friday 17:00 New York closure was treated as a transport failure.
The depth session now reuses `futures_session_is_open`, with its timezone,
weekend, maintenance, equity halt, and known holiday rules. A closed contract
receives `market_closed` immediately, with an empty book. Every five seconds
the backend checks the calendar locally, without requesting a ProjectX token
or opening a provider socket while all subscribed contracts are closed. SSE
remains open with its existing keepalive. At reopening, subscriptions resume
and the frontend receives a fresh snapshot before incremental updates.

An already-connected session also closes at the calendar boundary. Market
state is evaluated per contract, so a closed equity contract does not acquire
depth from another subscribed product. The UI clears old prices and ignores
late snapshots/deltas while closed. A late subscription acknowledgement cannot
replace the closed state with Connected. Genuine network failures during open
hours continue to report reconnecting/unavailable.

The scheduled Friday close matches the hours in the
[CME Micro E-mini FAQ](https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html).
The local calendar is not an authoritative feed for unexpected exchange halts
or future schedule changes. This display change does not expand or bypass the
bot's execution permissions or safety checks.

The depth client also sends SignalR protocol keepalives every 15 seconds, with
a bounded send timeout, in addition to WebSocket control pings. The protocol
defines a separate application-level ping; see the
[SignalR protocol specification](https://github.com/dotnet/aspnetcore/blob/main/src/SignalR/docs/specs/HubProtocol.md)
and [Microsoft client configuration](https://learn.microsoft.com/en-us/aspnet/core/signalr/configuration).
Reader, calendar watcher, and keepalive tasks are cancelled and collected when
the connection closes or a viewer leaves.

Added 16 backend regression cases and two frontend cases covering closed startup,
Friday/Sunday boundaries in summer and winter, daily maintenance, equity halt,
holiday closure, automatic reopening, late data, a viewer arriving at the close,
mixed product sessions, closure during authentication, SignalR keepalives,
keepalive timeouts, task cleanup, and the rendered closed/reopened UI.

The first full backend run after 17:00 produced **23 failures, 1,402 passes, and
8 skips** because existing mocked bot execution tests assumed an open exchange
based on the wall clock. Those tests now explicitly mock an open exchange;
the dedicated closure test still overrides it to closed, and real calendar
tests continue to run. No production trading gate was changed for these tests.

Follow-up verification commands and exact results:

| Command | Result |
| --- | --- |
| `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py tests/test_projectx_order_book.py tests/test_trading_day.py -q` | 63 passed in 1.41s; external connections blocked=0 |
| `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py tests/test_bot_execution_safety.py tests/test_bot_service.py -q --tb=short` | 264 passed in 4.76s; external connections blocked=0 |
| `backend/.venv/Scripts/python.exe backend/tools/run_offline_tests.py --tb=short -q -ra` | 1,425 passed, 8 PostgreSQL skips in 24.58s; external connections blocked=0 |
| `npm test -- src/lib/marketDepthStream.test.ts src/pages/bot/OrderBookPanel.test.tsx src/pages/bot/orderBook.test.ts` (frontend) | 28 passed across 3 files in 2.18s |
| `npm test` (frontend) | 770 passed across 97 files in 10.59s |
| `npm run lint` (frontend) | Exit 0 |
| `npm run build` (frontend) | TypeScript exit 0; Vite built 222 modules in 2.82s, exit 0 |
| `tmp/audit-20260904/static-tools/bin/ruff.exe check backend/app/services/projectx_order_book.py backend/tests/test_projectx_order_book.py --select E9,F63,F7,F82 --output-format concise` | All checks passed, exit 0 |
| Same Ruff command without `--select E9,F63,F7,F82` | Exit 1: 14 broader style/modernization/broad-exception findings; not a clean broad backend lint result |
| `git diff --check` | Exit 0; Windows line-ending notices only |

The existing temporary audit installation supplied Ruff; the virtual
environment itself still has no Ruff module. Broader lint debt and skipped
PostgreSQL acceptance remain recorded in the unattended-readiness audit.

The actual local bot page was checked after reloading following development
server restarts, then checked again after verification commands. Both reads
showed **Market closed**, an empty ladder, and automatic-resume text. TopBot
showed **DISABLED / DRY RUN** and the continuous worker was disabled. `/health`
returned `{"status":"ok"}`. Real reopening and sustained live keepalive behavior
remain unobserved after this change because the exchange was closed; their
transition and failure behavior were tested with mocks. No orders were placed,
modified, cancelled, or flattened; no bot was armed.

Files changed for this follow-up: `backend/app/services/projectx_order_book.py`,
`backend/tests/test_projectx_order_book.py`, `backend/tests/test_bot_execution_safety.py`,
`backend/tests/test_bot_service.py`, `frontend/src/lib/types.ts`,
`frontend/src/lib/api.ts`, `frontend/src/lib/marketDepthStream.test.ts`,
`frontend/src/pages/bot/orderBook.ts`, `frontend/src/pages/bot/OrderBookPanel.tsx`,
`frontend/src/pages/bot/OrderBookPanel.test.tsx`, and this verification note.
