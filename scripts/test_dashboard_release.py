"""Dashboard publisher rehearsal. Only disposable static files are ever switched."""
import importlib.util
import logging
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen
import zipfile

from dashboard_release import inspect_artifact, pointer, sha256, stage, switch, verify_page
from release_lib import companion_digest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rehearsal(scratch, probe=lambda: {}, compare=lambda before, after: None, browser=None):
    watch = time.perf_counter()
    timings = {}
    def measure(name, fn):
        start = time.perf_counter()
        value = fn()
        timings[name] = round(time.perf_counter() - start, 4)
        return value
    frontend = scratch / "frontend"
    dist = frontend / "dist"
    (dist / "assets").mkdir(parents=True)
    old = b'<html><body>Gauge<button onclick="import(\'/assets/chart.js\').then(m=>document.title=m.label)">Chart</button></body></html>'
    (dist / "index.html").write_bytes(old)
    (dist / "assets/chart.js").write_bytes(b'export const label="Original chart"')
    base = {"dist_sha256": {"frontend/dist/"+p.relative_to(dist).as_posix(): sha256(p) for p in dist.rglob("*") if p.is_file()}}
    server = load("serve-production-frontend")
    class Handler(server.TopSignalFrontendHandler):
        access_logger = logging.getLogger("dashboard-rehearsal")
        content_security_policy = "default-src 'self'; script-src 'self' 'unsafe-inline'"
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)
    http = server.BoundedFrontendServer(("127.0.0.1", 0), Handler)
    port = http.server_port
    Handler.allowed_host = f"127.0.0.1:{port}"
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        verify = lambda files, rev: verify_page(files, rev, port=port, host_port=port)
        rev = "a"*40
        prefix = "/dashboard-builds/"+rev+"/"
        html = f'<link rel="stylesheet" href="{prefix}assets/chart-12345678.css"><script type="module" src="{prefix}assets/chart-12345678.js"></script>New gauge'
        archive = scratch / "ci.zip"
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr("index.html", html)
            out.writestr("assets/chart-12345678.css", ".gauge { color: blue }")
            out.writestr("assets/chart-12345678.js", 'document.title="New chart"')
        if browser:
            browser("open", port)
        else:
            assert urlopen(f"http://127.0.0.1:{port}/bot").read() == old
        measure("stage", lambda: stage(frontend, archive, rev, sha256(archive)))
        try:
            stage(frontend, archive, rev, "0"*64)
        except ValueError as e:
            assert "digest mismatch" in str(e)
        else:
            raise AssertionError("Bad CI digest accepted")
        def fail(files, target):
            if target:
                raise OSError("injected served-page failure")
            verify(files, None)
        try:
            switch(frontend, None, rev, base, probe, compare, fail)
        except OSError as e:
            assert "injected" in str(e)
        else:
            raise AssertionError("Failed switch accepted")
        assert pointer(frontend)["active"] is None and urlopen(f"http://127.0.0.1:{port}/bot").read() == old
        result = measure("activation_including_host_verification", lambda: switch(frontend, None, rev, base, probe, compare, verify))
        measure("viewing_verification", lambda: verify(inspect_artifact(archive, rev), rev))
        assert urlopen(f"http://127.0.0.1:{port}/assets/chart.js").read() == b'export const label="Original chart"'
        if browser:
            browser("lazy-import", port)
        next_dist = scratch / "next/dist"
        (next_dist / "assets").mkdir(parents=True)
        (next_dist / "index.html").write_bytes(b"full release")
        load("preserve-frontend-assets").preserve_assets(next_dist, dist)
        assert (next_dist / "dashboard-builds" / rev / "index.html").read_text() == html
        measure("rollback", lambda: switch(frontend, rev, None, base, probe, compare, verify))
        assert urlopen(f"http://127.0.0.1:{port}{prefix}assets/chart-12345678.js").read() == b'document.title="New chart"'
        assert urlopen(f"http://127.0.0.1:{port}/bot").read() == old
        timings["wall"] = round(time.perf_counter()-watch, 4)
        return {"rehearsal_seconds": timings, "switch": result}
    finally:
        http.shutdown()
        http.server_close()
        thread.join(2)


class DashboardTests(unittest.TestCase):
    def test_stage_failure_switch_old_tab_rollback_and_full_supersession(self):
        with tempfile.TemporaryDirectory() as scratch:
            rehearsal(Path(scratch))

    def test_python_companion_only_ignores_crlf(self):
        self.assertEqual(companion_digest("helper.py", b"a\r\nb\n"), companion_digest("helper.py", b"a\nb\n"))
        for bad in (b"a\nc\n", b"a \nb\n", b"a\nb"):
            self.assertNotEqual(companion_digest("helper.py", bad), companion_digest("helper.py", b"a\nb\n"))
        self.assertNotEqual(companion_digest("data", b"a\r\n"), companion_digest("data", b"a\n"))

    def test_reviewed_selection_preserves_unrelated_staged_and_unstaged(self):
        from ship import git
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            def run(*args):
                return git(*args, cwd=repo)
            run("init", "-q")
            run("config", "user.name", "Fixture")
            run("config", "user.email", "fixture@example.invalid")
            for name in ["display","other"]:
                (repo/name).write_text("before")
            run("add", ".")
            run("commit", "-qm", "base")
            (repo/"other").write_text("staged")
            run("add", "other")
            (repo/"other").write_text("unstaged")
            (repo/"display").write_text("intended")
            run("--literal-pathspecs","commit","--only","-qm","ship","--","display")
            self.assertEqual(run("show","HEAD:other"), "before")
            self.assertEqual(run("show",":other"), "staged")
            self.assertEqual((repo/"other").read_text(), "unstaged")


if __name__ == "__main__":
    unittest.main()
