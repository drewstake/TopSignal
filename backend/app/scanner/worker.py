import argparse
import hashlib
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url

from . import ai, store as db
from .config import Options
from .prices import Bar, evaluate
from .sources import ISSUERS, fetch, parse, sources

LOG = logging.getLogger("scanner")
AUTHORITY = "b15899c2-5a5b-4352-aa5d-162c600188eb"


def retry_after(error, now):
    response = getattr(error, "response", None)
    if response is None:
        return 0
    raw = response.headers.get("Retry-After", "")
    try:
        return max(0, int(raw))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(raw).timestamp() - now)
        except (TypeError, ValueError, OverflowError):
            return 0


def category(error):
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"HTTP {error.response.status_code}"
    return "Connection or source data unavailable"


def discord_content(event):
    if event.kind == "move":
        # Stored titles also supply the detailed scanner feed and search context.
        move = re.match(
            r"^(\S+) ([+-])(\d+\.\d+)% in (\d+) min · (\d+\.\d+)× relative volume\b",
            event.title,
        )
        if move:
            ticker, sign, percent, minutes, volume = move.groups()
            direction, verb = ("Bullish", "rose") if sign == "+" else ("Bearish", "fell")
            unit = "minute" if minutes == "1" else "minutes"
            volume = volume.rstrip("0").rstrip(".")
            return (
                f"**{ticker} · {direction} move**\n"
                f"Price {verb} {percent}% in {minutes} {unit} on {volume}× normal volume.\n"
                f"{event.source} · <t:{int(event.received)}:t>"
                + (" · Delayed alert" if event.delayed else "")
            )
    seen = datetime.fromtimestamp(event.received, UTC).isoformat()
    return (
        f"{event.tickers.strip()} · {event.source}\n{event.title}\n{event.link}\nReceived {seen}"
        + (" · Delayed alert" if event.delayed else "")
    )


