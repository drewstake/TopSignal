# Session order-flow local validation

No deployment, production config change, service restart, order action, strategy
change or cloud storage write was performed. The read-only remote audit streamed
Python through SSH stdin without installing files. Eight sealed raw segments
(4,559,027 compressed bytes) were copied into the development filesystem cache
for local verification; their hashes matched the retained host originals.

## Evidence

- `python -m pytest tests/test_session_order_flow.py tests/test_depth_history.py tests/test_bot_strategy_registry.py tests/test_topbot_mathematical.py tests/test_topbot_profit_guard.py tests/test_worker_profit_guard.py -q`: **121 passed**.
- `npm test -- src/pages/bot`: **463 passed**, 48 files.
- `npm run build`: TypeScript and Vite production build passed.
- ESLint on the changed frontend modules/tests passed.
- `git diff --check`: passed (repository CRLF normalization notices only).
- Headless Chromium desktop (1440 px) and mobile (390 px) preview of the actual
  presentation component, using the frozen real response: inspected visually,
  recent-window control worked, document scroll width matched the viewport.
  Browser and temporary Vite preview server were closed; temporary harness files
  were removed.

Backend tests cover market trades versus DOM/quote/account activity, owner,
delivery and subscription separation, unknown sides, booleans/strings masquerading
as enums, invalid quantities, stale/future/missing provider timestamps, incomplete
segments, per-window totals, session resets, warm-cache reuse, source tampering,
overlapping captures, authenticated API access before reads, and remote host scope
validation. Existing session tests cover Eastern DST and weekend boundaries.

Frontend tests cover reported-volume labeling, disagreement between session and
recent flow, unknown quantities, partial coverage, unavailable aggressor bias,
price observations, window selection, stale/error suppression of current
conclusions, scope changes with late responses, demo/no-account behavior, and
updated timeframe wording. The surrounding bot suite and existing strategy/risk
tests passed with trading implementations unchanged.

## Frozen real-data smoke result

For the sampled November 3 evening session, totals through 7:41 p.m. Eastern:
1,165 provider Buy-labeled contracts, 1,041 Sell-labeled contracts, reported delta
+124, and 157 unclassified contracts due to unreliable provider timing. Classified
volume was 93.36% of valid observed volume; Buy share of classified volume was
52.81%. The available fragment began at 7:40:17 p.m.; the reader correctly showed
**partial session coverage**, not a full-session conclusion. Its observed trade
price change was +2.25 points. The first cold read of this selected session fragment
took approximately 0.27 seconds on the development PC; this is not a benchmark
of a full-day archive.

Local generated evidence is retained under
`backend/storage/databento/session-flow-verification/` (`response.json`,
`desktop-panel.png`, `mobile.png`, and immutable copied raw segments).

## Outstanding verification boundary

The implementation is reviewable locally, but **verified aggressor semantics are
not established**. The primary API docs define the enum but not the aggressor
contract. A separate TopstepX indicator help page describes its CVD as market-buy
minus market-sell volume, but does not connect that calculation to
`GatewayTrade.type`; it therefore does not resolve this API verification gap:
[TopstepX indicators](https://help.topstep.com/en/articles/8524249-topstepx-indicators).

The panel deliberately leaves a verified bullish/bearish verdict and absorption
unavailable. Provider confirmation or other authoritative, field-specific evidence
is still needed for that part of the requested outcome. The host reader is not
installed, in accordance with the instruction not to deploy; its future setup is
documented in [session-order-flow.md](../../session-order-flow.md).

## Level 2 follow-up

The user confirmed purchasing DOM Level 2. The frozen files already contain
53,170 Ask and 52,643 Bid explicit depth events, plus 6,045 NewBestBid and
5,992 NewBestAsk events. The independent DOM cross-check uses only preceding
best-price events, clears ambiguous deleted/improved best prices, rejects stale
or later provider timestamps, and never sorts on the undocumented `seq` field.
Its 85.1% outside-spread agreement is corroboration, not verified aggressor
semantics. The official public Swagger specification also has no `TradeLogType`
or aggressor definition: <https://api.topstepx.com/swagger/v1/swagger.json>.

Follow-up inspection fixed a delivery-isolation defect: a concurrently recorded
different delivery could previously cause a requested delivery's segment to be
excluded as overlapping. Overlap checks now apply after contract filtering.
The regression fixture proves a concurrent H27 recording cannot suppress Z26.
No existing strategy or recording implementation was modified.

## Quantower cross-platform follow-up

A live MNQZ6 sample from Quantower's **Data provider** aggressor mode matches
the recorded ProjectX feed over 60 seconds: 1,907 prints, 961 buy contracts,
996 sell contracts, delta -35. All 714 unique timestamp/price/size candidate
pairs agree on direction; repeated matching keys are kept separate from that
claim. See [the comparison evidence](quantower-comparison.md). This strengthens
cross-platform consistency evidence but does not establish independent exchange
provenance, full-session completeness, or change the verified-bias gate.

## Final local implementation audit

The UI now interprets the recorded Buy/Sell balance as **bullish/bearish reported
pressure**, with a visible qualification that this is neither a price forecast nor
institutional-activity detection. Its method disclosure identifies the exact
Quantower sample and common ProjectX source. The unavailable verified-aggressor
and absorption fields were not promoted to verified. Unknown volume that could
erase/reverse delta, or invalid quantities, makes reported pressure uncertain.

The reader was independently checked against all trade events in six frozen
segments. Its available-session total matched 12,018 prints, 6,225 buy contracts,
6,337 sell contracts and delta -112. The complete five-minute interval ending
9:02 p.m. EST matched 11,620 prints, 6,050 buy contracts, 6,105 sell contracts and
delta -55. The 15/60-minute selections also matched their available records;
their missing earlier history remains explicitly partial. A repeated cached
read returned identical session/window totals. Evidence is retained in
`backend/storage/databento/quantower-comparison/2026-11-03/session-reader-validation.json`.
This reader check uses ProjectX records; it is not a second Quantower capture.

| Requirement | Current evidence |
| --- | --- |
| Verify Buy/Sell mapping before presenting pressure | Quantower comparison, official enum, checksummed raw samples; scope/limitations disclosed |
| Session grouping from 6 p.m. New York | Existing DST/weekend backend helper; reset tests; new frontend rollover tests |
| Session volume and recent pressure | Independent real-record reader check; 5/15/60-minute UI selector |
| Explain price response without a false absorption claim | Same-window first/last prices; tested agreement/divergence/equal endpoints |
| Separate EMA context from order flow | Price-trend strip; existing calculation preserved; corrected spread/slope wording |
| Handle missing, unknown and stale evidence | Backend coverage/isolation tests; frontend unknown-volume and rollover tests |
| Keep bot strategy unchanged | No diff in strategy, profit guard, bot service or worker; strategy regression tests pass |
| No deployment | Only local source, local evidence and temporary local preview changed; remote reader remains uninstalled |

Final validation: **466 frontend tests across 48 files**, **126 backend tests**,
TypeScript/Vite build, targeted ESLint, and diff whitespace checks passed. Desktop
and 390-pixel mobile previews were inspected using the real frozen reader result;
there were no browser errors or mobile horizontal overflow. Changing the recent
window worked. Screenshots are `panel-desktop.png` and `panel-mobile.png` in the
same local evidence directory. The frontend immediately withdraws a previous
session's summary at 6 p.m., even if its response was generated only seconds ago.

The feature is ready for local review. Production installation/configuration and
deployment are deliberately outstanding under the user's **do not deploy**
instruction. Exchange-native classification and full exchange completeness remain
unknown rather than being represented as verified prerequisites already satisfied.
