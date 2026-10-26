"""Register an operator-verified running-model copy for local replay only.

Does not train, promote, upload, modify live artifacts, or write to SQL.
Supply the file checksum and three code hashes independently read from its host.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--file-sha256", required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--strategy-sha256", required=True)
    parser.add_argument("--adapter-sha256", required=True)
    parser.add_argument("--session-sha256", required=True)
    args = parser.parse_args()
    from app.services.databento_cache import default_cache_root
    from app.services.probabilistic_shadow import scope_hash
    from app.services.probabilistic_protocol import digest, protocol
    from app.services.probabilistic_strategy import PathModel
    raw = args.artifact.read_bytes()
    file_hash = sha256(raw).hexdigest()
    artifact = json.loads(raw)
    if file_hash != args.file_sha256.lower() or len(raw) > 4_000_000:
        raise ValueError("Artifact does not match the independently verified checksum or size limit")
    if artifact.get("model_sha256") != digest(artifact["model"]) or artifact.get("protocol_sha256") != digest(protocol()):
        raise ValueError("Model or protocol checksum mismatch")
    model = PathModel.from_dict(artifact["model"])
    if model.version != "bayesian_cells_v1":
        raise ValueError("Expected the current Bayesian TopBot model")
    code = dict(zip(("probabilistic_strategy.py", "topbot_mathematical.py", "topbot_session.py"),
                    (args.strategy_sha256, args.adapter_sha256, args.session_sha256)))
    for name, expected in code.items():
        if sha256((Path(__file__).resolve().parents[1]/"app/services"/name).read_text(encoding="utf-8").encode()).hexdigest() != expected:
            raise ValueError(f"Host code mismatch: {name}")
    directory = default_cache_root()/"backtest-models"/scope_hash(args.owner)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/f"{file_hash}.json"
    if not target.exists():
        target.write_bytes(raw)
    elif sha256(target.read_bytes()).hexdigest() != file_hash:
        raise ValueError("Existing snapshot checksum is invalid")
    manifest = {"owner_hash": scope_hash(args.owner), "source_owner_hash": artifact["owner_hash"],
                "file_sha256": file_hash, "model_sha256": artifact["model_sha256"], "source_code_sha256": code,
                "source_label": args.source_label, "verified_at": datetime.now(timezone.utc).isoformat()}
    pending = directory/"active.pending.json"
    pending.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pending.replace(directory/"active.json")
    print(json.dumps({"model_sha256": artifact["model_sha256"], "paths": len(model.samples), "trained_through": model.trained_through.isoformat(), "purpose": "historical_replay_only"}))


if __name__ == "__main__":
    main()