class Worker:
    def __init__(self, engine, config, clock=time.time):
        self.engine, self.cfg, self.clock = engine, config, clock
        self.sec_lock = threading.Lock()
        self.sec_next = 0.0
        self.raw_ready = threading.Event()
        self.feed_digests = {}

    def state(self, name):
        with self.engine.connect() as c:
            return (
                c.execute(select(db.health).where(db.health.c.name == name))
                .mappings()
                .first()
                or {}
            )

    def status(self, name, status, **values):
        with self.engine.begin() as c:
            db.source_state(c, name, status=status, **values)

    def ingest(self, source, items, received, previous, headers):
        baseline = previous.get("since") is None
        # Bounded first snapshot; every later poll overlaps the initial horizon.
        floor = received - 86400 if baseline else previous["since"]
        ordered = sorted(items, key=lambda i: i.published)
        with self.engine.begin() as c:
            for item in (
                i
                for index, i in enumerate(ordered)
                if i.published >= floor or baseline and index >= len(ordered) - 3
            ):
                db.headline(c, source, item, received, baseline)
            db.source_state(
                c,
                source.name,
                status="Healthy",
                success=received,
                since=floor,
                failures=0,
                next_check=received + source.interval,
                etag=headers.get("ETag"),
                modified=headers.get("Last-Modified"),
            )
        self.raw_ready.set()

    def sec_slot(self):
        with self.sec_lock:
            wait = self.sec_next - time.monotonic()
            if wait > 1:
                return wait
            if wait > 0:
                time.sleep(wait)
            self.sec_next = time.monotonic() + 0.4
        return 0

    def sec_document(self, url, limit):
        if not self.cfg.sec_user_agent or self.sec_slot():
            raise ValueError("SEC access deferred")
        try:
            return fetch(url, {"User-Agent": self.cfg.sec_user_agent}, limit)
        except requests.HTTPError as exc:
            if category(exc) in {"HTTP 403", "HTTP 429"}:
                with self.sec_lock:
                    delay = max(600, retry_after(exc, self.clock()))
                    self.sec_next = max(self.sec_next, time.monotonic() + delay)
            raise

    def lookup_article(self, url):
        if urlsplit(url).hostname == "www.sec.gov":
            return ai.article(url, self.sec_document)
        return ai.article(url)

    def poll(self, source):
        previous = self.state(source.name)
        now = self.clock()
        if previous.get("next_check", 0) and previous["next_check"] > now:
            return previous["next_check"] - now
        headers = {"User-Agent": "TopSignal public feed reader"}
        if source.kind == "sec":
            if not self.cfg.sec_user_agent:
                self.status(source.name, "SEC contact not configured")
                return 600
            headers["User-Agent"] = self.cfg.sec_user_agent
            wait = self.sec_slot()
            if wait:
                return wait
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("modified"):
            headers["If-Modified-Since"] = previous["modified"]
        try:
            status, returned, body = fetch(source.url, headers)
            received = self.clock()
            # The wire returns full bodies without conditional HTTP validators.
            digest = None if status == 304 else hashlib.sha256(body).digest()
            if previous.get("since") is not None and (
                status == 304 or digest == self.feed_digests.get(source.name)
            ):
                validators = (
                    {}
                    if status == 304
                    else {
                        "etag": returned.get("ETag"),
                        "modified": returned.get("Last-Modified"),
                    }
                )
                self.status(
                    source.name,
                    "Healthy",
                    success=received,
                    failures=0,
                    next_check=received + source.interval,
                    **validators,
                )
            else:
                self.ingest(
                    source,
                    parse(source, body, self.cfg.watchlist),
                    received,
                    previous,
                    returned,
                )
                self.feed_digests[source.name] = digest
            return source.interval
        except (
            requests.RequestException,
            ValueError,
            SyntaxError,
            KeyError,
            TypeError,
        ) as exc:
            count = (previous.get("failures") or 0) + 1
            delay = max(
                min(3600, source.interval * 2 ** min(count, 8)), retry_after(exc, now)
            )
            if source.kind == "sec" and category(exc) in {"HTTP 403", "HTTP 429"}:
                delay = max(delay, 600)
                with self.sec_lock:
                    self.sec_next = max(self.sec_next, time.monotonic() + delay)
            self.status(
                source.name, category(exc), failures=count, next_check=now + delay
            )
            return delay

    def detect(self, ticker, bars, sector, end, received, provider):
        stats = evaluate(bars, sector, end, received, self.cfg.rules)
        if stats and stats["qualifies"]:
            with self.engine.begin() as c:
                db.price_move(c, ticker, stats, received, self.cfg.rules, provider)
            self.raw_ready.set()
        return stats

    def prices(self):
        cfg = self.cfg
        if not (
            cfg.alpaca_key.get_secret_value() and cfg.alpaca_secret.get_secret_value()
        ):
            self.status("Equity prices", "Price feed not configured")
            return 600
        feed = cfg.alpaca_feed
        provider = (
            "Alpaca "
            + {
                "iex": "IEX (single exchange)",
                "sip": "SIP (consolidated)",
                "delayed_sip": "SIP (15-minute delay)",
            }[feed]
        )
        end = int(self.clock() // 60) * 60
        requested_end = end - (960 if feed == "delayed_sip" else 0)
        timestamp = lambda t: datetime.fromtimestamp(t, UTC).isoformat()
        params = {
            "symbols": ",".join(sorted(set([*cfg.watchlist, "SOXX"]))),
            "timeframe": "1Min",
            "feed": "sip" if feed == "delayed_sip" else feed,
            "sort": "asc",
            "limit": 10000,
            "start": timestamp(
                requested_end - (cfg.window_minutes + cfg.baseline_minutes + 2) * 60
            ),
            "end": timestamp(requested_end - 1),
            "adjustment": "raw",
        }
        try:
            r = requests.get(
                "https://data.alpaca.markets/v2/stocks/bars",
                params=params,
                headers={
                    "APCA-API-KEY-ID": cfg.alpaca_key.get_secret_value(),
                    "APCA-API-SECRET-KEY": cfg.alpaca_secret.get_secret_value(),
                },
                timeout=(4, 8),
                allow_redirects=False,
            )
            r.raise_for_status()
            received = self.clock()
            data = r.json()
            if data.get("next_page_token"):
                raise ValueError("Incomplete price page")
            bars = {
                t: {b.start: b for b in map(Bar.alpaca, rows)}
                for t, rows in data["bars"].items()
            }
            latest = [
                max(bars[t], default=0) + 60 if t in bars else 0 for t in cfg.watchlist
            ]
            age = received - min(latest)
            state = (
                "Stale"
                if age > 1020
                else "Delayed"
                if feed == "delayed_sip" or age > 75
                else "Live"
            )
            self.status(
                "Equity prices", f"{state} · {provider}", success=received, failures=0
            )
            if feed != "delayed_sip":
                for t in cfg.watchlist:
                    self.detect(
                        t,
                        bars.get(t, {}),
                        bars.get("SOXX", {}),
                        end,
                        received,
                        provider,
                    )
            return max(5, end + 65 - self.clock()) if state == "Live" else 15
        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            n = (self.state("Equity prices").get("failures") or 0) + 1
            self.status(
                "Equity prices",
                f"Disconnected · {provider} · {category(exc)}",
                failures=n,
            )
            return max(min(300, 5 * 2 ** min(n, 6)), retry_after(exc, self.clock()))

    def take(self, kinds):
        with self.engine.begin() as c:
            return db.claim(c, kinds, self.clock())

    def finish(self, job, state, reason=None, delay=0):
        with self.engine.begin() as c:
            db.finish(c, job, state, self.clock(), reason, delay)

    def deliver(self):
        work = self.take(["delivery"])
        if not work:
            return False
        job, e = work
        destination = self.cfg.discord_webhook.get_secret_value()
        if not destination:
            self.finish(job, "pending", "Notification destination not configured", 300)
            return True
        content = discord_content(e)
        try:
            r = requests.post(
                destination,
                params={"wait": "true"},
                timeout=(4, 8),
                allow_redirects=False,
                json={"content": content[:2000], "allowed_mentions": {"parse": []}},
            )
            if r.status_code == 429:
                self.finish(
                    job,
                    "pending",
                    "Rate limited",
                    max(2, retry_after(requests.HTTPError(response=r), self.clock())),
                )
            elif 200 <= r.status_code < 300:
                self.finish(job, "dispatched")
            elif r.status_code >= 500:
                self.finish(
                    job,
                    "pending",
                    f"HTTP {r.status_code}",
                    min(900, 5 * 2 ** min(job.attempts, 8)),
                )
            else:
                self.finish(job, "failed", f"HTTP {r.status_code}")
        except requests.ConnectionError as exc:
            # A connection refusal/DNS failure is safe to retry; an interrupted
            # response may already have created the remote message.
            refused = (
                isinstance(exc, requests.ConnectTimeout)
                or "NewConnectionError" in repr(exc)
                or "NameResolutionError" in repr(exc)
            )
            self.finish(
                job,
                "pending" if refused else "failed",
                "Connection failed" if refused else "Outcome unconfirmed",
                min(900, 5 * 2 ** min(job.attempts, 8)),
            )
        except requests.RequestException:
            self.finish(job, "failed", "Outcome unconfirmed")
        return True

    def enrich(self):
        work = self.take(["summary", "search"])
        if not work:
            return False
        job, e = work
        search = job.kind == "search"
        model = self.cfg.search_model if search else self.cfg.summary_model
        with self.engine.begin() as c:
            raw = (
                c.execute(
                    select(db.jobs).where(
                        db.jobs.c.event_id == e.id, db.jobs.c.kind == "delivery"
                    )
                )
                .mappings()
                .one()
            )
            if self.cfg.discord_webhook.get_secret_value() and (
                raw.attempts == 0 or raw.state == "working"
            ):
                db.finish(
                    c, job, "pending", self.clock(), "Waiting for raw dispatch", 1
                )
                return True
            if search and c.scalar(
                select(db.links.c.child).where(db.links.c.parent == e.id).limit(1)
            ):
                db.finish(
                    c, job, "dispatched", self.clock(), "Direct coverage received"
                )
                return True
            if not self.cfg.openai_key.get_secret_value():
                db.finish(
                    c, job, "pending", self.clock(), "OpenAI key not configured", 300
                )
                return True
            cycle = db.cycle_key(self.clock(), self.cfg.cycle_day)
            reservation = db.reserve(
                c,
                cycle,
                job.id,
                model,
                ai.bound(self.cfg, job.kind, model),
                int(self.cfg.budget_usd * 1e6),
            )
            if reservation is None:
                db.finish(
                    c,
                    job,
                    "pending",
                    self.clock(),
                    "AI paused · billing-cycle cap",
                    300,
                )
                return True
        try:
            payload, prefix = ai.request_body(e, job.kind, model, self.lookup_article)
            response = ai.respond(self.cfg, payload)
            cost, counts = ai.costs(response, self.cfg, model)
            with self.engine.begin() as c:
                db.settle(c, reservation, cost, counts)
            fields = ai.result(
                response, job.kind, e, prefix, self.clock(), self.lookup_article
            )
            with self.engine.begin() as c:
                if fields:
                    c.execute(
                        update(db.events).where(db.events.c.id == e.id).values(**fields)
                    )
                db.finish(
                    c,
                    job,
                    "dispatched",
                    self.clock(),
                    None if fields else "Cause unknown",
                )
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            # A rejected request has no completion. Ambiguous attempts retain
            # their full reservation, including across restart and cycle rollover.
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            if code in (400, 401, 403, 404, 429):
                with self.engine.begin() as c:
                    db.settle(c, reservation, 0, (0, 0, 0))
            retry = (
                not search or code == 429 or isinstance(exc, requests.ConnectTimeout)
            )
            self.finish(
                job,
                "pending" if retry else "failed",
                "AI unavailable · " + category(exc),
                max(
                    retry_after(exc, self.clock()),
                    min(3600, 30 * 2 ** min(job.attempts, 7)),
                ),
            )
        return True

    def run(self, lock):
        stop = threading.Event()

        def loop(name, action, queue=False):
            while not stop.is_set():
                try:
                    value = action()
                    delay = (0 if value else 1) if queue else value
                except Exception as exc:
                    LOG.error("%s unavailable (%s)", name, type(exc).__name__)
                    delay = 15
                if name == "delivery" and delay:
                    self.raw_ready.wait(delay)
                    self.raw_ready.clear()
                else:
                    stop.wait(delay)

        all_sources = list(sources(self.cfg.watchlist))
        with ThreadPoolExecutor(max_workers=len(all_sources) + 3) as pool:
            for source in all_sources:
                pool.submit(loop, source.name, lambda s=source: self.poll(s))
            pool.submit(loop, "delivery", self.deliver, True)
            pool.submit(loop, "AI", self.enrich, True)
            pool.submit(loop, "Equity prices", self.prices)
            try:
                while True:
                    lock.execute(text("SELECT 1"))
                    lock.commit()
                    self.status("Scanner worker", "Running", success=self.clock())
                    rate = self.cfg
                    with self.engine.connect() as c:
                        paused = c.scalar(
                            select(db.jobs.c.id)
                            .where(
                                db.jobs.c.state == "pending",
                                db.jobs.c.detail == "AI paused · billing-cycle cap",
                            )
                            .limit(1)
                        )
                    reason = (
                        "AI paused · billing-cycle cap"
                        if paused
                        else "Available"
                        if rate.openai_key.get_secret_value()
                        else "OpenAI key not configured"
                    )
                    self.status(
                        "AI",
                        f"{reason} · {rate.summary_model} summaries · {rate.search_model} searches · "
                        f"\u0024{rate.budget_usd:g} cap",
                        success=self.clock(),
                    )
                    stop.wait(30)
            finally:
                stop.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--retry-delivery",
        type=int,
        metavar="EVENT_ID",
        help="Retry a definitively rejected webhook after correcting its destination",
    )
    args = parser.parse_args()
    cfg = Options.load(args.config)
    ISSUERS.update(cfg.additional_issuers)

    ai.TRUSTED.update(
        urlsplit(issuer.releases).hostname for issuer in cfg.additional_issuers.values()
    )
    if any(
        os.environ.get(k)
        for k in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")
    ):
        raise ValueError("PostgreSQL routing overrides are not supported")
    engine = create_engine(
        make_url(cfg.database_url.get_secret_value()).set(
            drivername="postgresql+psycopg"
        ),
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=3,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=15000 -c lock_timeout=5000",
        },
    )
    with engine.connect() as lock:
        authority = lock.execute(
            text(
                "SELECT source_id,active FROM topsignal_local.authority WHERE singleton"
            )
        ).first()
        if not authority or str(authority[0]) != AUTHORITY or not authority[1]:
            raise RuntimeError("Local authority mismatch")
        if args.retry_delivery is not None:
            row = lock.execute(
                update(db.jobs)
                .where(
                    db.jobs.c.event_id == args.retry_delivery,
                    db.jobs.c.kind == "delivery",
                    db.jobs.c.state == "failed",
                    db.jobs.c.detail.like("HTTP 4%"),
                )
                .values(state="pending", due=0, detail=None)
            )
            if row.rowcount != 1:
                raise ValueError("No definitively rejected dispatch for this event")
            lock.commit()
            return
        if not lock.scalar(text("SELECT pg_try_advisory_lock(18752303)")):
            raise RuntimeError("Scanner already running")
        lock.commit()
        Worker(engine, cfg).run(lock)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(
            f"Scanner stopped ({type(error).__name__}); check protected configuration, schema and local authority."
        ) from None
