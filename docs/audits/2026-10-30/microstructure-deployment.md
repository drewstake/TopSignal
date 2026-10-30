# Microstructure workspace production deployment

On October 30, 2026, the user authorized committing, pushing and deploying the
completed research workspace and automatic-collection fix. Application commit
`e06b6693ec045ac3dc4a365f49797d0b1c69e601` was pushed to `origin/main` and deployed
to peteamdpc as `C:/ProgramData/TopSignal/releases/20261030-research-24`.

The private dashboard exposes `/microstructure`, `/event-study` and
`/research-notebook` at `http://127.0.0.1:4173` through the existing connection.
Account selection and authentication remain required. The production backend
reads native recordings under `D:/TopSignal/data/databento`; it has no SSH source
configuration. Development continues to use its existing private host reader.

## Release verification

- 169 relevant backend tests, 46 frontend tests and 44 startup/deployment tests
  passed. The production TypeScript/Vite build and `git diff --check` passed.
- The source archive contained 980 files from the committed revision. Deployment
  verified every source hash and the served frontend against its manifest.
- Only five `backend/app` files changed: the API registration, research API,
  research service, request models and study implementation. Database migrations,
  dependency lockfiles and the strategy implementation were unchanged.
- Hardened release/runtime permissions and production security validation passed.
  The existing environment, service identity and public authentication settings
  were preserved. Previous frontend assets remain available for open browser tabs.
- Read-only database checks before and after deployment confirmed identical bot
  configurations, the same running paper-bot record, and unchanged hashes for all
  three installed model files. Both live-execution gates remain false.
- Strategy implementation SHA-256 remains
  `856cee44fec03ded157e8d64423a19573027417f13a4dcb07d541f2b091a6da5`.

Package SHA-256 values:

| Package | SHA-256 |
| --- | --- |
| Git source ZIP | `76c1a62172b3dae93c166e6a4906928ba2d3ff14f03718dda962d414c902cb26` |
| Frontend dist tar | `331411f3e480d6a02be82f00de8d485f31ddcc1d3488acd069f808d992437992` |

## Activation and continuity

The release switch began at **19:18:57 UTC**. The preceding supervisor stopped
gracefully at **19:19:05**, and the frontend, API, worker and readiness checks all
passed by **19:19:26**. This deployment briefly restarted the main service; the
switch-to-healthy verification took about 30 seconds. The prior release
`20261030-history-23`, original task XML and rollback action were retained.

The independent recorder was not restarted. Its process IDs **12088 and 12224**
and creation times were identical across activation, and its scheduled-task
definition was unchanged. At 19:23:46 UTC, PID 12224 had received **4,406,360
events**, with one continuous connection, **zero reconnects and zero errors**.
Its original start time remains 17:04:49 UTC. The pre-existing ignored overlapping
watchdog launch result is not a recorder failure; continuous process identity,
fresh durable receipts and status were checked directly.

The automatic feature task retains its S4U/Limited identity, five-minute schedule,
startup trigger, below-normal priority and storage limits. Five helper files were
aligned byte-for-byte with the Git archive after proving that the installed
copies differed only in CRLF/LF line endings. The idle feature task was briefly
disabled during this file replacement and then re-enabled; no running collection
was interrupted. Prior helper copies and derived artifacts were preserved.
This alignment allows the collector and deployed reader to share the same code
fingerprint without weakening cache provenance checks.

The next timer-driven launch began at **19:22:22 UTC** and completed at
**19:23:18 UTC**, with exit code **0**. It produced **1,117 observations** for
**19:15–19:20 UTC**, adding **1,280,376 bytes** including its manifest. The deployed
reader then returned the same 1,117 observations with **derived cache reuse
confirmed**, no skipped segments, and matching measurement code SHA-256
`1c48f47db4a32c3cc3ca52d514ee95ca33346d15b6ecf3b488e6e42cefc1ce92`.
At the final check the main service and recorder were Running, the collector was
Ready for its next trigger, no operator STOP existed, and all health endpoints
returned 200. Approximately 72.2 GB remained free on C: and 1.23 TB on D:.

## Real data and browser verification

The staged native reader reconstructed **1,114 observations** for
**18:50–18:55 UTC**, with no skipped segments. The authenticated production UI
then completed that same bounded replay job, displayed its observed-depth heatmap,
price/depth charts and trade-flow measurements, and advanced/paused playback.
Unauthenticated access to the research coverage API returned 401.
Coverage at verification included 512 committed raw segments from 12:26:58 UTC
through 19:19:26 UTC on one recorded day. Coverage is not a completeness claim.

Observed-book limitations, unverified snapshot/sequence completeness and unavailable
directional trade semantics remain visible. This deployment provides no new claim
of a predictive or profitable edge. The existing development experiment notebook
was not migrated; production research artifacts use the production cache.

## Backup and evidence

The existing off-device backup completed before activation at **19:14:28 UTC**:
**1,333 files / 487,492,951 bytes**, verified with no failure. Its destination ends
in `backups/20261030T191328Z-ae92af1a`. The existing daily schedule and storage
budgets were preserved. No historical data was written to Supabase, no schema
migration was run, and no recordings or prior releases were deleted.

Private deployment scripts, manifests, original task XML, configuration/model
comparison receipts and activation logs are retained under
`C:/ProgramData/TopSignal/staging/microstructure-24` on the host, with an off-device
copy under the development cache's `audits/microstructure-deploy-24` directory.
The production browser screenshot is `production-replay.png` in that audit copy.
These ignored artifacts are intentionally outside Git.

See the [collection activation receipt](microstructure-automatic-collection.md)
and [operator guide](../../microstructure-research.md) for limits and recovery.
