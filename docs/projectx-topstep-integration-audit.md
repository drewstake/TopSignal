# ProjectX / Topstep integration audit

Audit date: 2026-07-29

## Outcome

The audited account-discovery and combine-expense paths now distinguish a
stored credential from a usable credential, a usable credential from a
provider-authenticated request, and a successful provider refresh from a
cached fallback.

A successful direct login with server-wide environment credentials is **not**
evidence that the signed-in application user is connected. The authoritative
application check is a forced `/api/accounts` refresh through the signed-in
user's stored credential. It either returns rows marked `provider_fresh`,
returns an explicitly identified `cached_fallback`, or fails with a structured
ProjectX error when no usable cache can be returned.

The combine reconciler now requires that forced provider-fresh result before it
reads expenses or persisted suppression state, changes the browser-local
combine ledger, creates an expense, or deletes an auto-generated duplicate.
Deleted auto-generated rows also leave a user-scoped server tombstone, so a
cleared browser or another device cannot recreate them. Re-running a successful
reconciliation is idempotent in the regression suite.

## Scope and safety

This audit covered:

- per-user ProjectX credential lookup, encryption-key handling, and client
  construction;
- ProjectX authentication/error classification and account discovery;
- account database synchronization, serialization, caching, and UI rendering;
- provider-data freshness and cached-fallback observability;
- the Topstep combine recognition and expense-reconciliation workflow; and
- Vite, Uvicorn, and the Node development supervisor's environment reload
  boundaries.

Safety constraints observed:

- No live ProjectX login or account request was made.
- No real expense record was created, updated, or deleted.
- Provider behavior, database rows, browser storage, and expense mutations were
  exercised with stubs, fixtures, and in-memory test databases.
- No stored credential was overwritten and no encryption key was rotated.
- Credential values, usernames, API keys, bearer tokens, encrypted blobs, and
  provider payloads were not printed or added to this report.
- `.env`, `backend/.env`, and `frontend/.env` are ignored by `.gitignore`; only
  `.env.example` is tracked.
- A comparison-only secret scan checked five sensitive ignored-environment
  fields against the Git diff and untracked files and found zero matches.

Because the audit intentionally did not use a real provider identity or real
financial records, the final section includes a non-production UI smoke-test
checklist.

## The authenticated application path

The real signed-in account path is:

1. `frontend/src/app/AppShell.tsx` first requests a local account snapshot
   through `getSelectableAccountsLocalFirst()`. When a ProjectX account is the
   active source, it follows with `refreshProvider: true`. The Accounts page
   uses the same client for an explicit refresh. Expense reconciliation uses a
   stricter request with `bypassCache: true` and `refreshProvider: true`.
2. `frontend/src/lib/api.ts` obtains the current authentication context,
   includes that access token in the request, and isolates account-list cache
   and in-flight entries by authenticated user scope.
3. `GET /api/accounts` in `backend/app/main.py` resolves the authenticated user
   with `get_authenticated_user_id()`. All account and credential queries use
   that user ID.
4. `_projectx_client_for_user()` calls
   `backend/app/services/projectx_credentials.py`, which selects exactly the
   `provider_credentials` row for `(signed-in user, "projectx")` and decrypts
   both fields with the current runtime's `CREDENTIALS_ENCRYPTION_KEY`.
5. If that row exists but cannot be decrypted, the request fails as
   `projectx_credentials_unavailable`. It does not silently test the unrelated
   server-wide environment credential. Legacy environment credentials are
   available only under the explicit local-only compatibility flag and only
   when no per-user row exists (or the credential table itself is unavailable).
6. `backend/app/services/projectx_client.py` authenticates at
   `/api/Auth/loginKey`, keeps the bearer token behind a one-way credential
   fingerprint, and fetches accounts from `/api/Account/search`.
7. `backend/app/services/projectx_accounts.py` upserts only the signed-in
   user's ProjectX rows. A successful refresh updates provider name, balance
   when supplied, tradability, visibility, account state, and `last_seen_at`.
   CSV-import rows are not overwritten. Provider rows omitted beyond the
   configured missing buffer transition to `MISSING`.
8. `backend/app/main.py` serializes every account with its source and provider
   health metadata. A row returned by the current provider response is
   `provider_fresh`; a saved row not returned in that response remains
   cache-backed.
9. `frontend/src/lib/accountProviderState.ts`, `AppShell.tsx`,
   `AccountsPage.tsx`, and the standalone dashboard render refresh progress,
   successful live refresh, recent saved data, aged saved data, and
   authentication/configuration/network failures without exposing upstream
   details.
