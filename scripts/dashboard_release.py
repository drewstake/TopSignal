"""Immutable dashboard builds and a release-scoped, compare-and-swap HTML pointer."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.request import build_opener, ProxyHandler, Request, HTTPRedirectHandler
import zipfile

from release_lib import archive_members, is_link, sha256, verify_files


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def commit(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected an exact Git commit")
    return value


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        file.write(json.dumps(data, sort_keys=True).encode())
        file.flush()
        os.fsync(file.fileno())
    try:
        # Windows readers can hold the previous JSON open for a few milliseconds.
        for attempt in range(20):
            try:
                os.replace(file.name, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    finally:
        Path(file.name).unlink(missing_ok=True)


def public_name(name):
    return re.fullmatch(r"assets/[\w.-]+-[A-Za-z0-9_-]{8}\.(?:js|css|woff2|png|svg)|index\.html|topsignal\.svg", name)


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "link"):
            self.urls.append(attrs.get("src" if tag == "script" else "href", ""))


def inspect_artifact(archive, revision):
    prefix = "/dashboard-builds/" + commit(revision) + "/"
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > 128 * 1024**2:
            raise ValueError("Oversized dashboard build")
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        if not all(public_name(n) for n in names) or "index.html" not in names:
            raise ValueError("Unexpected dashboard artifact inventory")
        files = {n: hashlib.sha256(z.read(n)).hexdigest() for n in names}
    contents = archive_members(archive, files)
    refs = References()
    refs.feed(contents["index.html"].decode("utf8"))
    if (not any(u.endswith(".js") for u in refs.urls) or not any(u.endswith(".css") for u in refs.urls)
            or any(not u.startswith(prefix) or u[len(prefix):] not in files for u in refs.urls)):
        raise ValueError("Dashboard HTML references the wrong build or a missing asset")
    return files


def stage(frontend, archive, revision, digest):
    if sha256(archive) != digest:
        raise ValueError("Dashboard CI artifact digest mismatch")
    files = inspect_artifact(archive, revision)
    versions = frontend / "dist/dashboard-builds"
    versions.mkdir(exist_ok=True)
    target = versions / revision
    if not target.exists():
        # Inherit the protected frontend ACL. Partial assets never have a public URL.
        with tempfile.TemporaryDirectory(dir=frontend) as scratch:
            build = Path(scratch) / "build"
            for name, data in archive_members(archive, files).items():
                path = build / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            verify_files(build, files)
            build.rename(target)
    verify_build(target, files)
    write_atomic(frontend / "dashboard-receipts" / (revision + ".json"), files)
    return files


def verify_build(directory, files):
    if is_link(directory) or {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} != set(files):
        raise ValueError("Immutable dashboard inventory changed")
    verify_files(directory, files)


def pointer(frontend):
    path = frontend / "dashboard.json"
    if not path.exists():
        return {"active": None, "previous": None}
    state = json.loads(path.read_bytes())
    for field in ("active", "previous"):
        if state[field] is not None:
            commit(state[field])
    return state


def inventory(frontend, revision, base_manifest):
    if revision is None:
        return {n.removeprefix("frontend/dist/"): h for n, h in base_manifest["dist_sha256"].items()}
    return json.loads((frontend / "dashboard-receipts" / (commit(revision) + ".json")).read_bytes())


def verify_page(files, revision, *, port=4173, host_port=4173, route="/bot"):
    prefix = "/dashboard-builds/" + commit(revision) + "/" if revision else "/"
    def check(pair):
        name, expected = pair
        url = route if name == "index.html" else prefix + name
        req = Request(f"http://127.0.0.1:{port}" + url, headers={"Host": f"127.0.0.1:{host_port}", "Cache-Control": "no-cache"})
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=12) as response:
            if response.status != 200 or hashlib.sha256(response.read()).hexdigest() != expected:
                raise ValueError(f"Served dashboard bytes differ at {url} (port {port})")
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(check, files.items()))


def switch(frontend, expected, target, base_manifest, probe, compare, verify=verify_page):
    old = pointer(frontend)
    if old["active"] != expected:
        raise ValueError("Dashboard changed since classification")
    files = inventory(frontend, target, base_manifest)
    if target:
        verify_build(frontend / "dist/dashboard-builds" / target, files)
    else:
        verify_files(frontend / "dist", files)
    before = probe()
    write_atomic(frontend / "dashboard.json", {"active": target, "previous": expected})
    try:
        verify(files, target)
        after = probe()
        compare(before, after)
        if target:
            write_atomic(frontend / "dashboard-receipts" / (target + ".verified.json"),
                         {"active": target, "before": before, "after": after})
    except Exception:
        write_atomic(frontend / "dashboard.json", old)
        verify(inventory(frontend, expected, base_manifest), expected)
        raise
    return {"before": before, "after": after, "active": target}


def continuity(host, config, release):
    host.health(config, release)
    sample = host.independent_recording(config)
    script = ("$ErrorActionPreference='Stop'; @(Get-NetTCPConnection -LocalPort 8000,4173 -State Listen | "
              "Sort-Object LocalPort | ForEach-Object { $p=Get-Process -Id $_.OwningProcess; "
              "'{0}/{1}/{2}' -f $_.LocalPort,$p.Id,$p.StartTime.ToUniversalTime().Ticks }) | ConvertTo-Json")
    sample["listeners"] = json.loads(subprocess.check_output(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]))
    if len(sample["listeners"]) != 2:
        raise ValueError("Expected both dashboard and API/worker listeners")
    return sample


def compare_continuity(host, before, after):
    host.check_independent_recording(before, after)
    if before["listeners"] != after["listeners"]:
        raise ValueError("API/worker or dashboard process changed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["stage", "activate", "rollback"])
    parser.add_argument("release", type=Path)
    parser.add_argument("base_stage", type=Path)
    parser.add_argument("candidate_stage", type=Path)
    parser.add_argument("expected")
    parser.add_argument("revision")
    parser.add_argument("digest")
    args = parser.parse_args()
    frontend = args.release / "frontend"
    manifest = json.loads((args.base_stage / "manifest.json").read_bytes())
    config = json.loads((args.base_stage / "host.json").read_bytes())
    if sha256(args.base_stage / "manifest.json") != config["manifest_sha256"] or manifest["git_commit"] != config["git_commit"]:
        raise ValueError("Running full release manifest mismatch")
    expected = None if args.expected == "base" else commit(args.expected)
    if pointer(frontend)["active"] != expected:
        raise ValueError("Dashboard changed since classification")
    if args.action == "stage":
        files = stage(frontend, args.candidate_stage / "dashboard.zip", args.revision, args.digest)
        print(json.dumps({"files": len(files)}))
        return
    revision = None if args.revision == "base" else commit(args.revision)
    if args.action == "rollback":
        if pointer(frontend)["previous"] != revision:
            raise ValueError("Expected the preceding dashboard")
    else:
        ci_file = args.candidate_stage / "dashboard-ci.json"
        ci = json.loads(ci_file.read_bytes())
        if (sha256(ci_file) != args.digest or ci["commit"] != revision or not 0 <= time.time()-ci["verified_at"] <= 900
                or ci["digest"] != sha256(args.candidate_stage / "dashboard.zip")
                or inspect_artifact(args.candidate_stage / "dashboard.zip", revision) != inventory(frontend, revision, manifest)):
            raise ValueError("Dashboard CI receipt or staged artifact mismatch")
    spec = importlib.util.spec_from_file_location("full_release", Path(__file__).with_name("release-host.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    print(json.dumps(switch(frontend, expected, revision, manifest,
                            lambda: continuity(host, config, args.release),
                            lambda before, after: compare_continuity(host, before, after))))


if __name__ == "__main__":
    main()
