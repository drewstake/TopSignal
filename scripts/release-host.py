"""Host-side release preparation, integrity verification and read-only broker gates."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler

from release_lib import extract_bundle, is_link, safe_name, sha256, verify_files, verify_bundle

urlopen = build_opener(ProxyHandler({})).open


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def inventory(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ValueError(f"Release directory is missing: {root}")
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {".venv", "__pycache__"}]
        for name in dirs + files:
            path = Path(directory) / name
            if is_link(path):
                raise ValueError(f"Linked release content: {path}")
        for name in files:
            path = Path(directory) / name
            if path.suffix != ".pyc":
                result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def prepare(stage: Path, config: dict, candidate: Path, previous: Path) -> None:
    manifest = verify_bundle(stage, config["manifest_sha256"])
    if manifest["git_commit"] != config["git_commit"]:
        raise ValueError("Unexpected commit in bundle")
    if candidate.exists():
        # Resuming only a completely prepared, identical candidate is allowed.
        verify(stage, candidate, previous)
        return
    baseline = inventory(previous)
    if shutil.disk_usage(candidate.parent).free < 2 * 1024**3:
        raise ValueError("Release staging requires at least 2 GiB free; no data is deleted")
    def protect_new_directory(path):
        # Before any private configuration is copied, keep the staging candidate
        # accessible only to SYSTEM and Administrators. Service RX is added later.
        subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r",
                        "*S-1-5-18:(OI)(CI)(F)", "*S-1-5-32-544:(OI)(CI)(F)"], check=True)

    extract_bundle(stage, candidate, config["manifest_sha256"], initialize=protect_new_directory)
    check_companions(config, candidate)
    environment = previous / "backend/.env"
    if not environment.is_file():
        raise ValueError("Previous private environment missing")
    for folder in ("backend", "frontend"):
        for private in (previous / folder).glob(".env*"):
            if private.name != ".env.example" and private.is_file():
                shutil.copyfile(private, candidate / folder / private.name)
    if (previous / "certs").exists():
        for name, checksum in inventory(previous / "certs").items():
            destination = candidate / "certs" / name
            if destination.exists():
                if sha256(destination) != checksum:
                    raise ValueError("Certificate drift requires a separate reviewed configuration change")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(previous / "certs" / name, destination)
    # Reuse dependencies only when requirements match, allowing LF/CRLF conversion.
    # Copy into a release-owned directory: never harden through a shared junction.
    unchanged = ((previous / "backend/requirements.txt").read_bytes().replace(b"\r\n", b"\n")
                 == (candidate / "backend/requirements.txt").read_bytes().replace(b"\r\n", b"\n"))
    target_venv = candidate / "backend/.venv"
    if unchanged:
        source_venv = (previous / "backend/.venv").resolve()
        for directory, dirs, files in os.walk(source_venv):
            for name in dirs + files:
                path = Path(directory) / name
                if is_link(path):
                    raise ValueError("Nested links in Python environment are not supported")
        shutil.copytree(source_venv, target_venv, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        subprocess.run([sys.executable, "-m", "venv", str(target_venv)], check=True)
        subprocess.run([str(target_venv / "Scripts/python.exe"), "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(candidate / "backend/requirements.txt")], check=True)
    python = str(target_venv / "Scripts/python.exe")
    subprocess.run([python, "-m", "pip", "check"], check=True)
    prefix = subprocess.check_output([python, "-c", "import sys; print(sys.prefix)"], text=True).strip()
    if Path(prefix).resolve() != target_venv.resolve():
        raise ValueError("Candidate Python resolves outside its own environment")
    if unchanged and inventory(target_venv) != inventory(source_venv):
        raise ValueError("Copied Python environment differs from the immutable source")
    spec = importlib.util.spec_from_file_location("preserve_assets", candidate / "scripts/preserve-frontend-assets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.preserve_assets(candidate / "frontend/dist", previous / "frontend/dist")
    verify_files(previous, baseline)
    write_json(stage / "prepared.json", {"manifest_sha256": config["manifest_sha256"],
               "previous": str(previous), "candidate": str(candidate), "previous_files": baseline,
               "candidate_files": inventory(candidate), "dependencies_reused": unchanged,
               "config_sha256": sha256(stage / "host.json"),
               "dependency_files": inventory(target_venv),
               "previous_dependency_files": inventory((previous / "backend/.venv").resolve())})


def verify(stage: Path, candidate: Path, previous: Path) -> None:
    config = json.loads((stage / "host.json").read_text(encoding="utf-8"))
    receipt = json.loads((stage / "prepared.json").read_text(encoding="utf-8"))
    if (receipt["manifest_sha256"] != config["manifest_sha256"]
            or receipt["config_sha256"] != sha256(stage / "host.json")
            or Path(receipt["candidate"]).resolve() != candidate.resolve()
            or Path(receipt["previous"]).resolve() != previous.resolve()):
        raise ValueError("Prepared receipt belongs to another candidate")
    verify_bundle(stage, config["manifest_sha256"])
    check_companions(config, candidate)
    for root, key in ((previous, "previous_files"), (candidate, "candidate_files")):
        if inventory(root) != receipt[key]:
            raise ValueError(f"Release inventory changed: {root}")
    for root, key in (((previous / "backend/.venv").resolve(), "previous_dependency_files"),
                      (candidate / "backend/.venv", "dependency_files")):
        if inventory(root) != receipt[key]:
            raise ValueError("Installed dependency files changed")


def check_companions(config: dict, candidate: Path) -> None:
    # Independent scheduled helpers must match the reviewed candidate. Updating
    # a running recorder/backup tool is a separate lifecycle operation, never an
    # unannounced file overwrite during an application release.
    for source, target in config["companion_files"].items():
        safe_name(source)
        if sha256(candidate / source) != sha256(Path(target)):
            raise ValueError(f"Independent helper differs: {source}; complete its reviewed rollout before application activation")


def expected_restart_disarm(observed: dict, baseline: dict) -> bool:
    """Accept only the worker's documented fail-closed restart transition."""
    before = baseline.get("configs", {})
    after = observed.get("configs", {})
    if not before or set(before) != set(after) or set(baseline["runs"]) != set(observed["runs"]):
        return False
    for key, old in before.items():
        new = after[key]
        old_run, new_run = baseline["runs"].get(key), observed["runs"].get(key)
        if old == new and old_run == new_run:
            continue
        if not old_run or not new_run or not (
            old_run["id"] == new_run["id"] and old_run["status"] == "running"
            and old_run.get("dry_run") is False and new_run.get("dry_run") is False
            and new_run["status"] == "stopped"
            and new_run.get("stop_reason") == "worker_restart_requires_rearm"
            and old.get("enabled") == "True" and new.get("enabled") == "False"
        ):
            return False
        if {k: v for k, v in old.items() if k not in {"enabled", "updated_at"}} != {
            k: v for k, v in new.items() if k not in {"enabled", "updated_at"}
        }:
            return False
    return True