10. For combine reconciliation,
    `frontend/src/pages/expenses/expenseReconciliation.ts` validates this
    provider result before loading expenses and user-scoped suppression state
    or applying either browser-ledger or expense API mutations. The backend
    stores deleted auto-expense tombstones in `expense_suppressions` and also
    rejects a later suppressed auto-create at the API boundary.

The separate credential-status route deliberately stops before provider
authentication:

| State | Meaning |
| --- | --- |
| `configured: false`, `status: not_configured` | No ProjectX credential row exists for the signed-in user. |
| `configured: true`, `decryptable: false`, `status: unavailable` | A row exists, but this runtime cannot decrypt it with the current key. |
| `configured: true`, `decryptable: true`, `status: ready` | The row can be decrypted. This does **not** prove that ProjectX accepts it. |
| `provider_fresh` from a forced account refresh | The normal per-user client authenticated, account search completed, and the returned row was synchronized. |
| `cached_fallback` | The attempted refresh failed, but saved ProjectX rows were returned with an actionable error code and last-successful-refresh time. |

## Confirmed root causes and implemented fixes

| Confirmed defect | Evidence and affected files | Implemented fix |
| --- | --- | --- |
| A direct environment-key test could exercise a different identity from the signed-in app. An undecryptable stored row could also fall back to that environment identity in local compatibility mode. | `backend/app/main.py` (`_projectx_client_for_user`); `backend/app/services/projectx_credentials.py`; regression coverage in `backend/tests/test_projectx_accounts_route.py` and `backend/tests/test_projectx_connection_observability.py`. | The resolver is user-scoped. An existing but unreadable row now fails closed and never falls back to the environment credential. Missing, changed, and malformed Fernet keys normalize to a safe unavailable state without overwriting the row. |
| Credential status reported only row existence, so “configured” could be mistaken for decrypted or authenticated. | Credential status schema and route in `backend/app/projectx_schemas.py` and `backend/app/main.py`. | The response now reports `configured`, `decryptable`, `status`, and `error_code`. `ready` is explicitly documented as decryptable, not authenticated. Authentication is verified by the normal account-refresh path. |
| `/api/accounts` swallowed refresh failures whenever any local row existed and returned an ordinary HTTP 200 list. A filtered-out cache could even result in a silent empty list. | Previous fallback branch in `backend/app/main.py`; outage, filtered-cache, and no-cache tests in `backend/tests/test_projectx_connection_observability.py`. | A returnable ProjectX cache is now required for a 200 fallback. Every returned ProjectX row is marked `cached_fallback` with a safe code/message and the last successful refresh. Without a returnable cache, the endpoint raises a structured non-200 error. |
| `refresh_provider=false` was treated as stale regardless of cache age. ProjectX failure state could also leak onto Live CSV rows. | Account serialization in `backend/app/main.py`; schema in `backend/app/projectx_schemas.py`; `PROJECTX_ACCOUNT_STALE_AFTER_SECONDS` in `.env.example` and `README.md`. | Staleness is computed from `last_seen_at`, independently of whether a refresh was requested or failed. The default threshold is 900 seconds. CSV-import rows are always `not_applicable`, non-stale, and carry no ProjectX error. |
| An explicit provider refresh could be skipped when the local database contained only CSV rows. | Provider-sync condition in `backend/app/main.py`; route coverage in `backend/tests/test_projectx_accounts_route.py`. | `refresh_provider=true` now always attempts ProjectX discovery. Cache-only reads remain provider-free. |
| A successful provider response did not reliably replace the local-first account cache, while a failed HTTP-200 fallback could be cached as if it were a success. | `frontend/src/lib/api.ts`; coverage in `frontend/src/lib/api.test.ts`. | Account cache lanes are user-scoped. Successful provider responses replace the corresponding local-first lane and invalidate obsolete in-flight/cache lanes. Cached fallbacks are not retained for the normal ten-minute list TTL, so the next refresh can really retry. |
| Two bypassed provider refreshes could resolve newest-first and oldest-last, allowing the older result to overwrite the local-first lane. | Current-request handling in `frontend/src/lib/api.ts`; deferred-response race coverage in `frontend/src/lib/api.test.ts`. | Cache replacement and fallback invalidation now run only for the request that is still current for that cache key. |
| An explicit Accounts-page refresh while Live CSV was selected updated only page-local rows, so the AppShell dropdown could omit newly discovered ProjectX accounts indefinitely. | `frontend/src/pages/accounts/AccountsPage.tsx`, `frontend/src/app/AppShell.tsx`, and their integrated lifecycle test. | A successful explicit provider refresh publishes an account-list update for a Live selection. The shell reloads the local-first lane, replaces names/rows, and preserves the Live selection without triggering ProjectX merely because Live was selected. |
| A mixed fresh/stale list used the stale row's warning but the newest row's refresh timestamp. | `frontend/src/lib/accountProviderState.ts` and mixed-row regression coverage. | Status, error, and displayed timestamp now all come from the same severity-representative row. |
| The frontend's ten-minute list cache could outlive a shorter configured backend staleness threshold and retain a fresh label. An already-open Live-selected page could also cross the threshold without another request. | Per-row deadline serialization in `backend/app/main.py` and `backend/app/projectx_schemas.py`; cache and timer handling in `frontend/src/lib/api.ts` and `accountProviderState.ts`; component integration in AppShell, Accounts, and Dashboard. | Each ProjectX row carries the exact server-computed `provider_data_stale_at`. Cached results are reclassified against that deadline on every read, and a local-only nearest-deadline timer updates already-rendered rows without contacting ProjectX. CSV stays not applicable; cached fallback keeps its failure status while its age flag changes. |
| AppShell and standalone dashboard refresh failures were silently caught while saved rows remained on screen. | `frontend/src/app/AppShell.tsx`, `frontend/src/pages/dashboard/DashboardPage.tsx`, `frontend/src/pages/accounts/AccountsPage.tsx`. | The UI now shows refresh-in-progress, provider-fresh, recent cache, aged cache, and safe auth/config/network/fallback messages. Saved rows remain usable, but their provenance is visible. |
| The development wrapper reloaded `.env` changes with the environment snapshot captured at supervisor startup, so the child restarted with the old values. | `scripts/dev-backend.cjs`, `scripts/dev-utils.cjs`, `scripts/dev-utils.test.cjs`, and the reload-boundary documentation in `README.md`. | Python changes still trigger code reload. `.env` changes now emit an explicit instruction to stop and restart `npm run dev` or `npm run dev:backend`; the wrapper does not pretend that a hot reload applied the new environment. |
| Combine reconciliation used a cache-only account read, could change tracker/expense state before learning the provider was unavailable, and could report a zero-row outcome without explaining why. | Previous flow in `frontend/src/pages/expenses/ExpensesPage.tsx` and `expenseAccountLoading.ts`; new safety gate in `expenseReconciliation.ts`. | Reconciliation forces an uncached provider request, validates `provider_fresh` and non-stale data first, and only then reads expenses or changes state. Failure and authoritative zero-combine outcomes are both explicit. |
| Combine matching and de-duplication had edge cases: punctuation around `DLL`, blank provider names, arbitrary manual paid prices, unrelated spreadsheet dates, suppressed purchases, and duplicate generated rows. | `frontend/src/lib/combineTracker.ts` and `frontend/src/pages/expenses/expenseReconciliation.ts`; coverage in their test files. | Recognition uses a trimmed provider name with display-name fallback, accepts punctuation-delimited `DLL` and “DAILY LOSS LIMIT,” and intentionally includes `ACTIVE` and `LOCKED_OUT`. A manual non-practice evaluation expense for the same account wins regardless of discount amount. The global spreadsheet-date cutoff was removed, suppression is honored, and only deterministic older auto duplicates are deleted. |
| Deleted auto-expense suppression existed only in browser storage, so another browser, cleared site data, or a concurrent/older client could recreate the row. | `backend/app/models.py`, expense routes in `backend/app/main.py`, `db/migrations/20260729_add_expense_suppressions.sql`, frontend reconciliation/API code, and tenant/idempotency/readiness tests. | User deletion explicitly writes a user/source/account tombstone in the same transaction. Reconciliation reads tombstones before local mutation; duplicate-cleanup deletes explicitly do not suppress; the create route rejects a suppressed auto row; and readiness requires the new migration/table. |
| ProjectX exception text could reach logs or HTTP responses outside account refresh through trade sync, hub dispatch/health, streaming persistence/runtime, backtests, and the shared route converter. | `backend/app/services/projectx_trades.py`, `projectx_hubs.py`, `projectx_streaming_runtime.py`, `backend/app/main.py`, and sentinel coverage in `backend/tests/test_projectx_safe_logging.py`. | These paths now retain only stable reason code, status, exception type, phase/state, and submission-outcome uncertainty. The shared HTTP converter returns mapped safe messages; raw upstream text, tracebacks, payloads, users, accounts, and token-bearing URLs are excluded. |

