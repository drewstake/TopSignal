"""Isolated public-asset deployment and HTTP diagnostics; no application DB."""
import importlib.util
import io
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(root, assets):
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>TopSignal</title>")
    (root / "assets").mkdir()
    for name, data in assets.items():
        (root / "assets" / name).write_text(data)
    return root


def test_bot_view_waits_for_tunnel_then_serves_current_dashboard(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "backend" / "tools"))
    from serve_imbalance_dashboard import dashboard_response

    class Dashboard(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/bot" or self.headers["Host"] != "127.0.0.1:4173":
                return self.send_error(404)
            self.send_response(self.server.dashboard_status)
            self.end_headers()
            self.wfile.write(b"Current dashboard")

    # Reserve a real loopback port, but delay listen() just like the SSH forward at logon.
    with HTTPServer(("127.0.0.1", 0), Dashboard, bind_and_activate=False) as upstream:
        upstream.server_bind()
        port = upstream.server_port
        page = "/bot?account=29593203"
        with dashboard_response(page, "GET", port) as response:
            body = response.read()
            assert response.status == 200
            assert b"Connecting to peteamdpc" in body and b'src="/topsignal-view/bot-retry.js"' in body
            assert int(response.headers["Content-Length"]) == len(body)
        with dashboard_response("/topsignal-view/bot-retry.js", "GET", port) as response:
            assert response.headers["Content-Type"].startswith("text/javascript")
            assert b"window.location.reload()" in response.read()
        with dashboard_response("/bot/", "HEAD", port) as response:
            assert response.status == 200 and response.read() == b""
            assert int(response.headers["Content-Length"]) == len(body)
        with pytest.raises(OSError):
            dashboard_response("/assets/app.js", "GET", port)

        upstream.server_activate()
        upstream.dashboard_status = 200
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        try:
            with dashboard_response(page, "GET", port) as response:
                assert response.read() == b"Current dashboard"
            upstream.dashboard_status = 503
            with pytest.raises(HTTPError) as exc:
                dashboard_response(page, "GET", port)
            exc.value.close()
            assert exc.value.code == 503
        finally:
            upstream.shutdown()
            thread.join(2)


def test_old_tab_assets_survive_one_deployment_without_accumulation(tmp_path):
    module = load("preserve-frontend-assets")
    first = build(tmp_path / "first", {"old.js": "old", "shared.css": "shared"})
    second = build(tmp_path / "second", {"new.js": "new", "shared.css": "shared"})
    result = module.preserve_assets(second, first)
    assert result["copied_bytes"] == 3
    assert (second / "assets/old.js").read_text() == "old"
    third = build(tmp_path / "third", {"newest.js": "newest"})
    module.preserve_assets(third, second)
    assert (third / "assets/new.js").exists()
    assert not (third / "assets/old.js").exists()
    assert (first / "assets/old.js").exists()  # Never purge the original release.


def test_conflicting_asset_and_bad_manifest_fail_before_copy(tmp_path):
    module = load("preserve-frontend-assets")
    first = build(tmp_path / "first", {"a.js": "a", "same.js": "different"})
    second = build(tmp_path / "second", {"same.js": "current"})
    with pytest.raises(ValueError, match="Conflicting"):
        module.preserve_assets(second, first)
    assert not (second / "assets/a.js").exists()
    (first / module.MANIFEST).write_text('{"current_assets": ["../secret"]}')
    with pytest.raises(ValueError, match="missing or unsafe"):
        module.preserve_assets(second, first)
    assert not (second / module.MANIFEST).exists()


def test_served_old_assets_missing_assets_and_query_free_status_logs(tmp_path):
    preserve = load("preserve-frontend-assets")
    old = build(tmp_path / "old", {"old.js": "export default 1;"})
    new = build(tmp_path / "new", {"new.js": "export default 2;"})
    preserve.preserve_assets(new, old)
    module = load("serve-production-frontend")
    output = io.StringIO()
    logger = logging.Logger("isolated.frontend")
    logger.addHandler(logging.StreamHandler(output))
    class Handler(module.TopSignalFrontendHandler):
        access_logger = logger
        content_security_policy = "default-src 'self'"
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(new), **kwargs)
    server = module.BoundedFrontendServer(("127.0.0.1", 0), Handler)
    Handler.allowed_host = f"127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/assets/old.js?access_token=private", timeout=2) as response:
            assert response.read() == b"export default 1;"
            assert response.headers["Cache-Control"] == "no-store"
            assert "javascript" in response.headers["Content-Type"]
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/assets/missing.js?secret=private", timeout=2)
        assert error.value.code == 404
        with urlopen(base + "/bot", timeout=2) as response:
            assert "text/html" in response.headers["Content-Type"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert "GET /assets/old.js status=200" in output.getvalue()
    assert "GET /assets/missing.js status=404" in output.getvalue()
    assert "private" not in output.getvalue()
