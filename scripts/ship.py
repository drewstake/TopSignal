"""Ship reviewed changes to peteamdpc. Run via npm run release -- ship --help."""
from __future__ import annotations

import argparse
import base64
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import zipfile

from dashboard_release import commit, inspect_artifact, verify_page, write_atomic
from release import ROOT, NPM, ps_quote, remote
from release_lib import companion_digest, sha256, verify_bundle


def output(args, **kwargs):
    return subprocess.check_output(args, cwd=kwargs.pop("cwd", ROOT), **kwargs)


def git(*args, cwd=ROOT):
    return output(["git", *args], cwd=cwd, text=True).strip()


def gh(*args):
    return json.loads(output(["gh", *args]))


def now():
    return datetime.now(timezone.utc).isoformat()


class Timing:
    def __init__(self, folder):
        folder.mkdir(exist_ok=True, parents=True)
        self.path = folder / "timing.json"
        self.data = json.loads(self.path.read_bytes()) if self.path.exists() else {"started": now(), "steps": []}

    def verified(self):
        end = datetime.now(timezone.utc)
        self.data["verified"] = end.isoformat()
        for key, label in (("push_started", "push_to_verified_seconds"), ("ship_started", "ship_to_verified_seconds")):
            if key in self.data:
                self.data[label] = round((end - datetime.fromisoformat(self.data[key])).total_seconds(), 3)
        write_atomic(self.path, self.data)

    @contextlib.contextmanager
    def step(self, name):
        print(name, flush=True)
        start = time.monotonic()
        row = {"step": name, "start": now()}
        self.data["steps"].append(row)
        write_atomic(self.path, self.data)
        try:
            yield
        except BaseException as error:
            row["error"] = str(error)
            print(f"Failed at {name}. Receipt: {self.path}", file=sys.stderr)
            raise
        finally:
            row.update(end=now(), seconds=round(time.monotonic() - start, 3))
            write_atomic(self.path, self.data)


# Executed with the running release's interpreter. Only non-secret fields of
# protected receipts leave peteamdpc. Verify the task, source and served HTML.
DISCOVER = """
import hashlib,json,sys,re,urllib.request
from pathlib import Path
r,s=map(Path,sys.argv[1:])
s=s/r.name
m=json.loads((s/'manifest.json').read_bytes())
c=json.loads((s/'host.json').read_bytes())
assert hashlib.sha256((s/'manifest.json').read_bytes()).hexdigest()==c['manifest_sha256']
assert m['git_commit']==c['git_commit']
sys.path.insert(0,str(r/'scripts'))
from release_lib import verify_files
verify_files(r,m['source_sha256'])
f=r/'frontend'
p=f/'dashboard.json'
state=json.loads(p.read_bytes()) if p.exists() else {'active':None,'previous':None}
active=state['active']
assert active is None or re.fullmatch('[0-9a-f]{40}',active)
files=json.loads((f/'dashboard-receipts'/(active+'.json')).read_bytes()) if active else {n[14:]:h for n,h in m['dist_sha256'].items()}
verify_files(f/'dist/dashboard-builds'/active if active else f/'dist',files)
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:4173/bot',timeout=5) as resp:
 assert hashlib.sha256(resp.read()).hexdigest()==files['index.html']
companions={}
for n,t in c['companion_files'].items():
 b=Path(t).read_bytes()
 companions[n]=hashlib.sha256(b.replace(b'\\r\\n',b'\\n') if n.endswith('.py') else b).hexdigest()
keys=['runtime_root','credential_script','bot_config_ids','companion_files','staging_root','releases_root']
print(json.dumps(dict(release=str(r),backend=m['git_commit'],publisher=(r/'scripts/dashboard_release.py').is_file(),
 complete=(s/'task-after.xml').exists() and (s/'recording-verification.json').exists(),
 dashboard_verified=not active or (f/'dashboard-receipts'/(active+'.verified.json')).is_file(),
 config={k:c[k] for k in keys}, companions=companions,files=files,**state)))
"""


