"""Compare serial/parallel FULL suites on identical inputs; reject coverage drift.

Run with backend/.venv/Scripts/python scripts/benchmark-backend-tests.py.
Reports contain test identities/outcomes, durations and source fingerprints,
never production configuration. Runs are sequential to avoid CPU contention.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(root: Path) -> str:
    files = [p for folder in ("app", "tests", "tools")
             for p in (root / "backend" / folder).rglob("*.py")]
    files += list((root / "backend").glob("requirements*.txt"))
    files += [root / "backend/pytest.ini"]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def outcomes(path: Path) -> Counter:
    result = Counter()
    for case in ET.parse(path).iter("testcase"):
        status = next((tag for tag in ("failure", "error", "skipped")
                       if case.find(tag) is not None), "passed")
        result[(case.get("classname", ""), case.get("name", ""), status)] += 1
    if not result:
        raise ValueError("JUnit report contains no tests")
    return result


def compare(baseline: Counter, candidate: Counter) -> None:
    if baseline != candidate:
        raise ValueError(f"Test identity/outcome drift: missing={list((baseline - candidate).items())[:10]}, "
                         f"extra={list((candidate - baseline).items())[:10]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, nargs="+", default=[2, 4])
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/release-speed" / time.strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if args.repeat < 1 or any(n < 1 for n in args.workers):
        parser.error("repeat and workers must be positive")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, DATABASE_URL="sqlite+pysqlite:///:memory:", AUTH_REQUIRED="true",
               TOPSIGNAL_DB_SCHEMA_INIT="skip", PYTHON_DOTENV_DISABLED="1",
               TOPSIGNAL_LIVE_EXECUTION_ENABLED="false", TOPSIGNAL_BOT_WORKER_ENABLED="false",
               TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION="false")
    # PostgreSQL integration stays in its own unchanged CI lane.
    env.pop("TOPSIGNAL_TEST_POSTGRES_URL", None)
    env.pop("PYTEST_ADDOPTS", None)
    before = fingerprint(ROOT)
    report = {"source_sha256": before, "platform": platform.platform(),
              "python": sys.version, "cpu_count": os.cpu_count(), "runs": [], "verified": False}
    expected = None
    try:
        for repeat in range(args.repeat):
            # Reverse on alternating rounds to reduce warm-cache/order bias.
            modes = [0, *args.workers]
            if repeat % 2:
                modes.reverse()
            for workers in modes:
                name = f"workers-{workers}-round-{repeat + 1}"
                junit = args.output / f"{name}.xml"
                command = [sys.executable, "-m", "pytest", "-q", "-n", str(workers),
                           "--dist=loadfile", "-o", "faulthandler_timeout=60", "--durations=30",
                           f"--junitxml={junit}"]
                print(f"Running full suite: {name}", flush=True)
                started = time.perf_counter()
                with (args.output / f"{name}.log").open("w", encoding="utf-8") as log:
                    run = subprocess.run(command, cwd=ROOT / "backend", env=env,
                                         stdout=log, stderr=subprocess.STDOUT)
                row = {"workers": workers, "round": repeat + 1,
                       "seconds": round(time.perf_counter() - started, 3), "exit_code": run.returncode}
                report["runs"].append(row)
                print(json.dumps(row), flush=True)
                if run.returncode:
                    raise RuntimeError(f"Suite failed: see {name}.log")
                if fingerprint(ROOT) != before:
                    raise RuntimeError("Source changed during benchmark; comparison is invalid")
                observed = outcomes(junit)
                log_text = (args.output / f"{name}.log").read_text(encoding="utf-8")
                subtests = re.search(r"(\d+) subtests passed", log_text)
                row["passed_subtests"] = int(subtests.group(1)) if subtests else 0
                if any(r.get("passed_subtests") != row["passed_subtests"] for r in report["runs"][:-1]):
                    raise RuntimeError("Passed subtest count changed between benchmark modes")
                row["outcomes"] = dict(Counter({status: sum(count for (_, _, s), count in observed.items() if s == status)
                                                for status in {key[2] for key in observed}}))
                if expected is None:
                    expected = observed
                else:
                    compare(expected, observed)
        medians = {str(n): statistics.median(r["seconds"] for r in report["runs"] if r["workers"] == n)
                   for n in [0, *args.workers]}
        report.update(verified=True, median_seconds=medians,
                      speedup={n: round(medians["0"] / seconds, 3) for n, seconds in medians.items()})
    finally:
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
