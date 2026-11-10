# TopBot recovery from temporary instrument rejections

A confirmed temporary ProjectX entry rejection leaves an authorized continuous
TopBot Practice run enabled. The failed attempt remains an error in the audit.
The run stores an entry pause, starting at 60 seconds and backing off through
120, 240 and at most 300 seconds after repeated rejections.

Only a decoded `success: false` response from `/api/Order/place` qualifies:
`errorCode: 5` (OutsideTradingHours), or `errorCode: 2` with the exact provider
message “Trading is currently unavailable. The instrument is not in an active
trading status.” Other errors keep the existing disable/reconciliation behavior.
In particular, ContractNotActive (9) requires delivery review; it does not mean
a temporary halt. HTTP failures, interrupted responses, timeouts and unknown
submission outcomes never authorize this recovery.

After the delay, TopBot requires a fresh qualifying depth episode that began
after the rejection. It then runs the ordinary account classification, session,
position/open-order, daily risk, signal, idempotency and worker-lease checks.
It creates a new attempt; it never resends the rejected attempt or reuses its
signal episode. The existing exit-plan reconciler settles the rejected entry as
not submitted before another entry. A successful submission clears the pause.

ProjectX's documented contract search provides active-delivery metadata, not a
real-time instrument tradability probe. A timer or fresh book does not establish
tradability. Recovery allows a newly qualified entry to be attempted after the
delay; another temporary rejection extends the pause. No diagnostic orders are
sent solely to test tradability.

The pause is stored in the existing run JSON and survives an authorized Practice
worker restart. Read-only provider checks, position protection and session/daily
exit management continue during the pause. Manual Stop, disabled configurations,
unrelated errors and restart opt-out remain effective; old failed runs are not
automatically rearmed. The dashboard's entry checks show the recovery state and
next eligible recheck, without claiming the instrument is tradable.

No schema or retention change is needed. This adds only compact run state and an
error reason code; market history remains on the filesystem. The recovery source
is included in the worker evidence fingerprint, so previous evidence retains its
original source identity.

References: [ProjectX order response codes](https://gateway.docs.projectx.com/docs/api-reference/order/order-place/)
and [contract search fields](https://gateway.docs.projectx.com/docs/api-reference/market-data/search-contracts/).
