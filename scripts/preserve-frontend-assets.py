"""Carry one preceding build's assets into a NEW, not-yet-served release.

Only public build assets are copied. The manifest lists the new build's own
assets, so successive deployments do not accumulate every historical build.
Existing release directories and source archives are never removed or changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

MANIFEST = "build-assets.json"


def asset_files(root: Path) -> dict[str, Path]:
    assets = root / "assets"
    result = {}
    for item in assets.rglob("*"):
        if item.is_symlink() or item.resolve().is_relative_to(root.resolve()) is False:
            raise ValueError("Assets must stay inside their build directory")
        if item.is_file():
            result[item.relative_to(root).as_posix()] = item
    return result


def preserve_assets(directory: Path, previous: Path) -> dict:
    directory, previous = directory.resolve(), previous.resolve()
    if directory == previous or directory.is_relative_to(previous) or previous.is_relative_to(directory):
        raise ValueError("Build directories must be separate")
    if not (directory / "index.html").is_file() or not (previous / "index.html").is_file():
        raise ValueError("Both directories must contain a built index.html")
    if (directory / MANIFEST).exists():
        raise ValueError("Destination already prepared; rebuild before preparing it again")
    current = asset_files(directory)
    old = asset_files(previous)
    manifest = previous / MANIFEST
    names = json.loads(manifest.read_text(encoding="utf-8"))["current_assets"] if manifest.exists() else sorted(old)
    if not isinstance(names, list) or not names or len(names) > 5000:
        raise ValueError("Invalid or excessive preceding-build asset list")
    plan = []
    for name in names:
        if not isinstance(name, str) or name not in old:
            raise ValueError("Preceding build manifest references a missing or unsafe asset")
        if name in current:
            if hashlib.sha256(old[name].read_bytes()).digest() != hashlib.sha256(current[name].read_bytes()).digest():
                raise ValueError(f"Conflicting build asset: {name}")
        else:
            plan.append((old[name], directory / name))
    total = sum(src.stat().st_size for src, _ in plan)
    if total > 128 * 1024 * 1024 or shutil.disk_usage(directory).free < total + 64 * 1024 * 1024:
        raise ValueError("Insufficient space or preceding build exceeds 128 MiB")
    # Validate the complete plan before writing. A failed staging directory must
    # never be activated; the source release remains untouched.
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    result = {"current_assets": sorted(current), "previous_assets": sorted(set(names)), "copied_bytes": total}
    (directory / MANIFEST).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--previous-dist", type=Path, required=True)
    args = parser.parse_args()
    result = preserve_assets(args.directory, args.previous_dist)
    print(f"Prepared preceding-build assets: {result['copied_bytes']} bytes copied")
