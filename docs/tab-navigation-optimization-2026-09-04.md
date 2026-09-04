# Tab navigation optimization — 2026-09-04

Extends the Dashboard navigation optimization to Trades, Expenses, Journal, Bot, and Themes.

## Result by tab

| Tab | Behavior |
| --- | --- |
| Trades | Existing ten-minute account/query/user cache handles return visits. Refresh now bypasses the browser cache and reads stored server data; Sync Latest still explicitly requests provider synchronization. |
| Expenses | Expenses, payouts, and financial totals share a ten-minute, in-memory display cache. Mutations invalidate all financial displays. Refresh clears the cache and reloads the three visible datasets. Explicit combine reconciliation bypasses the expense cache and continues to require fresh provider accounts. |
| Journal | Entries and image metadata are cached by user, account, query, and entry. Editing, conflicts, image changes, and existing journal mutation invalidations clear affected caches. Refresh reloads entries and images; it is disabled while a draft is unsaved, saving, or in error/conflict. Version checks and account request guards remain in place. |
| Bot | Existing candle history cache is preserved. Automatic partial-price REST requests and price streaming pause during scheduled futures closures. Ten-second ticks detect reopening and restart price reads/streaming. Explicit chart Refresh can still request data. Bot settings, runtime, and activity always use current server reads. |
| Themes | Already entirely local; no data-fetching changes needed. |

The new display cache retains at most 128 entries, shares pending requests, isolates consumers' cancellation, aborts an abandoned transport, and discards failures. An invalidated or superseded request cannot repopulate the cache. No new journal or financial data is persisted to browser storage. Changes made on another device become visible on Refresh, browser reload, or a read after cache expiry.

The futures schedule only suppresses chart display requests. It does not authorize trading, establish provider availability, or make old quotes fresh. Cached candles retain stale-data indicators. This change does not alter the prior unattended-live NO-GO decision.

## Verification

Commands run from `C:\Users\drews\Development\TopSignal\frontend`:

- `npm test`: **819 passed, 102 test files**, exit 0; final run at 17:32:57 ET, duration 11.12 seconds.
- `npm run lint`: exit 0.
- `npm run build`: TypeScript and Vite passed, exit 0; 224 modules, Vite build 2.79 seconds.
- Root `git diff --check`: exit 0; only existing Windows line-ending notices.

Thirty added regression cases cover request sharing/expiry, failed reads, cancellation, bounded storage, superseded responses, user/account/filter/demo isolation, financial and journal mutation invalidation, manual refresh, unsaved draft protection, actual Expenses and Journal remounts, and Bot closed/open/closed transitions with mocked transports. Existing account-switch and autosave tests pass.

Running-app check: after warming the tabs, navigated Trades → Expenses → Journal → Themes between **21:31:28.517Z and 21:31:48.087Z**. API performance logs recorded **zero new request starts** in that interval; no loading/error state remained in the inspected pages. Journal had no matching entries on this account; populated journal reuse is covered by the component regression test. The Bot return visit retained current server requests and displayed TopBot as **DISABLED / DRY RUN** with **Market closed**. Its server reads were intentionally not eliminated.

All write-path tests used mocks. Running-app verification only navigated tabs. No order controls, bot activation, or provider order endpoints were invoked. No backend code changed in this follow-up, and backend suites were not rerun.

## Files changed in this follow-up

- `frontend/src/lib/api.ts`
- `frontend/src/lib/navigationReadCache.ts`
- `frontend/src/lib/navigationReadCache.test.ts`
- `frontend/src/lib/api.navigationCache.test.ts`
- `frontend/src/pages/trades/TradesPage.tsx`
- `frontend/src/pages/trades/TradesPage.accountRace.test.tsx`
- `frontend/src/pages/expenses/ExpensesPage.tsx`
- `frontend/src/pages/expenses/ExpensesPage.test.tsx`
- `frontend/src/pages/journal/JournalPage.tsx`
- `frontend/src/pages/journal/JournalPage.accountRace.test.tsx`
- `frontend/src/pages/bot/BotSignalChart.tsx`
- `frontend/src/pages/bot/BotSignalChart.session.test.tsx`
- This verification note.