## Provider status contract

The account response remains a list for backward compatibility, with these
per-row fields:

- `provider_sync_status`
- `provider_sync_error_code`
- `provider_sync_error_message`
- `provider_last_successful_refresh_at`
- `provider_data_stale`
- `provider_data_stale_at`

The status meanings are:

| Status | Meaning |
| --- | --- |
| `provider_fresh` | This account was present in the latest successful ProjectX response. |
| `cache_fresh` | No provider call established freshness for this response, but `last_seen_at` is within the configured age threshold. |
| `cache_stale` | The saved ProjectX row is older than that threshold. |
| `cached_fallback` | A provider refresh was attempted and failed; saved data is being returned. `provider_data_stale` still reflects age, so a recent fallback can be non-stale without being misrepresented as a successful refresh. |
| `not_applicable` | The row uses Live CSV data and is not subject to ProjectX freshness. |

Structured failure reasons include:

- `projectx_credentials_not_configured`
- `projectx_credentials_unavailable`
- `projectx_auth_failed`
- `projectx_network_error`
- `projectx_configuration_error`
- `projectx_provider_error`
- `projectx_cached_fallback_used` in structured fallback logging

ProjectX-facing routes and UI expose mapped, actionable messages. Raw provider
messages are not returned or logged by the audited ProjectX paths.

