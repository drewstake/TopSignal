"""Shared, dependency-free release integrity checks (no production writes)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile


def is_link(path: Path) -> bool:
    # Path.is_junction is unavailable on CI's Python 3.11. Detect all Windows
    # reparse points without following them, including directory junctions.
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or any(char in name for char in '\\:<>"|?*') or path.is_absolute()
            or any(part in ("", ".", "..") for part in name.split("/"))
            or any(part.endswith((".", " ")) for part in path.parts)
            or any(part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]} for part in path.parts)):
        raise ValueError(f"Unsafe release path: {name!r}")
    return name


def source_allowed(name: str) -> bool:
    parts = PurePosixPath(safe_name(name)).parts
    return not any(p in {".git", ".venv", "node_modules", "storage", "tmp", "__pycache__"}
                   or (p.startswith(".env") and p != ".env.example") for p in parts)


def archive_members(path: Path, expected: dict[str, str]) -> dict[str, bytes]:
    """Validate the entire archive before extracting even one file."""
    result = {}
    folded = set()
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Release archive exceeds 512 MiB")
        for item in archive.infolist():
            if item.is_dir():
                continue
            name = safe_name(item.filename)
            if name.casefold() in folded or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Duplicate/case-colliding or linked archive entry")
            folded.add(name.casefold())
            data = archive.read(item)
            if hashlib.sha256(data).hexdigest() != expected.get(name):
                raise ValueError(f"Archive checksum mismatch: {name}")
            result[name] = data
    if set(result) != set(expected):
        raise ValueError("Archive file inventory mismatch")
    for name in result:
        if any(parent.as_posix().casefold() in folded for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
            raise ValueError("Archive contains a file/directory path collision")
    return result


def verify_bundle(bundle: Path, expected_manifest: str | None = None) -> dict:
    manifest_path = bundle / "manifest.json"
    if expected_manifest and sha256(manifest_path) != expected_manifest:
        raise ValueError("Manifest checksum mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not manifest.get("validated"):
        raise ValueError("Unvalidated or unsupported release bundle")
    if set(manifest.get("archives", {})) != {"source.zip", "frontend.zip"}:
        raise ValueError("Unexpected archive inventory")
    for name, checksum in manifest["archives"].items():
        if is_link(bundle / name) or sha256(bundle / name) != checksum:
            raise ValueError(f"Bundle checksum mismatch: {name}")
    source = archive_members(bundle / "source.zip", manifest["source_sha256"])
    frontend = archive_members(bundle / "frontend.zip", manifest["dist_sha256"])
    if not all(source_allowed(n) and not n.startswith("frontend/dist/") for n in source):
        raise ValueError("Private/runtime data in source archive")
    if not frontend or not all(n.startswith("frontend/dist/") for n in frontend):
        raise ValueError("Frontend archive escapes dist")
    if "frontend/dist/index.html" not in frontend:
        raise ValueError("Frontend index missing")
    # Windows extraction must not alias a source file to an asset.
    names = [n.casefold() for n in [*source, *frontend]]
    if len(set(names)) != len(names):
        raise ValueError("Source and frontend inventories overlap")
    return manifest


def extract_bundle(bundle: Path, destination: Path, expected_manifest: str, initialize=None) -> dict:
    manifest = verify_bundle(bundle, expected_manifest)
    if destination.exists():
        raise ValueError("Candidate already exists; never overwrite a release")
    # Both archives have been checked before creating a destination.
    destination.mkdir(parents=True)
    if initialize is not None:
        initialize(destination)
    for archive, key in (("source.zip", "source_sha256"), ("frontend.zip", "dist_sha256")):
        for name, data in archive_members(bundle / archive, manifest[key]).items():
            target = destination / name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Extraction escaped candidate")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return manifest


def verify_files(root: Path, inventory: dict[str, str]) -> None:
    for name, checksum in inventory.items():
        safe_name(name)
        path = root / name
        if is_link(path) or not path.resolve().is_relative_to(root.resolve()) or sha256(path) != checksum:
            raise ValueError(f"Release file drift: {name}")
