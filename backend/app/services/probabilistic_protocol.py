"""One registered specification shared by research, refits and execution."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

PROTOCOL_PATH = Path(__file__).resolve().parents[3] / "docs/topbot-probabilistic-protocol-v3.json"
PROTOCOL_SHA256 = "70cb218611e47b77470126a9c4dfc5cfb89b59fef7dead547aaf90427a8aaa96"


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def protocol() -> dict:
    value = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if value["protocol_version"] != "mnq-probabilistic-research-v3":
        raise ValueError("Unsupported registered protocol")
    if digest(value) != PROTOCOL_SHA256:
        raise ValueError("Registered protocol changed; register a new version before research")
    return value


def assert_protocol(value: dict) -> None:
    if digest(value) != digest(protocol()):
        raise ValueError("Protocol does not match the code-owned registered specification")


def implementation_sha() -> str:
    root = Path(__file__).resolve().parents[3]
    services = root / "backend/app/services"
    paths = [*sorted(services.glob("probabilistic_*.py")),
             *(services / name for name in ("topbot_session.py", "topbot_mathematical.py",
                 "topbot_time_exit.py", "topbot.py", "bot_service.py", "bot_risk.py", "trading_day.py", "databento_cache.py", "depth_model.py")),
             root / "backend/tools/research_probabilistic_v3.py", root / "backend/requirements.txt"]
    # Canonical newlines make this stable across Windows and Linux checkouts.
    return digest({p.relative_to(root).as_posix(): sha256(p.read_text(encoding="utf-8").encode()).hexdigest() for p in paths})
