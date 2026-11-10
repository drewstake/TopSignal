# Release speed validation — November 10, 2026

The full Windows backend suite now runs in four isolated processes, grouping
tests by file. No tests, assertions, skips or CI safety lanes were removed. The
Linux backend and PostgreSQL integration lanes remain unchanged. Windows keeps
the existing fail-fast behavior, standard temporary paths, 60-second diagnostic
dump threshold and ten-minute job timeout, and now retains JUnit evidence.

## Measured local result

Windows 11 build 26200; Python 3.12.10; 16 logical CPUs; pytest 9.0.3;
pytest-xdist 3.8.0. Full suites ran in order 0, 2, 4, 4, 2, 0 workers, with no
other backend suite running concurrently. Wall time includes interpreter/worker
startup and shutdown. Ordinary development activity was not disabled.

| Workers | Round 1 | Round 2 | Median | Relative speed |
| --- | ---: | ---: | ---: | ---: |
| Serial | 110.562 s | 120.004 s | 115.283 s | 1.00x |
| 2 | 62.644 s | 65.810 s | 64.227 s | 1.79x |
| 4 | 38.176 s | 39.678 s | 38.927 s | 2.96x |

Four workers saved **76.356 seconds / 66.2%** on the median full-suite run.
Each run passed **3,216 tests and 8 subtests**, with the same **20 skips**.
JUnit test identities, duplicate counts and outcomes matched exactly in all six
runs. All six log summaries also confirmed eight passed subtests; the benchmark
runner now records and compares that count automatically as well.

The source fingerprint covering backend app/tests/tools, test requirements and
pytest configuration was unchanged throughout:
`ae862b35fa426e7bf78f5ea1ddb86fda8bb570c22dd3c0bf5c760effd1ad39c7`.
The pending application changes already in this worktree were included; this is
not a benchmark of the older deployed commit. See `release-speed.json` for the
machine-readable result. Detailed local evidence is retained under ignored
`tmp/release-speed/comparison-v2/`.

An exploratory two-worker run before the six-run comparison failed in the
offline-runner subprocess fixture during collection (return code 2). Its
assertion now includes child stdout/stderr for diagnosis; no assertion was
relaxed. The targeted parallel rerun, another full parallel run and all six final
benchmark runs passed. The original cause was not established, so a future
recurrence must be investigated rather than retried into a green release.

## Hosted CI and deployment limits

The observed pre-change November 10 CI run took 6m20s, with 4m29s inside Windows
backend tests and 5m56s for the whole Windows job. November 4's job took 7m34s,
including 6m05s for backend tests. Those are historical hosted results, not an
after-change measurement. The new manual benchmark workflow can measure the
same source on GitHub's Windows/Python 3.11 runner after these changes are pushed.
Do not present the local 2.96x result as measured GitHub or end-to-end deployment
speed.

The reusable `npm run release` pipeline removes per-release script authoring,
reuses exact validation/build artifacts, and stages while CI runs. Preparation,
activation and rollback are separately guarded. Source packaging, stale receipt
rejection, archive tampering, broker/recording gates, exact-commit CI requirements,
operator STOP ownership and switch recovery are covered by executable local
regressions. Existing Windows deployment regressions are retained.

The actual `npm run release -- validate` command also completed its full local
pipeline: 3,216 backend tests plus 8 subtests (20 expected skips), 1,280 frontend
tests across 155 files, ESLint, all 46 Node script regressions, both dependency
audits and the TypeScript/Vite production build. Its seven measured commands
totaled 91.083 seconds. This is a measurement of the new local validation path,
not an apples-to-apples full-release comparison. The release regression wrapper
also runs 12 Python cases and executable PowerShell switch scenarios, including
real temporary Git packaging, private staging order, dependency-copy integrity,
previous-asset preservation and candidate reuse.

No commit, push, production switch, broker order, migration, retention change or
billing change was performed for this optimization. Actual production activation
and its timing remain unmeasured; the command's first use must retain its normal
readiness, recording and rollback checks. See [the release runbook](../../releasing.md).
