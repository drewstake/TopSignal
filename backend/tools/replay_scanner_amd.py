"""Replay public November 19 minute charts and publisher records into a disposable scanner."""

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape

import requests
from sqlalchemy import create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.scanner import store
from app.scanner.config import Options
from app.scanner.prices import Bar
from app.scanner.sources import parse, sources, stamp
from app.scanner.worker import Worker

START = int(stamp("2026-11-19T14:30:00Z"))
NEWS = stamp("2026-11-19T15:26:30Z")
WEB = stamp("2026-11-19T15:27:52Z")
TITLE = "AMD Has Its Own China Problem"
NEWS_URL = "https://newsletter.hntrbrk.com/p/amd-has-its-own-china-problem"
WEB_URL = "https://hntrbrk.com/investigations/amd-china"


def load_chart(root, ticker, download):
    path = root / (ticker + ".json")
    if download and not path.exists():
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker,
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1m", "period1": START, "period2": START + 77 * 60},
            timeout=15,
        )
        r.raise_for_status()
        root.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as f:
            f.write(r.content)
    result = json.loads(path.read_bytes())["chart"]["result"][0]
    if result["meta"]["symbol"] != ticker:
        raise ValueError("Incorrect replay symbol")
    q = result["indicators"]["quote"][0]
    return {
        t: Bar(t, o, c, v)
        for t, o, c, v in zip(
            result["timestamp"], q["open"], q["close"], q["volume"], strict=True
        )
        if None not in (o, c, v)
    }


def rss(title, url, uid, published):
    iso = datetime.fromtimestamp(published, UTC).isoformat()
    return f"<rss><channel><item><guid>{escape(uid)}</guid><title>{escape(title)}</title><link>{escape(url)}</link><pubDate>{iso}</pubDate></item></channel></rss>".encode()


def replay(root, download=False):
    amd, soxx = (load_chart(root, t, download) for t in ("AMD", "SOXX"))
    highlights, triggers, messages = {}, [], []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            messages.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))[
                    "content"
                ]
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    with (
        ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as server,
        TemporaryDirectory() as tmp,
    ):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        engine = create_engine(
            "sqlite:///" + Path(tmp, "scanner.db").as_posix(),
            execution_options={"schema_translate_map": {"topsignal_local": None}},
        )
        try:
            store.meta.create_all(engine)
            cfg = Options(
                database_url="postgresql://127.0.0.1/fixture",
                discord_webhook=f"http://127.0.0.1:{server.server_port}/alerts",
            )
            logical_now = START
            worker = Worker(engine, cfg, lambda: logical_now)
            newsletter, website = list(sources(["AMD"]))[:2]
            for s in (newsletter, website):
                worker.ingest(s, [], START, {}, {})
            schedule = [(end, "bar") for end in range(START + 60, START + 77 * 60, 60)]
            schedule += [(NEWS + 2, "newsletter"), (WEB + 3, "website")]
            for logical_now, kind in sorted(schedule):
                if kind == "bar":
                    data = worker.detect(
                        "AMD",
                        amd,
                        soxx,
                        logical_now,
                        logical_now,
                        "Yahoo Finance replay",
                    )
                    if data and data["qualifies"]:
                        triggers.append(data)
                    if logical_now == NEWS - 30:
                        highlights["10:25_bar"] = data
                        with engine.connect() as c:
                            assert not c.scalar(
                                select(store.events.c.id).where(
                                    store.events.c.kind == "headline"
                                )
                            )
                    continue
                s, url, pub, uid = (
                    (newsletter, NEWS_URL, NEWS, NEWS_URL)
                    if kind == "newsletter"
                    else (website, WEB_URL, WEB, "895b32e30ed67fc5da7ff17652be1850")
                )
                raw = rss(TITLE, url, uid, pub)
                previous = worker.state(s.name)
                arrived = time.perf_counter()
                worker.ingest(s, parse(s, raw, ["AMD"]), logical_now, previous, {})
                if kind == "newsletter":
                    assert worker.deliver()
                    highlights["receipt_to_dispatch_ms"] = round(
                        (time.perf_counter() - arrived) * 1000, 2
                    )
                    assert messages and TITLE in messages[-1]
                    with engine.connect() as c:
                        summary = (
                            c.execute(
                                select(store.jobs).where(store.jobs.c.kind == "summary")
                            )
                            .mappings()
                            .one()
                        )
                        assert summary.state == "pending" and summary.attempts == 0
                    worker.enrich()  # Missing OpenAI key; delivery already succeeded.
                else:
                    assert not worker.deliver()
            with engine.connect() as c:
                events = store.feed(c)
                original = next(e for e in events if e["source"] == newsletter.name)
                assert original["updates"][0]["link"] == WEB_URL
            return {
                **highlights,
                "threshold_crossings": triggers,
                "raw_messages": len(messages),
                "summary_state": original["jobs"]["summary"],
                "newsletter_simulated_receipt_utc": datetime.fromtimestamp(
                    NEWS + 2, UTC
                ).isoformat(),
                "website_simulated_receipt_utc": datetime.fromtimestamp(
                    WEB + 3, UTC
                ).isoformat(),
                "publisher_to_simulated_collector_seconds": [2, 3],
                "dispatch_clock": "loopback wall time from simulated newsletter receipt",
                "10:45_close": amd[START + 75 * 60].close,
                "10:25_chart_open": amd[START + 55 * 60].open,
                "10:25_volume": amd[START + 55 * 60].volume,
            }
        finally:
            engine.dispose()
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(
            os.getenv("TOPSIGNAL_DATABENTO_CACHE_DIR")
            or Path(__file__).resolve().parents[1] / "storage/databento"
        ),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            replay(args.cache_dir / "scanner-replay/2026-11-19", args.download),
            indent=2,
        )
    )