## Silent-failure paths found

The following previously ambiguous or silent paths were found and addressed:

1. Testing `PROJECTX_API_KEY` directly while the application used a separate
   per-user encrypted credential.
2. Reporting a credential row as “configured” when the runtime key was missing,
   changed, or malformed.
3. Falling back from an unreadable user credential to a server-wide environment
   credential.
4. Returning cached account rows after a provider failure without identifying
   the failure.
5. Returning an empty HTTP-200 list when provider refresh failed and existing
   cache rows were excluded by the current filters.
6. Labeling every cache-only read stale, including a recently refreshed row.
7. Applying ProjectX stale/error meaning to Live CSV rows.
8. Skipping explicit discovery when only Live CSV rows existed locally.
9. Keeping an obsolete local-first response after a successful provider
   refresh, or replaying a cached failed refresh.
10. Swallowing frontend refresh rejections while displaying cached balances and
    statuses with no warning.
11. Restarting Uvicorn after an `.env` edit while retaining the Node
    supervisor's old environment snapshot.
12. Reconciling from cache-only data, changing local tracker state before
    provider validation, or treating zero recognized combines as a silent
    success.
13. Letting an unrelated spreadsheet import date suppress a newly discovered
    combine.
14. Recreating a deliberately suppressed auto expense after browser storage
    was cleared, failing to recognize a manually discounted expense, or
    retaining multiple generated rows.
15. Leaving the shell dropdown stale after an Accounts-page refresh initiated
    while Live CSV remained selected.
16. Letting an older concurrent provider refresh overwrite the newer
    local-first snapshot, or showing a fresh row's timestamp beside a stale
    row's warning.
17. Logging or returning raw ProjectX exception text through trade, hub,
    streaming, backtest, bot, or shared HTTP-conversion paths.
18. Letting the frontend cache or an already-open page keep a fresh label after
    the exact server-configured staleness deadline passed.

## Combine reconciliation contract

The mutation order is now:

1. force the authenticated, uncached ProjectX account refresh;
2. reject a provider error, cached fallback, unconfirmed refresh, or stale
   eligible combine;
3. read relevant expenses and persisted user-scoped suppression tombstones;
4. synchronize the browser ledger from existing expenses, reapply server
   suppression, and then incorporate fresh ProjectX accounts;
5. build a deterministic delete/create plan;
6. remove only superseded auto-generated rows without creating tombstones and
   create only unsynced, unsuppressed purchases; and
7. rebuild the snapshot after successful mutations.

If provider validation fails, the test suite confirms that expenses are not
even read and neither the browser ledger nor expense API is mutated.

Recognized accounts use the provider-backed name, must begin with `50KTC`,
`100KTC`, or `150KTC`, and must be `ACTIVE` or `LOCKED_OUT`. CSV-import accounts
are excluded.

| Plan | Standard | DLL / no-activation |
| --- | ---: | ---: |
| 50K | 4,900 cents | 8,500 cents |
| 100K | 9,900 cents | 12,900 cents |
| 150K | 14,900 cents | 19,900 cents |

Generated rows use:

- `provider: "topstep"`
- `category: "evaluation_fee"`
- `currency: "USD"`
- plan sizes `50k`, `100k`, or `150k`
- account type `standard` or `no_activation`
- tags `combine_tracker`, `auto`, and additionally `dll` when applicable
- description `Auto tracked combine purchase (<PLAN>[ DLL])`
- the inferred browser-ledger purchase date

A manual, non-practice evaluation expense attached to the same account is
authoritative and suppresses generated rows even when its paid amount differs
from today's preset. When only generated duplicates exist, the newest row
(with ID as deterministic tie-breaker) is retained. An absent unsynced ledger
purchase is treated as intentional suppression; no fallback purchase is
manufactured. Deleting an auto-generated row writes a persisted tombstone; a
later auto-create is rejected server-side even if a stale or concurrent client
misses the tombstone read. A second reconciliation after the first successful
creation makes no total-changing mutation.

## Regression coverage and exact results

Final full-suite verification:

| Command | Result |
| --- | --- |
| `cd backend && .venv/bin/python -m pytest -q` | **866 passed, 4 warnings in 19.77s** |
| `NODE_OPTIONS=--no-experimental-webstorage npm --prefix frontend test` | **92 files passed, 703/703 tests passed** |
| `npm --prefix frontend run build` | **Succeeded; 219 modules transformed** |
| `npm --prefix frontend run lint` | **Succeeded** |
| `npm run test:dev-scripts` | **2/2 tests passed** |
| `git diff --check` | **Clean** |

The four backend warnings came from two authentication middleware tests that
intentionally use short fixture HMAC keys; they were not ProjectX failures.
The frontend test command disables the host Node 25 experimental Web Storage
shim so jsdom supplies its normal test implementation.

Targeted runs, which overlap the full suites above, also passed:

- ProjectX credential/account/client/hub/safe-logging set: **118 passed**.
- Persistent expense suppression/readiness/schema/migration set: **54 passed**.
- Backend stale-deadline set: **53 passed**.
- Frontend API/provider-state/deadline/AppShell/Accounts set:
  **5 files, 42 tests passed**.
- Combine tracker/reconciliation/Expenses page set:
  **3 files, 48 tests passed**.

The regression cases include:

- environment credential present but no signed-in user's stored credential;
- stored credential with missing, wrong, or malformed encryption key;
- signed-in user's decrypted credential driving a successful refresh while
  another user's row remains untouched;
- refreshed name, balance, visibility, tradability, and `last_seen_at`;
- provider outage with explicit cached fallback and safe structured logging;
- provider failure with no returnable cache producing a non-200 error;
- recent cache-only data remaining fresh, old cache becoming stale, and CSV
  remaining not applicable;
- local-first frontend data being replaced by provider success and failed
  fallback responses not being cached as success;
- a Live CSV selection remaining stable while ProjectX rows update in the
  dropdown, including an explicit Accounts-page refresh that starts from Live;
- newest-first/oldest-last provider refresh completion preserving the newest
  cache lane, and a mixed stale/fresh warning using its representative row's
  timestamp;
- cached and already-rendered rows becoming stale at the server-provided
  deadline without another provider request;
- stale/cached-fallback reconciliation performing no expense or ledger
  mutation;
- standard and DLL variants for all three plan sizes and both intended account
  states;
- exact generated price, type, provider, category, currency, description, tag,
  account, and date metadata;
- one new combine creating exactly one expense and a second run creating none;
- arbitrary-priced manual expenses taking precedence;
- deterministic auto-duplicate cleanup, unrelated spreadsheet dates not
  suppressing new accounts, and deleted/suppressed rows not being recreated;
- development environment snapshots requiring supervisor reconstruction; and
- ProjectX trade, hub, streaming, backtest, bot, and HTTP-conversion logs and
  responses omitting sentinel secrets embedded in exception text;
- user-scoped persisted deletion tombstones surviving an empty browser ledger,
  blocking server-side auto recreation, and leaving duplicate cleanup
  unsuppressed; and
- readiness failing closed when the suppression migration, table, columns, or
  fresh-schema baseline is absent.

## Remaining risks and manual steps

1. No live provider authentication was attempted. Unit/integration tests prove
   the normal resolver and client behavior with controlled provider responses,
   but a non-production forced refresh is still needed to verify the deployed
   user's current credential, provider access, network, and clock.
2. `status: ready` proves decryption only. Operators must not present it as
   “connected”; only a successful forced refresh proves current provider
   authentication and account search.
