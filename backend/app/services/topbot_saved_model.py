"""Owner-scoped, read-only snapshots of verified running TopBot artifacts.

Snapshots live in the Databento filesystem cache, separate from live loaders.
Only an operator can register a snapshot; API callers cannot supply file paths.
"""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from .databento_cache import default_cache_root
from .probabilistic_protocol import digest, protocol
from .probabilistic_strategy import PathModel
from .probabilistic_shadow import scope_hash


def load_saved_model(owner, *, root=None, expected_hash=None):
    directory = (Path(root) if root else default_cache_root()) / "backtest-models" / scope_hash(owner)
    try:
        manifest_path = directory / "active.json"
        if manifest_path.stat().st_size > 16000:
            raise ValueError("Saved-model manifest is too large.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        file_hash = manifest["file_sha256"]
        if manifest["owner_hash"] != scope_hash(owner) or not re.fullmatch("[a-f0-9]{64}", file_hash):
            raise ValueError("Saved-model ownership or fingerprint is invalid.")
        path = directory / f"{file_hash}.json"
        if path.stat().st_size > 4_000_000:
            raise ValueError("Saved model exceeds the supported size.")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != file_hash:
            raise ValueError("Saved model checksum changed. Re-verify the running artifact.")
        artifact = json.loads(raw)
        if (artifact.get("owner_hash") != manifest["source_owner_hash"] or artifact.get("root_symbol") != "MNQ"
            or artifact.get("protocol_sha256") != digest(protocol()) or artifact.get("model_sha256") != digest(artifact.get("model"))
            or artifact.get("model_sha256") != manifest["model_sha256"]):
            raise ValueError("Saved model scope, protocol or model checksum is invalid.")
        for name in ("probabilistic_strategy.py", "topbot_mathematical.py", "topbot_session.py"):
            code_hash = sha256(Path(__file__).with_name(name).read_text(encoding="utf-8").encode()).hexdigest()
            if manifest["source_code_sha256"].get(name) != code_hash:
                raise ValueError("TopBot logic differs from the verified running model. Verify compatibility before replay.")
        model = PathModel.from_dict(artifact["model"])
        if model.version != "bayesian_cells_v1":
            raise ValueError("The saved artifact is not the current TopBot Bayesian model.")
        if expected_hash and expected_hash != artifact["model_sha256"]:
            raise ValueError("The saved model changed after selection. Refresh the model details and run again.")
        earliest = model.trained_through + timedelta(minutes=15)
        first_full_day = (earliest.replace(hour=0, minute=0, second=0, microsecond=0)
                          + (timedelta(days=1) if earliest.time().isoformat() != "00:00:00" else timedelta()))
        metadata = {"model_sha256": artifact["model_sha256"], "artifact_file_sha256": file_hash,
            "artifact_created_at": artifact["created_at"], "verified_at": manifest["verified_at"],
            "source_label": manifest["source_label"], "training_paths": len(model.samples),
            "training_sessions": len(artifact.get("training_sessions", [])),
            "training_start": min(artifact.get("training_sessions") or [s.features.day for s in model.samples]),
            "training_end": max(artifact.get("training_sessions") or [s.features.day for s in model.samples]),
            "trained_through": model.trained_through.isoformat(), "earliest_start": earliest.isoformat(),
            "first_full_test_day": first_full_day.date().isoformat(), "validation_status": artifact["validation_status"],
            "model_binding": "saved_running_model", "pool_mix": model.pool_mix}
        return model, metadata
    except FileNotFoundError as exc:
        raise ValueError("No verified running TopBot model is installed for this user on this backend. Import a verified snapshot; no refit will be substituted.") from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The saved-model snapshot is incomplete or invalid. Re-verify it before replay.") from exc
