"""Side-by-side static dashboard. Only the read-only study goes through this server."""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import threading
import time
from hashlib import sha256
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

from maintain_session_flow import NoRedirect, read_json

ROUTE = re.compile(r"/api/accounts/([1-9][0-9]{0,17})/topbot/edge")
PUBLIC_HOST = "127.0.0.1:4173"


def dashboard_response(path, method, port=4176):
    # Pass only the static request path. The existing forwarding process owns
    # the SSH connection. Study API requests retain their separate owner check.
    path = urlsplit(path).path
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Invalid dashboard path")
    req = request.Request(f"http://127.0.0.1:{port}" + path, method=method, headers={"Host": PUBLIC_HOST})
    return request.build_opener(request.ProxyHandler({}), NoRedirect()).open(req, timeout=8)


class ViewingCopy:
    """Fetch only an authorized account's compact report over the existing private file-transfer connection."""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.checked = {}

    def read(self, owner, account):
        digest = sha256(str(owner).encode()).hexdigest()
        target = Path(self.config["runtime"]) / f"{digest}-{int(account)}.json"
        with self.lock:
            if time.monotonic() - self.checked.get(target, -60) >= 15 or not target.exists():
                temp = target.with_suffix(".pending")
                remote = (
                    self.config["remote_cache"] + f"/imbalance-study-v1/{digest}/{int(account)}/report.json"
                )
                try:
                    subprocess.run(
                        [
                            self.config["scp"],
                            "-q",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "IdentitiesOnly=yes",
                            "-o",
                            "StrictHostKeyChecking=yes",
                            "-o",
                            "ConnectTimeout=4",
                            "-i",
                            self.config["key"],
                            self.config["target"] + ":" + remote,
                            str(temp),
                        ],
                        timeout=9,
                        check=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    if (
                        temp.stat().st_size > 8 * 1024**2
                        or json.loads(temp.read_bytes())["account_id"] != account
                    ):
                        raise ValueError("Transferred report scope or size")
                    os.replace(temp, target)
                    self.checked[target] = time.monotonic()
                except (subprocess.SubprocessError, OSError):
                    if not target.exists():
                        raise OSError("Study transfer unavailable") from None
            return target


def serve(config):
    tool = Path(__file__).resolve().parent
    server_file = tool / "serve-production-frontend.py"
    if not server_file.exists():
        server_file = tool.parents[1] / "scripts" / "serve-production-frontend.py"
    spec = importlib.util.spec_from_file_location("static_server", server_file)
    static = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(static)
    if config.get("viewing_pc"):
        copy = ViewingCopy(config)
        report_path = copy.read
        log_file = Path(config["runtime"]) / "dashboard.log"
    else:
        from track_imbalances import bootstrap

        cache, _ = bootstrap(config)
        from imbalance_study import directory

        def report_path(owner, account):
            return directory(cache, owner, account) / "report.json"

        log_file = cache / "imbalance-study-v1" / "dashboard.log"

    class Handler(static.TopSignalFrontendHandler):
        def _host_is_allowed(self):
            return self.headers.get("Host", "").lower() in {self.allowed_host, PUBLIC_HOST}

        def send_head(self):
            if not config.get("viewing_pc"):
                return super().send_head()
            try:
                response = dashboard_response(self.path, self.command)
            except error.HTTPError as exc:
                exc.close()
                if exc.code == 404 and re.fullmatch(r"/assets/[\w.-]+", urlsplit(self.path).path):
                    return super().send_head()  # Lazy imports from the former viewing copy.
                self.send_error(exc.code)
                return None
            except (ValueError, OSError):
                self.send_error(503)
                return None
            self.send_response(response.status)
            self.send_header("Content-Type", response.headers["Content-Type"])
            self.send_header("Content-Length", response.headers["Content-Length"])
            self.end_headers()
            return response

        def do_GET(self):
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                return super().do_GET()
            if not self._host_is_allowed():
                return self.send_error(421)
            match = ROUTE.fullmatch(path)
            if not match:
                return self.send_error(404)
            account = int(match[1])
            token = self.headers.get("Authorization", "")
            if not token.startswith("Bearer ") or not 7 < len(token) <= 8192:
                return self.send_error(401)
            opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

            def get(path):
                req = request.Request("http://127.0.0.1:8000" + path, headers={"Authorization": token})
                with opener.open(req, timeout=4) as response:
                    data = response.read(1024 * 1024 + 1)
                    if len(data) > 1024 * 1024:
                        raise ValueError("Large auth response")
                    return json.loads(data)

            try:
                owner = get("/api/auth/me")["user_id"]
                configs = get("/api/bots")["items"]
                if not any(c["account_id"] == account and c["id"] == config["config"] for c in configs):
                    return self.send_error(404)
                path = report_path(owner, account)
                with path.open("rb") as f:
                    data = f.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024 or json.loads(data)["account_id"] != account:
                    raise ValueError("Report scope or size")
            except error.HTTPError as exc:
                return self.send_error(exc.code if exc.code in (401, 403, 404) else 503)
            except FileNotFoundError:
                return self.send_error(404)
            except (KeyError, ValueError, OSError):
                return self.send_error(503)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    static.main(
        ["--directory", str(tool / "dist"), "--port", str(config["port"]), "--log-file", str(log_file)],
        Handler,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    serve(read_json(parser.parse_args().config))