def check_gate_snapshot(observed: dict, baseline: dict | None, require_flat: bool,
                        allow_restart_disarm: bool = False) -> None:
    if baseline and (observed["config_sha256"] != baseline["config_sha256"] or observed["runs"] != baseline["runs"]):
        if not allow_restart_disarm or not expected_restart_disarm(observed, baseline):
            raise ValueError("Trading configuration or runner identity/status changed")
    if observed["unresolved_attempts"]:
        raise ValueError("Pending/unknown order attempts block release")
    for account in observed["accounts"]:
        if account["simulated"] is not True or account["can_trade"] is not True:
            raise ValueError("Account eligibility is not verified")
        if require_flat and (account["positions"] or account["orders"]):
            raise ValueError("Broker exposure is active; wait without interrupting management")


def gate(stage: Path, config: dict, root: Path, record: bool, require_flat: bool,
         allow_restart_disarm: bool = False) -> dict:
    # Application imports happen only inside the host gate, never at tool import time.
    sys.path.insert(0, str(root / "backend"))
    from dotenv import load_dotenv
    load_dotenv(root / "backend/.env", override=True)
    os.environ["TOPSIGNAL_DB_SCHEMA_INIT"] = "skip"
    os.environ["TOPSIGNAL_ENV"] = "production"
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.models import BotConfig, BotRun, BotOrderAttempt
    from app.main import _projectx_client_for_user, _validate_runtime_security_configuration
    _validate_runtime_security_configuration()
    ids = {int(n) for n in config["bot_config_ids"]}
    if not ids:
        raise ValueError("Explicit bot_config_ids are required")
    clients = {}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        active = {r.id for r in db.query(BotConfig).filter(BotConfig.enabled.is_(True))}
        active |= {r.bot_config_id for r in db.query(BotRun).filter(BotRun.status == "running")}
        if not active.issubset(ids):
            raise ValueError("Configured release scope omits enabled/running bots")
        configs = db.query(BotConfig).filter(BotConfig.id.in_(ids)).order_by(BotConfig.id).all()
        if {r.id for r in configs} != ids or any(r.provider != "projectx" for r in configs):
            raise ValueError("Unknown bot or unsupported provider in release scope")
        rows = [{column.name: str(getattr(row, column.name)) for column in BotConfig.__table__.columns} for row in configs]
        runs = {}
        for row in configs:
            run = db.query(BotRun).filter(BotRun.bot_config_id == row.id).order_by(BotRun.id.desc()).first()
            runs[str(row.id)] = {"id": run.id, "status": run.status, "dry_run": run.dry_run,
                                 "stop_reason": run.stop_reason} if run else None
            key = (str(row.user_id), int(row.account_id))
            if key not in clients:
                clients[key] = _projectx_client_for_user(db, user_id=str(row.user_id))
        unresolved = db.query(BotOrderAttempt).filter(BotOrderAttempt.bot_config_id.in_(ids),
                           BotOrderAttempt.status.in_(["pending", "submission_unknown"])).count()
        database_bytes = db.execute(text("select pg_database_size(current_database())")).scalar()
        db.rollback()
    observed = {"config_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                "configs": {row["id"]: row for row in rows},
                "runs": runs, "unresolved_attempts": unresolved, "database_bytes": database_bytes, "accounts": []}
    allowed = {"Auth/loginKey", "Account/search", "Position/searchOpen", "Order/searchOpen"}
    for (_, account_id), client in clients.items():
        original = client._request_once

        def readonly(method, path, _original=original, **kwargs):
            route = path.lstrip("/").removeprefix("api/")
            if route not in allowed:
                raise ValueError("Release gate denied a broker mutation")
            return _original(method, path, **kwargs)

        client._request_once = readonly
        accounts = client.list_accounts(only_active_accounts=False)
        account = next((a for a in accounts if int(a.get("id", a.get("account_id", 0))) == account_id), None)
        if not account:
            raise ValueError("Broker account missing")
        observed["accounts"].append({"account_id": account_id, "simulated": account.get("simulated"),
                    "can_trade": account.get("can_trade"),
                    "positions": len(client.search_open_positions(account_id=account_id)),
                    "orders": len(client.search_open_orders(account_id=account_id))})
    baseline_path = stage / "broker-before.json"
    baseline = None if record else json.loads(baseline_path.read_text(encoding="utf-8"))
    check_gate_snapshot(observed, baseline, require_flat, allow_restart_disarm)
    observed["restart_disarmed"] = bool(baseline and observed["runs"] != baseline["runs"])
    if record:
        if baseline_path.exists():
            raise ValueError("Broker baseline already exists; cannot overwrite activation evidence")
        write_json(baseline_path, observed)
    write_json(stage / "broker-latest.json", observed)
    return observed


