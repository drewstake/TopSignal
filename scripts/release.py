"""Repeatable releases: validate, package, stage, then deploy the exact tested commit.

No command commits or pushes source. Deploy requires successful CI for the
bundle's exact commit and calls the host's guarded activation/rollback script.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

from release_lib import is_link, sha256, source_allowed, verify_bundle

ROOT = Path(__file__).resolve().parents[1]
NPM = shutil.which("npm.cmd" if os.name == "nt" else "npm") or "npm"


def capture(command: list[str], cwd: Path = ROOT) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def source_files() -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT)
    return sorted(set(n.decode("utf-8") for n in output.split(b"\0") if n))


def input_fingerprint() -> str:
    digest = hashlib.sha256()
    files = source_files()
    # Vite build inputs are private and must be hashed, never packaged as source.
    files += [p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend").glob(".env*") if p.is_file()]
    for name in sorted(set(files)):
        path = ROOT / name
        if not path.exists():
            digest.update(name.encode() + b"\0deleted\0")
            continue
        digest.update(name.encode() + b"\0" + sha256(path).encode())
    for name in sorted(n for n in os.environ if n.startswith("VITE_")):
        digest.update(name.encode() + b"\0" + os.environ[name].encode())
    return digest.hexdigest()


def check_output(output: Path) -> None:
    resolved = output.resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        relative = (resolved / "release-output-probe").relative_to(ROOT.resolve()).as_posix()
        ignored = subprocess.run(["git", "check-ignore", "--quiet", "--no-index", relative], cwd=ROOT)
        if ignored.returncode != 0:
            raise ValueError("Output inside the checkout must be Git-ignored (use tmp/releases/...) or outside the repository")


def run_logged(name: str, command: list[str], output: Path, env: dict, cwd: Path = ROOT) -> dict:
    print(f"{name}...", flush=True)
    started = time.perf_counter()
    log_path = output / f"{name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    row = {"name": name, "seconds": round(time.perf_counter() - started, 3), "exit_code": result.returncode}
    print(json.dumps(row), flush=True)
    if result.returncode:
        raise RuntimeError(f"{name} failed; see {log_path}")
    return row


def validate(output: Path, workers: int) -> dict:
    check_output(output)
    output.mkdir(parents=True, exist_ok=False)
    before = input_fingerprint()
    env = dict(os.environ, DATABASE_URL="sqlite+pysqlite:///:memory:", AUTH_REQUIRED="true",
               TOPSIGNAL_DB_SCHEMA_INIT="skip", PYTHON_DOTENV_DISABLED="1",
               TOPSIGNAL_LIVE_EXECUTION_ENABLED="false", TOPSIGNAL_BOT_WORKER_ENABLED="false",
               TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("TOPSIGNAL_TEST_POSTGRES_URL", None)
    commands = [
        ("backend", [sys.executable, "-m", "pytest", "-q", "-n", str(workers), "--dist=loadfile",
                     "-o", "faulthandler_timeout=60", f"--junitxml={output / 'backend.xml'}"], ROOT / "backend"),
        ("frontend", [NPM, "--prefix", "frontend", "test"], ROOT),
        ("lint", [NPM, "--prefix", "frontend", "run", "lint", "--", "--max-warnings=0"], ROOT),
        ("scripts", [NPM, "run", "test:dev-scripts"], ROOT),
        ("root-audit", [NPM, "audit"], ROOT),
        ("frontend-audit", ["node", "scripts/audit-frontend.cjs"], ROOT),
    ]
    rows = [run_logged(name, command, output, env, cwd) for name, command, cwd in commands]
    # Build exactly the loopback production configuration; no second build at packaging.
    build_env = dict(os.environ, VITE_API_BASE_URL="http://127.0.0.1:8000", VITE_DEMO_MODE="false")
    rows.append(run_logged("production-build", [NPM, "--prefix", "frontend", "run", "build"], output, build_env))
    if input_fingerprint() != before:
        raise RuntimeError("Source/build configuration changed during validation; rerun on stable inputs")
    dist = {p.relative_to(ROOT).as_posix(): sha256(p) for p in (ROOT / "frontend/dist").rglob("*") if p.is_file()}
    result = {"version": 1, "validated": True, "input_sha256": before,
              "git_commit": capture(["git", "rev-parse", "HEAD"]), "steps": rows, "dist_sha256": dist}
    (output / "validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def package(output: Path, receipt: Path | None, workers: int) -> None:
    check_output(output)
    if capture(["git", "status", "--porcelain"]):
        raise RuntimeError("Commit the complete reviewed source before packaging; working tree is dirty")
    if receipt:
        validation = json.loads(receipt.read_text(encoding="utf-8"))
    else:
        validation = validate(output.with_name(output.name + "-validation"), workers)
    expected_steps = {"backend", "frontend", "lint", "scripts", "root-audit", "frontend-audit", "production-build"}
    if (not validation.get("validated") or validation.get("input_sha256") != input_fingerprint()
            or validation.get("git_commit") != capture(["git", "rev-parse", "HEAD"])
            or len(validation.get("steps", [])) != len(expected_steps)
            or {s["name"] for s in validation.get("steps", []) if s["exit_code"] == 0} != expected_steps):
        raise RuntimeError("Validation receipt does not cover these exact source/build inputs")
    dist = validation["dist_sha256"]
    actual_dist = {p.relative_to(ROOT).as_posix(): sha256(p) for p in (ROOT / "frontend/dist").rglob("*") if p.is_file()}
    if dist != actual_dist:
        raise RuntimeError("Built assets changed after validation")
    output.mkdir(parents=True, exist_ok=False)
    source = {}
    with zipfile.ZipFile(output / "source.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in source_files():
            if name.startswith("frontend/dist/"):
                continue
            if not source_allowed(name):
                raise RuntimeError(f"Private/runtime file is tracked: {name}")
            path = ROOT / name
            if is_link(path) or not path.resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError(f"Linked source file: {name}")
            source[name] = sha256(path)
            archive.write(path, name)
    with zipfile.ZipFile(output / "frontend.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in dist:
            archive.write(ROOT / name, name)
    if input_fingerprint() != validation["input_sha256"] or capture(["git", "status", "--porcelain"]):
        raise RuntimeError("Source changed during packaging; bundle is incomplete")
    manifest = {"version": 1, "validated": True, "git_commit": validation["git_commit"],
                "source_sha256": source, "dist_sha256": dist,
                "archives": {n: sha256(output / n) for n in ("source.zip", "frontend.zip")},
                "validation": validation}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify_bundle(output)
    print(f"Verified bundle: {output}")


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def check_ci(commit: str, repository: str) -> dict:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    runs = json.loads(capture(["gh", "run", "list", "--repo", repository, "--commit", commit,
                               "--workflow", "ci.yml", "--limit", "20", "--json", "databaseId,headSha,status,conclusion"]))
    # Use the newest run, never an older success hiding a newer failure.
    if not runs or runs[0]["headSha"] != commit or runs[0]["conclusion"] != "success":
        raise RuntimeError("Latest CI run for this exact commit has not succeeded; candidate may be staged, not activated")
    run = json.loads(capture(["gh", "run", "view", str(runs[0]["databaseId"]), "--repo", repository,
                              "--json", "headSha,conclusion,jobs,url"]))
    required = {"Database migrations", "Backend tests", "Frontend tests", "Frontend quality", "Windows compatibility", "verify"}
    if run["headSha"] != commit or run["conclusion"] != "success" or not required.issubset(
            {j["name"] for j in run["jobs"] if j["conclusion"] == "success"}):
        raise RuntimeError("Required CI jobs missing or unsuccessful")
    return {"commit": commit, "run_id": runs[0]["databaseId"], "url": run["url"], "verified_at": time.time()}


def remote(bundle: Path, config_path: Path, activate: bool) -> None:
    manifest = verify_bundle(bundle)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    required = {"host", "key", "staging_root", "releases_root", "previous_release", "runtime_root", "release_id", "repository", "credential_script", "bot_config_ids", "companion_files"}
    if not required.issubset(config):
        raise ValueError(f"Missing host configuration keys: {sorted(required - config.keys())}")
    if not re.fullmatch(r"[\w.-]+(?:@[\w.-]+)?", config["host"]) or not re.fullmatch(r"[\w.-]+", config["release_id"]):
        raise ValueError("Unsafe host or release ID")
    # SCP's Windows remote destination is deliberately restricted to avoid shell parsing.
    if not re.fullmatch(r"[A-Za-z]:/[\w./-]+", config["staging_root"]) or ".." in config["staging_root"].split("/"):
        raise ValueError("staging_root must be an absolute Windows path without spaces or shell characters")
    stage = config["staging_root"].rstrip("/") + "/" + config["release_id"]
    ssh = ["ssh", "-i", str(Path(config["key"]).expanduser()), "-o", "IdentitiesOnly=yes",
           "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", config["host"]]

    def invoke(body: str) -> None:
        encoded = base64.b64encode(("$ErrorActionPreference='Stop'; " + body).encode("utf-16le")).decode()
        subprocess.run([*ssh, "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], check=True)

    invoke(f"""