class Host:
    def __init__(self, config):
        self.config = config
        self.staging = config["staging_root"].rstrip("/")
        if not re.fullmatch(r"[A-Za-z]:/[\w./-]+", self.staging) or ".." in self.staging.split("/"):
            raise ValueError("Invalid staging root")
        if not re.fullmatch(r"[\w.-]+@[\w.-]+", config["host"]):
            raise ValueError("Invalid SSH target")
        self.options = ["-i", config["key"], "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        self.identity = """
$t=Get-ScheduledTask -TaskName 'TopSignal Personal Device'
$r=$t.Actions[0].WorkingDirectory
if ($t.State -ne 'Running' -or $t.Actions.Count -ne 1 -or !$t.Actions[0].Arguments.Contains((Join-Path $r 'scripts\\run-production.ps1'))) {throw 'Unexpected running task'}
$py=Join-Path $r 'backend\\.venv\\Scripts\\python.exe'
"""

    def ps(self, code):
        script = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';" + code
        encoded = base64.b64encode(script.encode("utf-16le")).decode()
        return output(["ssh", *self.options, self.config["host"], "powershell.exe", "-NoProfile", "-NonInteractive",
                       "-EncodedCommand", encoded], text=True).strip()

    def upload(self, path, directory):
        subprocess.run(["scp", *self.options, str(path), self.config["host"] + ":" + directory + "/"], check=True)

    def state(self):
        code = base64.b64encode(DISCOVER.encode()).decode()
        return json.loads(self.ps(self.identity + f"""
& $py -B -c "import base64;exec(base64.b64decode('{code}'))" $r {ps_quote(self.staging)}
if ($LASTEXITCODE) {{exit $LASTEXITCODE}}
"""))

    def dashboard(self, action, state, folder, target, digest):
        directory = self.staging + "/" + folder
        args = " ".join(map(ps_quote, [action, state["release"], self.staging + "/" + Path(state["release"]).name,
                                       directory, state["active"] or "base", target or "base", digest]))
        return json.loads(self.ps(f"""
$guard=[IO.File]::Open({ps_quote(self.staging + '/publish.lock')},'OpenOrCreate','ReadWrite','None')
try {{
{self.identity}
if ($r -ine {ps_quote(state["release"])}) {{ throw 'Full release changed since classification' }}
& $py -B (Join-Path $r 'scripts/dashboard_release.py') {args}
if ($LASTEXITCODE) {{exit $LASTEXITCODE}}
}} finally {{$guard.Dispose()}}
"""))


def ci_status(revision, repo, dashboard):
    required = {"Frontend tests", "Frontend quality"} if dashboard else {
        "Frontend tests", "Frontend quality", "Backend tests", "Database migrations", "Windows compatibility", "verify"}
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        runs = gh("run", "list", "--repo", repo, "--workflow", "ci.yml", "--branch", "main", "--event", "push",
                  "--commit", revision, "--limit", "1", "--json", "databaseId,headSha")
        if runs and runs[0]["headSha"] == revision:
            run = gh("run", "view", str(runs[0]["databaseId"]), "--repo", repo, "--json",
                     "attempt,databaseId,createdAt,conclusion,headSha,jobs,url")
            if run["headSha"] != revision:
                raise ValueError("CI returned another commit")
            if run["conclusion"] not in ("", "success", None) or any(j["conclusion"] in {
                    "failure", "cancelled", "timed_out", "action_required"} for j in run["jobs"]):
                raise RuntimeError("CI failed: " + run["url"])
            passed = {j["name"] for j in run["jobs"] if j["conclusion"] == "success"}
            if required <= passed and (dashboard or run["conclusion"] == "success"):
                return run
            if run["conclusion"] == "success":
                raise RuntimeError("Required CI jobs missing: " + run["url"])
        time.sleep(4)
    raise RuntimeError("Timed out waiting for exact-commit CI")


def ci_artifact(folder, revision, repo, run):
    name = f"dashboard-{revision}-{run['attempt']}"
    api = f"repos/{repo}/actions"
    artifacts = gh("api", f"{api}/runs/{run['databaseId']}/artifacts?name={name}")["artifacts"]
    matches = [a for a in artifacts if a["name"] == name and not a["expired"] and
               a["workflow_run"]["head_sha"] == revision and a["workflow_run"]["id"] == run["databaseId"]]
    if len(matches) != 1 or not re.fullmatch("sha256:[a-f0-9]{64}", matches[0].get("digest") or ""):
        raise ValueError("Missing or ambiguous exact-run CI artifact")
    item = matches[0]
    digest = item["digest"][7:]
    path = folder / "dashboard.zip"
    if not path.exists() or sha256(path) != digest:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as temp:
            subprocess.run(["gh", "api", f"{api}/artifacts/{item['id']}/zip"], stdout=temp, check=True)
        if sha256(Path(temp.name)) != digest:
            raise ValueError("Downloaded CI artifact digest mismatch")
        os.replace(temp.name, path)
    return path, digest


def clean_package(folder, revision):
    bundle = folder / "bundle"
    if bundle.exists():
        if verify_bundle(bundle)["git_commit"] != revision:
            raise ValueError("Cached full bundle belongs to another commit")
        return bundle
    checkout = Path(tempfile.gettempdir()) / "ts-ship" / revision[:16]
    if not checkout.exists():
        git("worktree", "add", "--detach", str(checkout), revision)
    if git("rev-parse", "HEAD", cwd=checkout) != revision or git("status", "--porcelain", cwd=checkout):
        raise ValueError("Private release checkout changed")
    env = dict(os.environ, TOPSIGNAL_TEST_PYTHON=sys.executable, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    if any(n.startswith("VITE_") for n in env) or any(p.name != ".env.example" for p in (checkout / "frontend").glob(".env*")):
        raise ValueError("Unexpected private Vite inputs in isolated release build")
    dependency_marker = folder / "installed"
    if not dependency_marker.exists() or not (checkout / "frontend/node_modules/typescript").exists():
        for location in (checkout, checkout / "frontend"):
            subprocess.run([NPM, "ci", "--no-audit", "--fund=false"], cwd=location, env=env, check=True)
        dependency_marker.touch()
    # Keep failed attempts and their logs. A completed validation for these
    # exact inputs can be reused even if packaging was interrupted.
    attempts = sorted(folder.glob("package-*-validation/validation.json"), key=lambda p: p.stat().st_mtime)
    attempt = folder / f"package-{time.time_ns()}"
    cmd = [sys.executable, "-B", str(checkout / "scripts/release.py"), "package", "--output", str(attempt)]
    if attempts:
        cached = subprocess.run([*cmd, "--receipt", str(attempts[-1])], cwd=checkout, env=env)
        if cached.returncode:
            attempt = folder / f"package-{time.time_ns()}"
            cmd[-1] = str(attempt)
            subprocess.run(cmd, cwd=checkout, env=env, check=True)
    else:
        partials = sorted(folder.glob("package-*-validation/partial.json"), key=lambda p: p.stat().st_mtime)
        if partials and partials[-1].stat().st_mtime >= dependency_marker.stat().st_mtime:
            cmd += ["--resume-tests", str(partials[-1])]
        subprocess.run(cmd, cwd=checkout, env=env, check=True)
    attempt.rename(bundle)
    return bundle


def check_companions(revision, state):
    for name, expected in state["companions"].items():
        if companion_digest(name, output(["git", "show", revision + ":" + name])) != expected:
            raise ValueError(f"Independent helper differs on peteamdpc: {name}. Its reviewed rollout is required.")


def install_view(bundle):
    # Run only the tested viewer and installer from the approved full bundle.
    manifest = verify_bundle(bundle)
    with tempfile.TemporaryDirectory(prefix="ts-view-") as directory, zipfile.ZipFile(bundle / "source.zip") as source:
        root = Path(directory)
        for name in ("scripts/install-dashboard-view.ps1", "backend/tools/serve_imbalance_dashboard.py"):
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_bytes(source.read(name))
            if sha256(path) != manifest["source_sha256"][name]:
                raise ValueError("Viewing handoff source differs from the full candidate")
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File",
                        str(root / "scripts/install-dashboard-view.ps1")], check=True)


def select_commit(args, upstream):
    if args.commit:
        return commit(git("rev-parse", "--verify", args.commit + "^{commit}"))
    if git("rev-parse", "HEAD") != upstream or git("branch", "--show-current") != "main":
        raise ValueError("Commit selection requires local main at origin/main")
    if not args.message:
        raise ValueError("Provide --message for the reviewed change")
    if args.paths:
        git("--literal-pathspecs", "commit", "--only", "-m", args.message, "--", *args.paths)
    else:
        if git("write-tree") != args.staged:
            raise ValueError("Staged snapshot differs from the reviewed tree")
        git("diff", "--cached", "--check")
        git("commit", "-m", args.message)
        if git("rev-parse", "HEAD^{tree}") != args.staged:
            raise ValueError("The commit differs from the reviewed tree; push refused")
    return commit(git("rev-parse", "HEAD"))


def main(argv):
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", metavar="TREE", help="exact reviewed git write-tree including partial-file edits")
    group.add_argument("--paths", nargs="+", help="complete reviewed tracked files, ignoring other staged changes")
    group.add_argument("--commit", help="resume the reviewed commit")
    group.add_argument("--rollback", metavar="COMMIT", help="roll back only this currently displayed dashboard")
    p.add_argument("--message")
    p.add_argument("--host-config", type=Path, default=ROOT / "tmp/release-host.json")
    p.add_argument("--prepare-only", action="store_true")
    p.add_argument("--install-publisher", action="store_true", help="first full switch after explicit approval")
    args = p.parse_args(argv)
    lock_dir = ROOT / "tmp/releases/ship"
    lock_dir.mkdir(exist_ok=True, parents=True)
    with (lock_dir / "ship.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ship(args)


def ship(args):
    started = now()
    supplied = json.loads(args.host_config.read_bytes())
    # Only connection settings survive. Resolve the current runtime profile
    # from the running task. Prior research/storage approvals never propagate.
    config = {key: supplied[key] for key in ("key", "host", "repository", "staging_root")}
    host = Host(config)
    if args.rollback:
        state = host.state()
        if state["active"] != commit(args.rollback):
            raise ValueError("Rollback commit is not the running dashboard")
        folder = ROOT / "tmp/releases/ship" / args.rollback
        timing = Timing(folder)
        with timing.step("rollback dashboard"):
            host.dashboard("rollback", state, "dashboard-" + args.rollback, state["previous"], "-")
        with timing.step("verify rollback through viewing connection"):
            after = host.state()
            if after["release"] != state["release"] or after["active"] != state["previous"]:
                raise ValueError("Dashboard changed during rollback verification")
            verify_page(after["files"], after["active"])
        print(f"Refresh peteamdpc. Verified rollback to {after['active'] or after['backend']}.")
        return
    git("fetch", "origin", "main")
    upstream = git("rev-parse", "origin/main")
    revision = select_commit(args, upstream)
    if upstream != revision and git("show", "-s", "--format=%P", revision) != upstream:
        raise ValueError("Release would push other commits or a divergent main")
    folder = ROOT / "tmp/releases/ship" / revision
    timing = Timing(folder)
    timing.data.setdefault("ship_started", started)
    with timing.step("push reviewed commit"):
        if upstream != revision:
            timing.data["push_started"] = now()
            git("push", "origin", revision + ":refs/heads/main")
    with timing.step("inspect peteamdpc"):
        state = host.state()
        if not state["complete"]:
            raise ValueError("Current full switch lacks final receipts; inspect activation.log")
        if (state["active"] or state["backend"]) == revision:
            if not state["dashboard_verified"]:
                raise ValueError("Dashboard switch lacks its health receipt; inspect or roll it back")
            verify_page(state["files"], state["active"])
            timing.verified()
            print(f"Refresh peteamdpc. Verified {revision}.")
            return
        check_companions(revision, state)
        if state["publisher"]:
            classifier = output(["git", "show", state["backend"] + ":scripts/dashboard-eligibility.cjs"])
            rule = json.loads(output(["node", "-", state["backend"], state["active"] or state["backend"], revision],
                                     input=classifier))
        else:
            rule = {"lane": "full", "reason": "peteamdpc needs its first publisher installation"}
        timing.data.update(route=rule, backend=state["backend"], dashboard=state["active"] or state["backend"])
        print(rule["lane"] + " release: " + rule["reason"], flush=True)
    fast = rule["lane"] == "dashboard"
    repo = config["repository"]
    # Re-key full preparation whenever the serving baseline changes.
    if not fast:
        with timing.step("validate and package full release"):
            bundle = clean_package(folder, revision)
        with timing.step("stage full release"):
            prefix = f"ship-{revision[:12]}-{Path(state['release']).name[-12:]}-{(state['active'] or 'base')[:12]}"
            # An interrupted extraction has no integrity receipt. Preserve its
            # evidence and use a fresh directory for the already verified bundle.
            full_id = host.ps(f"""
$id={ps_quote(prefix)}
$n=1
while ((Test-Path (Join-Path {ps_quote(state['config']['releases_root'])} $id)) -and
       !(Test-Path (Join-Path {ps_quote(host.staging)} "$id/prepared.json"))) {{
    $n++; $id={ps_quote(prefix)} + "-r$n"
}}
$id
""")
            if not re.fullmatch(re.escape(prefix) + r"(?:-r[0-9]+)?", full_id):
                raise ValueError("Unexpected full candidate directory")
            directory = host.staging + "/" + full_id
            profile = dict(config, **{k:v for k,v in state["config"].items() if k not in config},
                           previous_release=state["release"], expected_dashboard=state["active"], release_id=full_id)
            profile_path = folder / (full_id + ".json")
            write_atomic(profile_path, profile)
            expected_config = dict(profile, manifest_sha256=sha256(bundle / "manifest.json"), git_commit=revision)
            for key in ("key", "host", "repository"):
                del expected_config[key]
            ready = host.ps(f"""
$p={ps_quote(directory)}
if (Test-Path "$p/ready.json") {{
 $ready=Get-Content -Raw "$p/ready.json"|ConvertFrom-Json
 if ($ready.config_sha256 -ne (Get-FileHash "$p/host.json").Hash -or $ready.manifest_sha256 -ne {ps_quote(expected_config["manifest_sha256"])}) {{throw 'Prepared candidate mismatch'}}
 Get-Content -Raw "$p/host.json"
}}
""")
            if ready:
                if json.loads(ready) != expected_config:
                    raise ValueError("Prepared host configuration differs from the requested release")
            else:
                remote(bundle, profile_path, False)
    with timing.step("wait for exact-commit CI"):
        run = ci_status(revision, repo, fast)
        timing.data["ci"] = {k:run[k] for k in ("url", "createdAt", "attempt")}
        timing.data["ci"]["jobs"] = [{k:j[k] for k in ("name","startedAt","completedAt","conclusion")} for j in run["jobs"]]
        completed = max(j["completedAt"] for j in run["jobs"] if not fast or j["name"] in {"Frontend quality", "Frontend tests"})
        timing.data["ci"]["gate_seconds"] = (datetime.fromisoformat(completed.replace("Z","+00:00"))
                                             - datetime.fromisoformat(run["createdAt"].replace("Z","+00:00"))).total_seconds()
    if fast:
        name = "dashboard-" + revision
        directory = host.staging + "/" + name
        with timing.step("stage exact CI dashboard"):
            archive, digest = ci_artifact(folder, revision, repo, run)
            files = inspect_artifact(archive, revision)
            existing = host.ps(f"""
$p={ps_quote(directory)}
New-Item -Force -ItemType Directory -Path $p | Out-Null
if (Test-Path "$p/dashboard.zip") {{(Get-FileHash -LiteralPath "$p/dashboard.zip").Hash.ToLowerInvariant()}}
""")
            if existing != digest:
                host.upload(archive, directory)
            host.dashboard("stage", state, name, revision, digest)
    if args.prepare_only or (not state["publisher"] and not args.install_publisher):
        print(f"Prepared {revision}. First publisher installation requires approval." if not state["publisher"]
              else f"Prepared {revision}.")
        return
    with timing.step("verify viewing connection before activation"):
        try:
            verify_page(state["files"], state["active"])
        except (ValueError, OSError):
            if state["publisher"] or not args.install_publisher:
                raise
            verify_page(state["files"], state["active"], port=4176)
            install_view(bundle)
            verify_page(state["files"], state["active"])
    if fast:
        with timing.step("activate dashboard"):
            ci_path = folder / "dashboard-ci.json"
            write_atomic(ci_path, dict(commit=revision, run=run["databaseId"], attempt=run["attempt"],
                                       digest=digest, verified_at=time.time()))
            host.upload(ci_path, directory)
            timing.data["switch"] = host.dashboard("activate", state, name, revision, sha256(ci_path))
        with timing.step("verify dashboard through viewing connection"):
            try:
                verify_page(files, revision)
            except Exception:
                after = host.state()
                if after["release"] == state["release"] and after["active"] == revision:
                    host.dashboard("rollback", after, name, state["active"], "-")
                    verify_page(state["files"], state["active"])
                raise
    else:
        with timing.step("wait for safe full switch"):
            while True:
                window = host.ps(host.identity + f"""
if ($r -ine {ps_quote(state["release"])}) {{throw 'Running release changed'}}
& $py -B {ps_quote(directory + '/release-host.py')} window --stage {ps_quote(directory)}
if ($LASTEXITCODE) {{exit $LASTEXITCODE}}
""")
                sample = json.loads(window.splitlines()[-1])
                if sample["ready"]:
                    break
                print(sample["reason"], flush=True)
                time.sleep(45)
        with timing.step("guarded full activation"):
            remote(bundle, profile_path, True, prepared=True)
        with timing.step("verify full release through viewing connection"):
            after = host.state()
            if after["backend"] != revision or after["active"] is not None or not after["complete"]:
                raise ValueError("Unexpected serving identity after full switch")
            verify_page(after["files"], None)
    timing.verified()
    print(f"Refresh peteamdpc. Verified {revision}. Timing: {timing.path}")