def health(config: dict, root: Path) -> dict:
    result = {}
    for path in ("http://127.0.0.1:4173/", "http://127.0.0.1:8000/health", "http://127.0.0.1:8000/ready",
                 "http://127.0.0.1:8000/health/worker?require_enabled=true"):
        with urlopen(path, timeout=5) as response:
            body = response.read()
            if response.status != 200:
                raise ValueError("Release health probe failed")
            if path.endswith(":4173/") and body != (root / "frontend/dist/index.html").read_bytes():
                raise ValueError("Served frontend does not match candidate")
            result[path] = response.status
    return result


def recording(account_ids: list[int]) -> dict:
    origin = "http://127.0.0.1:4173"
    base = "http://127.0.0.1:8000"
    headers = {"Origin": origin, "X-TopSignal-Local-Access": "device", "Content-Type": "application/json"}
    with urlopen(Request(base + "/auth/local/device", data=b"{}", headers=headers), timeout=15) as response:
        headers["Authorization"] = "Bearer " + json.load(response)["access_token"]
    readings = {}
    for account in account_ids:
        with urlopen(Request(base + f"/api/accounts/{int(account)}/topbot/statistics", headers=headers), timeout=15) as response:
            data = json.load(response)
        evidence = (data.get("depth") or {}).get("evidence") or {}
        status = evidence.get("recording") or {}
        readings[str(account)] = {"capture_id": evidence.get("capture_id"),
                                 **{n: status.get(n) for n in ("state", "durable_events", "dropped_events")}}
    return readings


