"""Bounded local capture/replay. No database or trading-service dependency."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .depth_research import Event, ResearchBook, canonical, digest, timestamp
from .probabilistic_shadow import cache_root
from .projectx_client import ProjectXClient, ProjectXClientError, validate_projectx_url

MAX_CAPTURE_BYTES = 2 * 1024 ** 3
TOTAL_BUDGET_BYTES = 5 * 1024 ** 3
SEGMENT_BYTES = 16 * 1024 ** 2


def check_storage(root: Path, growth: int):
    base = root / "depth-v1"
    base.mkdir(parents=True, exist_ok=True)
    used = sum(p.stat().st_size for p in base.rglob("*") if p.is_file())
    if growth < 0 or used + growth > TOTAL_BUDGET_BYTES or shutil.disk_usage(base).free < growth + 1024 ** 3:
        raise CaptureLimit("Local budget/free-space limit; archive with a verified backup before more capture.")


class ReadOnlyMarketClient(ProjectXClient):
    """Deny by default even if future code accidentally calls an order method."""
    def _request_once(self, method, path, **kwargs):
        if method != "POST" or path not in {"/api/Auth/loginKey", "/api/Contract/search", "/api/Contract/available", "/api/History/retrieveBars"}:
            raise RuntimeError("Depth research permits authentication and contract lookup only.")
        return super()._request_once(method, path, **kwargs)


def active_mnq(rows: list[dict]) -> str:
    matches = [r["id"] for r in rows if str(r.get("id", "")).startswith("CON.F.US.MNQ.")
               and r.get("active_contract") is True and r.get("tick_size") == .25 and r.get("tick_value") == .5]
    if len(matches) != 1:
        raise ValueError("Exactly one authoritative active MNQ contract with expected tick metadata is required.")
    return matches[0]


def stream_key(contract: str, live: bool) -> str:
    return hashlib.sha256(f"{contract}|{int(live)}".encode()).hexdigest()


def latest_path(owner_hash: str, contract: str, live: bool, root: Path | None = None) -> Path:
    return (root or cache_root()) / "depth-v1/latest" / owner_hash / (stream_key(contract, live) + ".json")


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical(value)); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # Only the temporary file just created by this call.


class CaptureLimit(RuntimeError):
    pass


class LocalCapture:
    def __init__(self, *, root: Path, owner_hash: str, data_live: bool = False,
                 max_bytes: int = MAX_CAPTURE_BYTES, max_events: int = 250000,
                 segment_bytes: int = SEGMENT_BYTES, synthetic: bool = False):
        if not 4096 <= max_bytes <= MAX_CAPTURE_BYTES or not 1 <= max_events <= 5000000 or not 512 <= segment_bytes <= SEGMENT_BYTES:
            raise ValueError("Capture bounds exceed the registered local policy.")
        if len(owner_hash) != 64 or any(c not in "0123456789abcdef" for c in owner_hash):
            raise ValueError("Expected a full owner SHA-256.")
        self.root = root.resolve()
        base = self.root / "depth-v1"
        base.mkdir(parents=True, exist_ok=True)
        # Stop on age/budget; never automatically delete or restore history.
        manifests = list(base.glob("captures/*/*/manifest.json"))
        if any(time.time() - p.stat().st_mtime > 30 * 86400 for p in manifests):
            raise CaptureLimit("Capture older than 30 days needs reviewed archival and verified off-device backup.")
        check_storage(self.root, max_bytes)
        self.lock = base / ".capture.lock"
        try:
            self.lock_handle = self.lock.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise CaptureLimit("A collector lock exists; verify the owning process before recovery.") from exc
        self.lock_handle.write(str(os.getpid())); self.lock_handle.flush()
        self.capture_id = uuid4().hex
        self.owner_hash, self.data_live, self.synthetic = owner_hash, data_live, synthetic
        self.path = base / "captures" / owner_hash / self.capture_id
        self.path.mkdir(parents=True)
        self.max_bytes, self.max_events, self.segment_bytes = max_bytes, max_events, segment_bytes
        self.index = self.bytes = 0
        self.segments: list[dict] = []
        self.handle = None
        self.segment_size = 0
        self.segment_hash = hashlib.sha256()
        self.books: dict[str, ResearchBook] = {}
        self.counts: Counter = Counter()
        self.started = datetime.now(timezone.utc)
        self.code_sha256 = digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                   for name in ("depth_capture.py", "depth_research.py", "projectx_client.py", "projectx_hubs.py")})
        self.finished = False
        self.last_status = 0.

    def _close_segment(self):
        if self.handle:
            self.handle.flush(); os.fsync(self.handle.fileno()); self.handle.close()
            self.segments.append({"file": f"{len(self.segments):06d}.ndjson", "bytes": self.segment_size,
                                  "sha256": self.segment_hash.hexdigest()})
            self.handle = None

    def append(self, *, contract: str, kind: str, payload: dict, received_at: datetime | None = None,
               provider_at: datetime | None = None, monotonic_ns: int | None = None,
               source: str = "projectx", terminal: bool = False) -> Event:
        if self.finished:
            raise ValueError("Capture is closed.")
        received_at = received_at or datetime.now(timezone.utc)
        e = Event(self.owner_hash, contract, self.data_live, self.capture_id, self.index + 1,
                  received_at, time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
                  kind, payload, provider_at, source, self.synthetic)
        line = canonical(e.to_dict()) + b"\n"
        reserve = 2048 + 1024 * len(self.books) if not terminal else 0
        if self.bytes + len(line) + reserve > self.max_bytes or (self.index >= self.max_events and not terminal):
            raise CaptureLimit("Capture byte/event limit reached.")
        if self.handle is None or self.segment_size + len(line) > self.segment_bytes:
            self._close_segment()
            self.handle = (self.path / f"{len(self.segments):06d}.ndjson").open("xb")
            self.segment_hash = hashlib.sha256(); self.segment_size = 0
        self.handle.write(line)
        self.segment_hash.update(line); self.segment_size += len(line)
        self.bytes += len(line); self.index += 1; self.counts[kind] += 1
        book = self.books.setdefault(contract, ResearchBook(self.owner_hash, contract, self.data_live))
        book.apply(e)
        if not self.synthetic and (time.monotonic() - self.last_status >= 1 or kind in {"disconnect", "gap", "end", "roll"}):
            self.publish(book, e.received_at)
            self.last_status = time.monotonic()
        return e

    def publish(self, book: ResearchBook, as_of: datetime):
        # Small latest-state artifact only; replay arrays stay in segments.
        payload = {"schema": "mnq-depth-latest-v1", "owner_hash": self.owner_hash,
                   "contract_id": book.scope[1], "data_live": self.data_live, "capture_id": self.capture_id,
                   "as_of": as_of.isoformat(), "status": book.status(as_of), "features": book.features(as_of),
                   "source": "projectx", "fixture": False}
        atomic_json(latest_path(self.owner_hash, book.scope[1], self.data_live, self.root), payload)

    def finish(self, reason: str = "bounded_capture_complete", evidence: dict | None = None) -> dict:
        if self.finished:
            raise ValueError("Capture already closed.")
        try:
            for contract, book in self.books.items():
                self.append(contract=contract, kind="end", payload={"reason": reason}, terminal=True,
                            received_at=book.received_at if self.synthetic else None,
                            monotonic_ns=book.monotonic_ns if self.synthetic else None)
            self._close_segment()
            result = {"schema": "mnq-depth-capture-v1", "capture_id": self.capture_id,
                      "owner_hash": self.owner_hash, "data_live": self.data_live, "synthetic": self.synthetic,
                      "started_at": self.started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
                      "reason": reason, "events": self.index, "bytes": self.bytes, "counts": dict(self.counts),
                      "segments": self.segments, "contracts": sorted(self.books),
                      "collector_code_sha256": self.code_sha256,
                      "capability": {c: b.status(datetime.now(timezone.utc)) for c, b in self.books.items()},
                      "diagnostics": {c: dict(b.counts) for c, b in self.books.items()}, "evidence": evidence or {},
                      "provider_sequence_guarantee": False, "snapshot_completion_verified": False,
                      "storage": {"root_budget_bytes": TOTAL_BUDGET_BYTES, "capture_limit_bytes": self.max_bytes,
                                  "rotation_bytes": self.segment_bytes, "retention_review_days": 30,
                                  "automatic_deletion": False, "off_device_backup_verified": False}}
            atomic_json(self.path / "manifest.json", result)
            return result
        finally:
            self.finished = True
            if self.handle:
                self.handle.close()
            self.lock_handle.close(); self.lock.unlink()


def replay_events(path: Path):
    """Verify immutable segments before yielding any events; never timestamp-sort."""
    manifest_file = path / "manifest.json"
    if manifest_file.stat().st_size > 1000000:
        raise ValueError("Oversized capture manifest.")
    m = json.loads(manifest_file.read_text(encoding="utf-8"))
    if m.get("schema") != "mnq-depth-capture-v1" or len(m["segments"]) > 2000:
        raise ValueError("Invalid capture manifest.")
    for index, segment in enumerate(m["segments"]):
        if segment["file"] != f"{index:06d}.ndjson" or not 0 <= segment["bytes"] <= SEGMENT_BYTES + 70000:
            raise ValueError("Invalid segment path or size.")
        f = path / segment["file"]
        with f.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if f.stat().st_size != segment["bytes"] or actual != segment["sha256"]:
            raise ValueError("Capture segment integrity check failed.")
    count = 0
    for segment in m["segments"]:
        with (path / segment["file"]).open("rb") as handle:
            for line in handle:
                if len(line) > 70000:
                    raise ValueError("Oversized raw record.")
                e = Event.from_dict(json.loads(line))
                count += 1
                if (e.index != count or e.owner_hash != m["owner_hash"] or e.capture_id != m["capture_id"]
                        or e.data_live != m["data_live"] or e.synthetic != m["synthetic"]):
                    raise ValueError("Capture scope/order mismatch.")
                yield e
    if count != m["events"]:
        raise ValueError("Capture event count mismatch.")


async def collect(client: ReadOnlyMarketClient, capture: LocalCapture, *, seconds: int = 60) -> dict:
    """Dedicated market-only socket; never changes existing viewers or bot runs."""
    from .projectx_hubs import _open_hub, _append_query, _signalr_handshake
    if not 1 <= seconds <= 28800:
        raise ValueError("Capture must be bounded to 1–28800 seconds.")
    deadline = time.monotonic() + seconds
    evidence = {"authentication": "not_attempted", "subscription_acknowledgements": {},
                "reconnects": 0, "rolls": [], "error_types": [], "payload_fields": {}, "contract_metadata": None}
    reason = "bounded_capture_complete"
    contract = None
    try:
        while time.monotonic() < deadline:
            bar_task = None
            try:
                rows = await asyncio.to_thread(client.search_contracts, search_text="MNQ", live=capture.data_live)
                selected = active_mnq(rows)
                evidence["authentication"] = "success"
                evidence["contract_metadata"] = {"contract_id": selected, "tick_size": .25, "tick_value": .5, "active": True}
                if contract and contract != selected:
                    capture.append(contract=contract, kind="roll", payload={"next_contract": selected})
                    evidence["rolls"].append({"from": contract, "to": selected})
                contract = selected
                token = await asyncio.to_thread(client.get_access_token)
                hub = validate_projectx_url(os.getenv("PROJECTX_MARKET_HUB_URL") or "https://rtc.topstepx.com/hubs/market", websocket=True)
                url = _append_query(hub, {"access_token": token})
                capture.append(contract=contract, kind="connect", payload={"reason": "new_socket"})
                async with _open_hub(url, open_timeout=10, close_timeout=2, max_size=1048576, max_queue=16) as socket:
                    pending_frames = await _signalr_handshake(socket)
                    targets = ("SubscribeContractQuotes", "SubscribeContractTrades", "SubscribeContractMarketDepth")
                    for i, target in enumerate(targets):
                        await socket.send(json.dumps({"type": 1, "invocationId": str(i), "target": target, "arguments": [contract]}) + "\x1e")
                    next_ping, next_roll, next_bars = time.monotonic() + 10, time.monotonic() + 60, 0.
                    while time.monotonic() < deadline:
                        if time.monotonic() >= next_bars and bar_task is None:
                            end = datetime.now(timezone.utc)
                            bar_task = asyncio.create_task(asyncio.to_thread(client.retrieve_bars, contract_id=contract,
                                live=capture.data_live, start=end - timedelta(hours=3), end=end, unit=2,
                                unit_number=5, limit=36, include_partial_bar=False))
                            next_bars = time.monotonic() + 60
                        if bar_task is not None and bar_task.done():
                            bars = bar_task.result(); bar_task = None
                            arrived = datetime.now(timezone.utc)
                            for bar in bars:
                                payload = {k: v for k, v in bar.items() if k != "raw_payload"}
                                payload["timestamp"] = bar["timestamp"].isoformat()
                                payload["raw_payload"] = bar["raw_payload"]
                                capture.append(contract=contract, kind="candle", payload=payload,
                                               received_at=arrived, provider_at=bar["timestamp"])
                        if time.monotonic() >= next_roll:
                            rows = await asyncio.to_thread(client.search_contracts, search_text="MNQ", live=capture.data_live)
                            if active_mnq(rows) != contract:
                                break  # New socket/epoch, no cross-contract book mixing.
                            next_roll = time.monotonic() + 60
                        if time.monotonic() >= next_ping:
                            await socket.send('{"type":6}\x1e'); next_ping = time.monotonic() + 10
                        if pending_frames:
                            raw = "\x1e".join(json.dumps(f) for f in pending_frames)
                            pending_frames = []
                        else:
                            try:
                                raw = await asyncio.wait_for(socket.recv(), timeout=min(1., max(.001, deadline - time.monotonic())))
                            except asyncio.TimeoutError:
                                continue
                        receive = datetime.now(timezone.utc); mono = time.monotonic_ns()
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        for part in raw.split("\x1e"):
                            if not part:
                                continue
                            frame = json.loads(part)
                            if frame.get("type") == 3:
                                invocation = frame.get("invocationId")
                                if invocation in {"0", "1", "2"}:
                                    status = "rejected" if frame.get("error") else "accepted"
                                    evidence["subscription_acknowledgements"][targets[int(invocation)]] = status
                                    if status == "rejected":
                                        capture.append(contract=contract, kind="gap", payload={"reason": "subscription_rejected"},
                                                       received_at=receive, monotonic_ns=mono)
                                continue
                            if frame.get("type") == 7:
                                raise ConnectionError("Provider closed stream.")
                            kind = {"GatewayDepth": "depth", "GatewayQuote": "quote", "GatewayTrade": "trade"}.get(frame.get("target"))
                            args = frame.get("arguments", [])
                            if not kind or len(args) != 2 or args[0] != contract:
                                continue
                            entries = args[1] if isinstance(args[1], list) else [args[1]]
                            for payload in entries:
                                if not isinstance(payload, dict):
                                    capture.append(contract=contract, kind="gap", payload={"reason": "unrecognized_payload", "raw": payload},
                                                   received_at=receive, monotonic_ns=mono)
                                    continue
                                evidence["payload_fields"][kind] = sorted(set(evidence["payload_fields"].get(kind, [])) | set(payload))
                                try:
                                    provider = timestamp(payload["timestamp"])
                                except (KeyError, TypeError, ValueError):
                                    provider = None
                                capture.append(contract=contract, kind=kind, payload=payload, received_at=receive,
                                               monotonic_ns=mono, provider_at=provider)
                capture.append(contract=contract, kind="disconnect", payload={"reason": "socket_closed"})
            except CaptureLimit:
                reason = "capture_limit"; break
            except Exception as exc:
                # Provider exception text can contain credentials; record types/codes only.
                evidence["error_types"].append({"type": type(exc).__name__, "status_code": getattr(exc, "status_code", None),
                                                "reason_code": getattr(exc, "reason_code", None)})
                if contract:
                    capture.append(contract=contract, kind="disconnect", payload={"reason": type(exc).__name__})
                if isinstance(exc, ProjectXClientError) and (exc.status_code in {401, 403, 429} or "auth" in str(exc.reason_code).lower()):
                    reason = "authentication_or_rate_limit"; break
                evidence["reconnects"] += 1
                if evidence["reconnects"] >= 3:
                    reason = "bounded_reconnect_limit"; break
                await asyncio.sleep(min(2 ** evidence["reconnects"], max(0, deadline - time.monotonic())))
            finally:
                if bar_task is not None:
                    # HTTP is read-only and has a bounded client timeout. Consume
                    # completion before closing the capture, without any further write.
                    try:
                        await bar_task
                    except Exception:
                        pass
    except BaseException as exc:
        reason = "interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "collector_failed"
        evidence["error_types"].append({"type": type(exc).__name__})
        raise
    finally:
        report = capture.finish(reason, evidence)
    return report
