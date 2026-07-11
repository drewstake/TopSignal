#!/usr/bin/env python3
"""Build TopSignal's local Databento Parquet and mmap cache.

Examples (run from the repository root):

    backend\.venv\Scripts\python backend\tools\build_databento_cache.py --downloads
    backend\.venv\Scripts\python backend\tools\build_databento_cache.py --timeframe 5m
    backend\.venv\Scripts\python backend\tools\build_databento_cache.py archives... --json

This tool imports only the filesystem-backed Databento cache service. It does
not initialize or access SQLAlchemy, Supabase, or any other database.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


KNOWN_JOB_IDS = (
    "YBPWQQUDKJ",
    "GKBCMKGKET",
    "8555B5YDYR",
    "35JAY9SXQ8",
    "BAWR6AXHE9",
    "M4VSXHQMJ3",
    "3B9J6LDNDK",
    "6REVRBVFS8",
    "4QRXRM4KDS",
    "U9LKHXCTKS",
)


@dataclass(frozen=True)
class CacheApi:
    build: Callable[..., Any]
    default_timeframes: Sequence[tuple[str, int]]
    parse_timeframe: Callable[[str], tuple[str, int]]
    timeframe_key: Callable[[str, int], str]
    error_type: type[Exception]


def _load_cache_api() -> CacheApi:
    from app.services.databento_cache import (
        DEFAULT_TIMEFRAMES,
        DatabentoCacheError,
        build_databento_cache,
        parse_timeframe,
        timeframe_key,
    )

    return CacheApi(
        build=build_databento_cache,
        default_timeframes=DEFAULT_TIMEFRAMES,
        parse_timeframe=parse_timeframe,
        timeframe_key=timeframe_key,
        error_type=DatabentoCacheError,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert Databento batch ZIPs into canonical partitioned Parquet "
            "and memory-mapped replay arrays."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "archives",
        nargs="*",
        metavar="ARCHIVE",
        help="Databento batch ZIPs; definition and ohlcv-1m are required per root",
    )
    parser.add_argument(
        "--downloads",
        nargs="?",
        const=str(Path.home() / "Downloads"),
        metavar="DIRECTORY",
        help=(
            "use the ten known TopSignal jobs from DIRECTORY; when DIRECTORY "
            "is omitted, use the current user's Downloads folder"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help=(
            "cache destination; otherwise TOPSIGNAL_DATABENTO_CACHE_DIR or "
            "backend/storage/databento"
        ),
    )
    parser.add_argument(
        "--timeframe",
        "--timeframes",
        action="append",
        default=[],
        metavar="TF[,TF...]",
        help=(
            "materialized timeframe(s), for example 1m,5m,15m,1h,4h,1d; "
            "repeat the option or provide a comma-separated list"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild the matching immutable cache version from source archives",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    return parser


def _archives_from_downloads(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f"downloads directory does not exist: {directory}")
    archives: list[Path] = []
    missing: list[str] = []
    ambiguous: list[str] = []
    for job_id in KNOWN_JOB_IDS:
        matches = sorted(directory.glob(f"GLBX-*-{job_id}.zip"))
        if not matches:
            missing.append(job_id)
        elif len(matches) > 1:
            ambiguous.append(job_id)
        else:
            archives.append(matches[0].resolve())
    if missing:
        raise ValueError(
            "missing known Databento job ZIP(s) in "
            f"{directory}: {', '.join(missing)}"
        )
    if ambiguous:
        raise ValueError(
            "multiple ZIPs matched known Databento job(s) in "
            f"{directory}: {', '.join(ambiguous)}"
        )
    return archives


def _resolve_archives(args: argparse.Namespace) -> list[Path]:
    if args.downloads is not None and args.archives:
        raise ValueError("use positional archives or --downloads, not both")
    if args.downloads is not None:
        return _archives_from_downloads(Path(args.downloads).expanduser().resolve())
    if not args.archives:
        raise ValueError("provide archive ZIPs or use --downloads")
    archives = [Path(value).expanduser().resolve() for value in args.archives]
    duplicates = sorted(
        {str(path) for path in archives if archives.count(path) > 1}
    )
    if duplicates:
        raise ValueError(f"duplicate archive path(s): {', '.join(duplicates)}")
    return archives


def _resolve_timeframes(args: argparse.Namespace, api: CacheApi) -> list[str]:
    if not args.timeframe:
        return [
            api.timeframe_key(unit, number)
            for unit, number in api.default_timeframes
        ]
    values: list[str] = []
    for group in args.timeframe:
        for raw in str(group).split(","):
            value = raw.strip().lower()
            if not value:
                raise ValueError("timeframe lists cannot contain an empty value")
            unit, number = api.parse_timeframe(value)
            key = api.timeframe_key(unit, number)
            if key not in values:
                values.append(key)
    return values


def _run(args: argparse.Namespace) -> dict[str, Any]:
    api = _load_cache_api()
    archives = _resolve_archives(args)
    timeframes = _resolve_timeframes(args, api)
    started = time.perf_counter()
    result = api.build(
        archives,
        cache_root=args.cache_dir,
        timeframes=timeframes,
        force=args.force,
    )
    elapsed = time.perf_counter() - started
    return {
        "ok": True,
        "elapsed_seconds": round(elapsed, 6),
        "requested_archives": [str(path) for path in archives],
        "requested_timeframes": timeframes,
        "result": asdict(result),
    }


def _print_text(report: dict[str, Any]) -> None:
    result = report["result"]
    disposition = "reused" if result["reused"] else "built"
    print(f"Databento cache {disposition} in {report['elapsed_seconds']:.3f}s")
    print(f"  cache_root: {result['cache_root']}")
    print(f"  version_dir: {result['version_dir']}")
    print(f"  source_fingerprint: {result['source_fingerprint']}")
    print(f"  roots: {', '.join(result['roots'])}")
    print(f"  timeframes: {', '.join(result['timeframes'])}")
    print(f"  archives: {result['archive_count']}")
    records = result.get("records_by_schema") or {}
    if records:
        rendered = ", ".join(f"{key}={value:,}" for key, value in records.items())
        print(f"  records: {rendered}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = _run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "error": type(exc).__name__, "message": str(exc)},
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