def check_recording(before: dict, after: dict) -> None:
    if not before or set(before) != set(after):
        raise ValueError("Recording coverage changed or is unavailable")
    for account, first in before.items():
        last = after[account]
        if first["state"] != "recording" or last["state"] != "recording":
            raise ValueError("Recording has not recovered")
        if not first["capture_id"] or first["capture_id"] != last["capture_id"]:
            raise ValueError("Recording restarted during verification")
        if not isinstance(first["dropped_events"], int) or first["dropped_events"] != 0 or last["dropped_events"] != 0:
            raise ValueError("Recording reports dropped events")
        if not isinstance(first["durable_events"], int) or not isinstance(last["durable_events"], int) or last["durable_events"] < first["durable_events"]:
            raise ValueError("Recording durability regressed")


def monitor_recording(stage: Path) -> None:
    baseline = json.loads((stage / "broker-before.json").read_text(encoding="utf-8"))
    accounts = [a["account_id"] for a in baseline["accounts"]]
    samples = [recording(accounts)]
    for _ in range(2):
        time.sleep(10)
        samples.append(recording(accounts))
        check_recording(samples[0], samples[-1])
    write_json(stage / "recording-verification.json", {"samples": samples, "verified": True})


def migration_check(root: Path) -> None:
    from dotenv import load_dotenv
    load_dotenv(root / "backend/.env", override=True)
    os.environ["TOPSIGNAL_ENV"] = "production"
    os.environ["TOPSIGNAL_DB_SCHEMA_INIT"] = "skip"
    subprocess.run([sys.executable, "-B", str(root / "backend/tools/migrate_db.py"), "--check"],
                   cwd=root, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "gate", "health", "recording", "migration-check"])
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--previous", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--allow-exposure", action="store_true")
    parser.add_argument("--allow-restart-disarm", action="store_true")
    args = parser.parse_args()
    stage = args.stage.resolve()
    config = json.loads((stage / "host.json").read_text(encoding="utf-8"))
    releases = Path(config["releases_root"]).resolve()
    previous = Path(config["previous_release"]).resolve()
    candidate = releases / config["release_id"]
    if (candidate.parent != releases or not previous.is_relative_to(releases) or candidate == previous
            or candidate.exists() and candidate.resolve() != candidate):
        raise ValueError("Release paths must be distinct and inside releases_root")
    root = previous if args.previous else candidate
    if args.command == "prepare":
        prepare(stage, config, candidate, previous)
    elif args.command == "verify":
        verify(stage, candidate, previous)
    elif args.command == "gate":
        gate(stage, config, root, args.record, not args.allow_exposure, args.allow_restart_disarm)
    elif args.command == "health":
        print(json.dumps(health(config, root)))
    elif args.command == "migration-check":
        migration_check(root)
    else:
        monitor_recording(stage)


if __name__ == "__main__":
    main()
