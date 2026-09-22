# Test evidence and replay commands

All commands run from repository root unless specified. Results apply to baseline `df83159`; only audit artifacts were added.

| Check | Result | Duration / notes |
|---|---|---|
| Backend offline suite | 2,111 passed / 9 skipped | 38.66 s; external connections blocked=0 (no attempts) |
| Frontend compatible runtime | 963 passed / 1 failed | 15.64 s; 115 files passed, 1 failed |
| Audit probes | 11 passed / 7 xfailed | 0.93 s; strict xfails |
| Frontend build | Passed | 4.76 s |
| Frontend lint | Passed | No reported lint errors |
| Dev scripts | 35 passed / 5 skipped | 1.17 s; skips Windows-only |

```sh
backend/.venv/bin/python docs/audits/2026-09-22/run_isolated.py backend-tests-standard
backend/.venv/bin/python docs/audits/2026-09-22/run_isolated.py frontend-tests-compatible
backend/.venv/bin/python docs/audits/2026-09-22/run_isolated.py frontend-build
backend/.venv/bin/python docs/audits/2026-09-22/run_isolated.py frontend-lint
PYTHON_DOTENV_DISABLED=1 backend/.venv/bin/python backend/tools/run_offline_tests.py tests/test_audit_regressions.py -q --tb=short
npm run test:dev-scripts
PYTHONPATH=backend backend/.venv/bin/python docs/audits/2026-09-22/benchmark_metrics.py
```

The standard backend runner is authoritative: initial direct pytest runs under the manual-app environment produced seven harness-related failures. Do not count these as product bugs. The standard runner installs its own network audit hook and test defaults.

The first frontend invocation on Node 25.9.0 failed broadly because localStorage methods were absent. The compatible mode disables experimental WebStorage and matches the test suite's API URL and auth fixture conventions. Intermediate failures caused by the audit API base differing from hard-coded test mocks were resolved in the launcher; they are not product findings.

The sole final frontend failure is `BotPage.accountState.test.tsx:691`: the ordinary Stop test expects obsolete text. See AUD-08. Subsequent behavior assertions in that test are not reached on this run.

Backend skips: six parameterized PostgreSQL concurrency cases, two PostgreSQL worker/timeout cases, and one optional fixed market capture. Live authentication/provider, PostgreSQL and Windows results must not be inferred from SQLite/macOS tests.

The new tests use actual ASGI request dispatch, in-memory SQLite, signed fixture JWTs and dependency overrides. They do not use a live network server and do not validate browser authentication UX. Their xfails are strict so a changed outcome forces review. They were subsequently promoted to `backend/tests/test_audit_regressions.py` during remediation and now run in the normal backend suite. The counts above preserve the original audit baseline; see fixes.md for current results.

Raw runner output remains locally under `/tmp/topsignal-audit-*.log`; final audit probe output and synthetic benchmark results are retained beside this document. Tests generated no production writes.
