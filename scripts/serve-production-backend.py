"""Single-worker Uvicorn with a local, ACL-protected graceful shutdown signal."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from pathlib import Path
import sys


def stop_requested(*paths: Path | None) -> bool:
    for path in paths:
        if path is None:
            continue
        try:
            path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            # Losing access to the operator's local control state is unsafe.
            return True
        return True
    return False


async def serve_until_stopped(server, shutdown_file: Path, poll_seconds: float = 0.5, stop_file: Path | None = None) -> None:
    async def watch_shutdown() -> None:
        while not server.should_exit:
            if stop_requested(shutdown_file, stop_file):
                server.should_exit = True
                return
            await asyncio.sleep(poll_seconds)

    if stop_requested(shutdown_file, stop_file):
        return
    watcher = asyncio.create_task(watch_shutdown())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--log-config", required=True)
    parser.add_argument("--shutdown-file", required=True)
    parser.add_argument("--stop-file", required=True)
    args = parser.parse_args()
    if args.host != "127.0.0.1" or args.workers != 1 or not 1 <= args.port <= 65535:
        parser.error("Production requires loopback, one worker, and a valid port")
    sys.path.insert(0, str(Path(args.app_dir).resolve()))
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", host=args.host, port=args.port, workers=1,
        log_config=args.log_config, timeout_graceful_shutdown=30,
        limit_concurrency=100, timeout_keep_alive=5,
    ))
    asyncio.run(serve_until_stopped(server, Path(args.shutdown_file), stop_file=Path(args.stop_file)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