$path = {ps_quote(stage)}
if (Test-Path -LiteralPath (Join-Path $path 'manifest.json')) {{
    if ((Get-FileHash -LiteralPath (Join-Path $path 'manifest.json')).Hash.ToLowerInvariant() -ne {ps_quote(sha256(bundle / 'manifest.json'))}) {{ throw 'Staging ID already belongs to another bundle' }}
}}
New-Item -ItemType Directory -Force -Path $path | Out-Null
$item = Get-Item -LiteralPath $path
while ($null -ne $item) {{
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{ throw 'Linked staging path' }}
    $item = $item.Parent
}}
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.Access)) {{ [void]$acl.RemoveAccessRuleSpecific($rule) }}
foreach ($sid in @('S-1-5-18','S-1-5-32-544')) {{
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(([Security.Principal.SecurityIdentifier]$sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
}}
Set-Acl -LiteralPath $path -AclObject $acl
""")
    # Bootstrap scripts must be the bundle's tested versions, not a later working tree.
    with zipfile.ZipFile(bundle / "source.zip") as source:
        bootstrap = bundle / "bootstrap"
        bootstrap.mkdir(exist_ok=True)
        for name in ("release-host.ps1", "release-host.py", "release_lib.py"):
            (bootstrap / name).write_bytes(source.read("scripts/" + name))
    host_config = dict(config, manifest_sha256=sha256(bundle / "manifest.json"), git_commit=manifest["git_commit"])
    for private in ("key", "host", "repository"):
        host_config.pop(private)
    (bootstrap / "host.json").write_text(json.dumps(host_config), encoding="utf-8")
    subprocess.run(["scp", *ssh[1:-1], *[str(bundle / n) for n in ("source.zip", "frontend.zip", "manifest.json")],
                    *[str(bootstrap / n) for n in ("release-host.ps1", "release-host.py", "release_lib.py", "host.json")],
                    config["host"] + ":" + stage + "/"], check=True)
    invoke(f"& {ps_quote(stage + '/release-host.ps1')} -Phase Prepare -Stage {ps_quote(stage)}; if (-not $?) {{ exit 1 }}")
    if activate:
        ci = check_ci(manifest["git_commit"], config["repository"])
        print(f"Exact commit CI passed: {ci['url']}", flush=True)
        ci_path = bootstrap / "ci.json"
        ci_path.write_text(json.dumps(ci), encoding="utf-8")
        subprocess.run(["scp", *ssh[1:-1], str(ci_path), config["host"] + ":" + stage + "/"], check=True)
        invoke(f"& {ps_quote(stage + '/release-host.ps1')} -Phase Activate -Stage {ps_quote(stage)} -CIReceiptHash {ps_quote(sha256(ci_path))}; if (-not $?) {{ exit 1 }}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "package", "verify", "stage", "deploy"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--host-config", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.command in ("validate", "package"):
        if not args.output:
            parser.error("--output is required (a new directory)")
        if args.command == "validate":
            validate(args.output.resolve(), args.workers)
        else:
            package(args.output.resolve(), args.receipt, args.workers)
    else:
        if not args.bundle:
            parser.error("--bundle is required")
        if args.command == "verify":
            print(json.dumps({"commit": verify_bundle(args.bundle)["git_commit"], "verified": True}))
        else:
            if not args.host_config:
                parser.error("--host-config is required")
            remote(args.bundle.resolve(), args.host_config, args.command == "deploy")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
