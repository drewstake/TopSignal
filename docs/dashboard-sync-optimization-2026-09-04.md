# Dashboard navigation sync optimization — September 4, 2026

Returning to the Dashboard previously invoked a fresh ProjectX trade sync for
the copy-trade leader and every follower. Each successful call invalidated all
cached account reads. Provider account-list updates also rebuilt the roster
array, restarting the dashboard load even when the account IDs were unchanged.
Compact Mode then requested provider refreshes again while reading the synced
results. Calendar refresh attempts were remembered only for a mounted page.

The frontend now shares recent successful trade-refresh observations across
route mounts, scoped by authenticated user, account, and covered history range.
Concurrent covered requests share one promise. A moving end timestamp does not
force a duplicate recent-tail sync. Broader or uncovered historical ranges are
still fetched. Errors expire immediately, and a manual `refreshTrades` call
always reaches the server. Actual refreshes and data mutations invalidate read
caches; reuse of a successful observation does not discard them.

Automatic observations last at most 30 seconds during normal weekly session
hours and 10 minutes during the usual Friday/weekend or 17:00–18:00 New York
maintenance closure. Reopening changes the cache period immediately, including
across daylight-saving offsets. This is a dashboard analytics cadence, not a
provider status or trading authorization check. Holidays and unscheduled
closures retain the shorter cadence. There is no new background polling timer;
the cadence bounds reuse when another automatic load is requested. Late broker
corrections remain discoverable on a subsequent stale load or manual sync.

Visible-month calendar refreshes use the same automatic sync lane, then read
the saved calendar. Compact Mode reads locally after a copied-account sync;
it no longer refreshes the provider again for each result. Roster identity
remains stable when the same account IDs arrive in refreshed account objects.
The copy-trade panel calls ordinary data loading “Loading” instead of “Syncing.”

The cache is memory-only, bounded to 128 user/account groups and 16 ranges per
group, and is cleared by applicable data mutations. A full browser reload may
perform an initial sync. No order routes, bot arming, or execution configuration
were changed.

## Verification

Commands ran in `frontend`:

| Command | Final result |
| --- | --- |
| `npm test -- src/lib/tradeRefreshCache.test.ts src/pages/dashboard/DashboardPage.compactMode.integration.test.tsx` | 30 passed across 2 files, 3.68 seconds |
| `npm test -- src/lib/api.tradeRefresh.test.ts src/lib/tradeRefreshCache.test.ts` | 17 passed across 2 files, 0.552 seconds |
| `npm test` | 789 passed across 99 files, 10.65 seconds |
| `npm run lint` | Exit 0 |
| `npm run build` | TypeScript passed; Vite built 223 modules in 2.65 seconds; exit 0 |

`git diff --check` on the changed frontend files exited 0 (Windows line-ending
notices only). Backend code was not changed for this follow-up.

Nineteen new cases cover route remounts in Standard and Compact modes, moving
end times, pending-request coalescing, covered/uncovered ranges, user/account
isolation, manual refresh, failed refresh retry, mutation invalidation, cache
preservation, calendar coordination, Demo Mode isolation, TTL expiry, reopening,
and summer/winter timezone offsets. An existing calendar assertion now explicitly
requires automatic refresh policy.

The running local app was warmed on account 26507139, navigated to Accounts,
and returned to Dashboard. Between 2026-09-04T21:20:31.856Z and
21:20:49.155Z, the browser's API-start log recorded **zero new API requests**.
The Dashboard displayed Copy Adjusted with the existing totals. No manual
trade sync or order operation was invoked during that UI verification.

## Files

- `frontend/src/lib/tradeRefreshCache.ts`
- `frontend/src/lib/tradeRefreshCache.test.ts`
- `frontend/src/lib/api.tradeRefresh.test.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/dashboard/DashboardPage.tsx`
- `frontend/src/pages/dashboard/DashboardPage.compactMode.integration.test.tsx`
- `frontend/src/pages/dashboard/components/CopyTradePanel.tsx`
- This verification note.