3. The routed frontend does not currently provide a dedicated ProjectX
   credential-management screen. Reconnecting a credential still requires the
   existing authenticated API or an authorized administrative workflow.
4. `/api/accounts` remains a list response. An empty successful provider result
   cannot carry response-level refresh metadata, although refresh failures with
   no returnable cache are non-200 and therefore cannot masquerade as that
   successful empty result.
5. The inferred combine purchase date and baseline remain browser-ledger or
   existing-expense history. Deletion suppression itself is now server-side
   and user-scoped, but there is no UI to remove an intentional suppression
   tombstone if the user later wants auto-generation re-enabled.
6. Reconciliation performs multiple expense API calls rather than one database
   transaction. A partial API failure is reported and deterministic retry can
   repair it, but the operation is not atomic across all creates/deletes.
7. Combine pricing is code-configured and can become outdated if Topstep
   changes its plans. Prices should be reviewed before a production release.
8. The new `20260729_add_expense_suppressions.sql` migration must be applied
   before serving this build. Readiness now fails closed if its ledger marker,
   table, or required columns are absent.
9. The reload boundary is covered by utility tests and documentation, but the
   actual local supervisor/HMR/Uvicorn matrix still needs a running-process
   smoke test.
10. Dependency installation reported four high-severity npm advisories. They
    were not automatically changed because dependency remediation was outside
    this integration audit and can introduce unrelated behavior changes.

## Running UI verification checklist

Perform this only in a disposable local or staging environment. Use a test
ProjectX identity and reversible expense fixtures.

- [ ] Start `npm run dev`, sign in as the intended user, and confirm that the
  credential status reports configured/decryptable separately. Do not treat
  `ready` as authenticated.
- [ ] Open the account dropdown. Confirm the saved snapshot appears first,
  “Refreshing ProjectX accounts…” appears for a ProjectX selection, and a
  successful refresh replaces the snapshot with a provider-success message.
- [ ] Inspect the forced `/api/accounts?refresh_provider=true` response in the
  browser network panel. Confirm returned ProjectX rows are
  `provider_fresh`, non-stale, and have a last-successful-refresh time. Do not
  inspect or copy credential values.
- [ ] In staging, simulate a rejected credential and a provider/network outage.
  Confirm an actionable auth/config/network message appears, cached rows are
  labeled as fallback, and an unfiltered no-cache failure is non-200.
- [ ] Request a cache-only snapshot. Confirm a recent ProjectX row is
  `cache_fresh`, an aged row is `cache_stale`, and every Live CSV row is
  `not_applicable` and non-stale.
- [ ] Select a Live CSV account while ProjectX rows are also present. Confirm
   the Live selection remains stable and does not itself start a ProjectX
   refresh; if an earlier refresh is already in flight, its rows may update the
   dropdown without changing the selection. While Live remains selected, use
   “Refresh Express Accounts” and confirm newly discovered rows and refreshed
   names also propagate into the shell dropdown.
- [ ] On Expenses, attempt reconciliation while the provider is unavailable or
  cached. Confirm the operation explains why it is blocked and that no expense
  or local combine-ledger value changes.
- [ ] With fresh fixture accounts covering standard/DLL 50K, 100K, and 150K
  names in `ACTIVE` and `LOCKED_OUT`, reconcile once. Confirm exact price,
  plan, type, tags, description, account, and purchase date.
- [ ] Reconcile again. Confirm “already reconciled,” no duplicate expense, and
   unchanged totals. Add a manual fixture expense for one account and confirm it
   wins over the generated row. Delete/suppress an auto fixture, clear local
   site data or use another staging browser, and confirm the server tombstone
   prevents recreation. Confirm duplicate-cleanup deletes do not suppress the
   retained generated row.
- [ ] Use a fresh successful response with no recognized combine names. Confirm
  the UI explicitly says that ProjectX refreshed but no eligible combines were
  found.
- [ ] Reload the browser and confirm backend environment behavior is unchanged.
  Edit frontend source and confirm Vite HMR updates code only. Edit Python and
  confirm Uvicorn reloads code while retaining its startup environment.
- [ ] Change a harmless test-only value in `backend/.env`. Confirm the wrapper
  instructs a full restart and does not claim to have loaded the value. Stop
  and restart `npm run dev` (or `npm run dev:backend`) and only then verify the
  new value is active.
- [ ] Review logs from the above scenarios. Confirm only structured reason
  codes and safe metadata appear—never usernames, credentials, bearer tokens,
  encrypted blobs, or raw provider payloads.
