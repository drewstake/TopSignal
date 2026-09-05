"""Immutable local source identity. Candle persistence uses the existing tenant table."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalCapture:
    capture_id: str
    directory: Path
    manifest_sha256: str
    contract_id: str
    symbol: str
    live: bool
    rows: int


# This source was explicitly collected and audited for this workspace. No API
# parameter can select a path, contract, mode, manifest, or alternate capture.
LOCAL_MNQ_CAPTURE = LocalCapture(
    capture_id="projectx-mnqu26-20260904",
    directory=Path(__file__).resolve().parents[1] / "storage" / "research" / "quarantine" / "projectx-mnqu26-20260904" / "complete-newer-pool",
    manifest_sha256="25e354280208ae795f402dd88155b3f87c1652b4b976c5b31002fc462dff4576",
    contract_id="CON.F.US.MNQ.U26",
    symbol="MNQ",
    live=False,
    rows=55240,
)
