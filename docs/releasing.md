# Releasing TopSignal

Use `npm run release -- ...` from the development checkout. This replaces the
per-release scripts previously written under ignored audit folders. It does not
commit or push code, enable trading, change strategy settings, or delete data.

Install development dependencies with `npm ci`, `npm --prefix frontend ci` and
`backend/.venv/Scripts/python -m pip install -r backend/requirements-test.txt`.
The command uses that virtual environment. Git, Node/npm, OpenSSH and authenticated
GitHub CLI (`gh`) must be available. The production host remains the existing
private Windows device; the remote SSH identity needs its existing administrative
deployment permissions, while the scheduled application stays under the limited
service identity.

## Routine release

1. Finish reviewing the complete source and commit it. A package requires a clean
   working tree. A `validate` run can be used before committing; its receipt is
   reusable only if both the input fingerprint and commit still match.
2. Validate and package once:

   ```powershell
   npm run release -- package --output tmp/releases/candidate-001
   ```

   This runs every offline backend test with four workers, the complete frontend
   suite, lint, deployment regressions, dependency audits, and the production
   loopback build. It stores timed logs and `validation.json` alongside the bundle
   in `candidate-001-validation`. PostgreSQL migration/concurrency tests still run
   in their existing GitHub CI lane. No test selection or changed-file heuristic
   is used. Packaging uses the built assets without rebuilding them.

   To separate validation and packaging on the same committed source:

   ```powershell
   npm run release -- validate --output tmp/releases/check-001
   npm run release -- package --receipt tmp/releases/check-001/validation.json --output tmp/releases/candidate-001
   ```

   Any changed tracked/untracked source, frontend environment/build input, commit,
   missing validation lane, or changed built asset invalidates the receipt.
   Outputs must be outside the repository or in a Git-ignored folder.
3. Push the reviewed commit normally. Prepare the candidate while CI runs:

   ```powershell
   npm run release -- stage --bundle tmp/releases/candidate-001 --host-config tmp/release-host.json
   ```

4. Once CI succeeds, activate the same bundle:

   ```powershell
   npm run release -- deploy --bundle tmp/releases/candidate-001 --host-config tmp/release-host.json
   ```

   `deploy` revalidates preparation, checks the newest `ci.yml` run for the exact
   commit, and requires all six existing CI job names, including `verify`, to
   succeed. If CI is unfinished or failed, preparation is retained and the
   running release is unchanged. Run the same command after CI passes. The host
   also requires the fresh checksum-bound CI receipt passed by this command.

`npm run release -- verify --bundle ...` verifies a local package without
connecting to production. Never overwrite a failed candidate: preserve its logs,
correct the issue, and use a new release ID/output directory. Fully prepared
candidates can be reused with exactly the same host configuration and manifest.

## Private host configuration

Create `tmp/release-host.json` using the actual running release, configuration IDs,
existing credential helper and SSH identity. This is a template; do not assume
the example release name or bot ID is still current.

```json
{
  "host": "kaitl@100.99.71.73",
  "key": "C:/Users/drews/.ssh/peteamdpc_ed25519",
  "repository": "drewstake/TopSignal",
  "staging_root": "C:/ProgramData/TopSignal/staging",
  "releases_root": "C:/ProgramData/TopSignal/releases",
  "previous_release": "C:/ProgramData/TopSignal/releases/REPLACE_WITH_RUNNING_RELEASE",
  "release_id": "REPLACE_WITH_NEW_RELEASE_ID",
  "runtime_root": "C:/ProgramData/TopSignal/runtime",
  "credential_script": "C:/ProgramData/TopSignal/tools/paper-v2r1-17/read-service-credential-v2.ps1",
  "bot_config_ids": [39],
  "companion_files": {
    "backend/tools/backup_market_cache.py": "C:/ProgramData/TopSignal/tools/depth-recorder-v1/backup_market_cache.py"
  }
}
```

The credential helper must return only a `PSCredential` for the existing limited
service identity. No password or broker credential belongs in this JSON. Host-key
checking remains mandatory. The current command supports the existing task name
`TopSignal Personal Device` and loopback ports 8000/4173. Enabled/running bots
outside the declared IDs block activation. Independent scheduled helpers must
be listed in `companion_files` and match their candidate source; tool changes
need their own reviewed lifecycle rollout before this application release.

## Preserved gates and recovery

- Entire source/asset archives are SHA-256 checked before extraction. Reject
  traversal, links, Windows device names, duplicate/case-colliding paths, extra
  files, secrets and local runtime/storage directories. Candidate and previous
  release paths are distinct; existing releases are never overwritten.
- Source, assets, private environment, certificates, and installed dependency
  inventories are checked again immediately before switching and after startup.
  An unchanged requirements file allows copying the previous Python environment
  into a release-owned directory. Shared environment junctions are not created
  or recursively hardened. Changed runtime dependencies get a fresh environment.
- Staging and candidate directories are private before copying configuration.
  The existing installer validates the limited service identity and every
  candidate ACL. Runtime ACL validation remains unchanged. The running release
  and its dependency environment are never modified by preparation.
- Database migration readiness is checked without applying migrations. Pending
  migrations, storage cutovers, changed private configuration/certificates and
  independent recorder/tool upgrades require their existing reviewed procedures
  before using this routine activation path. Follow
  [the storage policy](supabase-storage-policy.md) and
  [Windows operations](windows-24x7-operations.md); no cloud history, retention
  cleanup or backup is silently introduced by this command.
- Broker calls are restricted to login/account/position/order reads. Check all
  scoped accounts are eligible and simulated, configuration and runner identity
  are unchanged, no order attempt is pending/unknown, and positions/orders are
  flat before stopping and again before activation. The application security
  validator also checks local authority, authentication and production settings.
- A uniquely owned STOP marker and existing supervisor lock coordinate graceful
  shutdown. A pre-existing or replaced operator STOP is never removed. The task
  keeps its existing principal, triggers, settings and arguments except release
  paths. No force-kill is used to meet a deadline.
- Verify actual frontend bytes, API liveness/readiness, worker health, broker
  configuration, independent recorder PID, all other TopSignal task definitions,
  and three recording observations over 20 seconds. Missing recording, new
  drops, capture restarts or durability regression fail verification. The API's
  local-device authentication is required for the recording check.
- If candidate startup/verification fails, restore the previous task action and
  verify it. Recheck broker exposure and release integrity before stopping a
  partially started candidate. If exposure is active or the rollback gate cannot
  be verified, stop the rollback procedure and report the error, leaving that
  candidate available to manage its position. A shutdown timeout leaves STOP
  latched for operator diagnosis. Never blindly retry an interrupted switch;
  inspect task/marker ownership and `activation.log` first.

Host receipts stay in the private staging directory: `prepared.json`,
`broker-before.json`, `broker-latest.json`, `ci.json`, `recording-verification.json`,
task XML and `activation.log`. Retain the previous release for rollback; cleanup
is deliberately a separate authorized operation.

## Benchmarking

```powershell
backend/.venv/Scripts/python scripts/benchmark-backend-tests.py --workers 2 4 --repeat 2 --output tmp/windows-benchmark
```

Each complete suite runs separately on the same machine. The second round
reverses worker-count order. The runner rejects failures, source drift, different
test identities/outcomes, or different passed-subtest counts; it retains logs,
JUnit reports and timings. An unsuccessful run never claims a speedup.

The manually dispatched `Benchmark Windows tests` GitHub workflow repeats this
on a hosted Windows runner with CI's Python version. Its timing is separate from
the local measurements in the [November 10 report](audits/2026-11-10/release-speed.md).
