"""Offline, network-disabled worker for a registered exit-only observation.

stdout carries exactly one JSON document:
  * a study result (with ``replay`` provenance), or
  * ``{"index_progress": {...}}`` when checkpoint building used its whole budget
    before reaching the entry (progress is saved; this is not a failure), or
  * ``{"failure": {...}}`` with exit code 3: fixed categories, exception types and
    source locations only, never messages that could carry paths or data.
stderr carries one JSON progress line per second so the parent can tell slow
work from stalled work.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def forbidden(*args, **kwargs):
    raise RuntimeError("Research worker has no network access")


def progress(value):
    sys.stderr.write(json.dumps(value, separators=(",", ":"))+"\n")
    sys.stderr.flush()


def background_io():
    """Lower this process's I/O and memory priority below the recorders on Windows."""
    if os.name != "nt" or os.environ.get("TOPSIGNAL_RESEARCH_IO_MODE", "background") != "background":
        return "normal"
    import ctypes
    kernel = ctypes.windll.kernel32
    return "background" if kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000) else "background_unavailable"


def failure(exc, phase, started, extra=None):
    from app.services.topbot_evidence import failure_category
    frames = [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in traceback.extract_tb(exc.__traceback__)[-5:]]
    detail = {"category": failure_category(exc), "type": type(exc).__name__, "phase": phase,
              "frames": frames, "seconds": round(time.monotonic()-started, 3)}
    text = str(exc)
    if text.replace("_", "").isalnum() and text.islower() and len(text) <= 64:
        detail["code"] = text  # Fixed identifiers such as invalid_evidence_batch.
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        detail["sqlite_code"] = code
    detail.update(extra or {})
    return detail


def main():
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.create_connection = forbidden
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--checkpoints", action="store_true", help="Use and extend derived replay checkpoints")
    parser.add_argument("--index-budget", type=float, default=90.)
    args = parser.parse_args()
    started = time.monotonic()
    phase = "start"
    replay = {"reader": "full-scan", "io_priority": background_io()}
    try:
        raw = sys.stdin.read(1024*1024+1)
        if len(raw) > 1024*1024:
            raise ValueError("payload_too_large")
        payload = json.loads(raw)
        from app.services.topbot_study import simulate, MODEL_CODE, EXPERIMENT_ID
        from app.services.topbot_study_store import quotes
        from app.services.topbot_depth import utc
        from app.services import topbot_replay_index as index
        entry = payload["entry"]
        snapshot = entry.get("strategy_snapshot") or {}
        if ("study_model_code" in snapshot and snapshot["study_model_code"] != MODEL_CODE) or (
                "experiment_id" in snapshot and snapshot["experiment_id"] != EXPERIMENT_ID):
            # Results for another experiment need that experiment's retained model.
            raise LookupError("recorded_model_unavailable")
        start, cutoff = utc(entry["entry_at"]), utc(snapshot["research_cutoff"])
        owner, contract = payload["owner"], payload["contract"]
        try:
            if not args.capture.is_file():
                raise FileNotFoundError("Registered capture is absent")
            source = None
            if args.checkpoints:
                phase = "checkpoint_lookup"
                try:
                    index.locate(args.capture, owner, contract, start, require_indexed_through=True)
                    source = "ready"
                except LookupError as exc:
                    replay["checkpoint_lookup"] = str(exc)
                    phase = "index"
                    try:
                        built = index.advance(args.capture, owner, contract, until=start,
                                              budget_seconds=args.index_budget, progress=progress)
                    except index.CheckpointError as error:
                        # Derived data only; the capture is replayed with the full scan instead.
                        built = {"state": "checkpoint_error", "reason": str(error)}
                    replay["index"] = built
                    if built["state"] == "budget_exhausted":
                        print(json.dumps({"index_progress": built, "replay": replay}, allow_nan=False))
                        return
                    source = "ready" if built["state"] in {"reached", "end_of_capture"} else None
                except index.CheckpointError as exc:
                    replay["checkpoint_error"] = str(exc)
            phase = "replay"
            result = None
            if source == "ready":
                try:
                    result = simulate(entry, index.resumed_quotes(args.capture, owner, contract, start, cutoff,
                                                                  report=replay, progress=progress))
                except LookupError as exc:
                    replay["checkpoint_lookup"] = str(exc)
                except index.CheckpointError as exc:
                    replay["checkpoint_error"] = str(exc)
                    replay["reader"] = "full-scan"
            if result is None:
                replay["reader"] = "full-scan"
                result = simulate(entry, quotes(args.capture, owner, contract, start, cutoff))
            result["capture_id"] = args.capture.stem
        except (FileNotFoundError, ValueError) as exc:
            # Unchanged recorded handling: unreadable or invalid evidence is missing, never zero.
            result = simulate(entry, [], gap_reason="evidence_unavailable_"+type(exc).__name__)
        replay["seconds"] = round(time.monotonic()-started, 3)
        result["replay"] = replay
        print(json.dumps(result, allow_nan=False))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent, never swallowed silently
        if isinstance(exc, KeyboardInterrupt):
            raise
        print(json.dumps({"failure": failure(exc, phase, started, {"io_priority": replay["io_priority"]})}))
        sys.stdout.flush()
        sys.exit(3)


if __name__ == "__main__":
    main()
