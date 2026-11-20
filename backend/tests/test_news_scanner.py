import threading
from types import SimpleNamespace

import pytest
import requests
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.scanner import ai, routes, store
from app.scanner.config import Options
from app.scanner.prices import Bar, Rules, evaluate
from app.scanner.sources import Item, Source, matches, parse, sources
from app.scanner.worker import Worker
from tools.replay_scanner_amd import NEWS, NEWS_URL, TITLE, WEB, WEB_URL, rss


@pytest.fixture
def scanner(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "test.db").as_posix(),
        execution_options={"schema_translate_map": {"topsignal_local": None}},
    )
    store.meta.create_all(engine)
    now = [NEWS - 1800]
    cfg = Options(
        database_url="postgresql://127.0.0.1/test",
        discord_webhook="http://127.0.0.1:1234/alerts",
        openai_key="fixture",
        watchlist=["AMD", "TSM"],
    )
    worker = Worker(engine, cfg, lambda: now[0])
    yield worker, now
    engine.dispose()


def test_reconnect_restart_dispatch_and_related_coverage(scanner, monkeypatch):
    w, now = scanner
    s, site = list(sources(["AMD"]))[:2]
    rows = [
        rss("Old AMD release", NEWS_URL + "/old", "old", NEWS - 3600),
        rss(TITLE, NEWS_URL, "new", NEWS),
    ]
    monkeypatch.setattr(
        "app.scanner.worker.fetch", lambda *args: (200, {"ETag": "initial"}, rows[0])
    )
    w.poll(s)
    w.ingest(site, [], now[0], {}, {})
    assert not w.deliver()
    now[0] = NEWS + 600

    def unreachable(*args):
        raise requests.ConnectTimeout()

    monkeypatch.setattr("app.scanner.worker.fetch", unreachable)
    assert w.poll(s) >= 60
    assert w.state(s.name)["status"] != "Healthy"
    now[0] += 120

    def reconnected(url, headers):
        assert headers["If-None-Match"] in ("initial", "new")
        return 200, {"ETag": "new"}, rows[1]

    monkeypatch.setattr("app.scanner.worker.fetch", reconnected)
    w = Worker(w.engine, w.cfg, lambda: now[0])
    w.poll(s)
    messages = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kw: (
            messages.append(kw["json"]) or SimpleNamespace(status_code=204)
        ),
    )
    assert (
        w.deliver() and len(messages) == 1 and "Delayed alert" in messages[0]["content"]
    )
    with w.engine.connect() as c:
        e = store.feed(c)[0]
        assert (
            e["published"] == NEWS and e["received"] == now[0] and e["summary"] is None
        )

    def unavailable(*args):
        raise requests.ConnectTimeout()

    monkeypatch.setattr(ai, "respond", unavailable)
    monkeypatch.setattr(ai, "article", lambda u: ("", None))
    assert w.enrich()
    now[0] += 600
    w.poll(s)  # Duplicate after restart; summary retries independently.
    monkeypatch.setattr(
        ai,
        "respond",
        lambda cfg, p: {
            "status": "completed",
            "usage": {"input_tokens": 250, "output_tokens": 30},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Hunterbrook published an investigation.",
                        }
                    ],
                }
            ],
        },
    )
    w.enrich()
    w.ingest(
        site,
        parse(site, rss(TITLE, WEB_URL, "website", WEB), ["AMD"]),
        now[0],
        w.state(site.name),
        {},
    )
    assert not w.deliver()
    with w.engine.connect() as c:
        e = next(e for e in store.feed(c) if e["link"] == NEWS_URL)
        assert (
            e["summary"].startswith("Headline only.")
            and e["updates"][0]["relation"] == "Related coverage"
        )
        assert e["jobs"]["delivery"]["state"] == "dispatched" and len(messages) == 1
        assert c.scalar(select(func.count()).select_from(store.usage)) == 2


def sample(end):
    return {
        end - i * 60: Bar(
            end - i * 60, 100, 98 if i == 1 else 100, 250 if i == 1 else 100
        )
        for i in range(1, 22)
    }


@pytest.mark.parametrize(
    ("percent", "minutes", "headline", "description"),
    [
        (-2.26, 1, "Bearish move", "Price fell 2.26% in 1 minute"),
        (1.35, 5, "Bullish move", "Price rose 1.35% in 5 minutes"),
    ],
)
def test_discord_price_move(
    scanner, monkeypatch, percent, minutes, headline, description
):
    w, now = scanner
    stats = dict(
        qualifies=True,
        start=now[0] - 60 * minutes,
        minutes=minutes,
        percent=percent,
        rvol=2.4,
        sector_percent=0.06,
    )
    provider = "Alpaca IEX (single exchange)"
    with w.engine.begin() as c:
        store.price_move(c, "AVGO", stats, now[0], Rules(), provider)
    messages = []

    def post(url, **kwargs):
        messages.append(kwargs["json"]["content"])
        return SimpleNamespace(status_code=204)

    monkeypatch.setattr(requests, "post", post)
    assert w.deliver()
    assert messages == [
        f"**AVGO · {headline}**\n{description} on 2.4× normal volume.\n"
        f"{provider} · <t:{int(now[0])}:t>"
    ]


def test_detector_signed_moves_window_gaps_and_cooldown(scanner):
    w, now = scanner
    end = int(NEWS - 30)
    bars = sample(end)
    rules = Rules()
    s = evaluate(bars, {}, end, end, rules)
    assert s["qualifies"] and s["rvol"] == 2.5 and s["sector_percent"] is None
    bars[end - 60] = Bar(end - 60, 100, 102, 250)
    assert evaluate(bars, bars, end, end, rules)["percent"] > 0
    assert evaluate(bars, bars, end, end - 1, rules) is None
    assert evaluate(bars, bars, end, end + 76, rules) is None
    with w.engine.begin() as c:
        assert store.price_move(c, "AMD", s, end, rules, "test")
        assert not store.price_move(c, "AMD", s, end + 5, rules, "test")
        assert (
            c.scalar(
                select(func.count())
                .select_from(store.jobs)
                .where(store.jobs.c.kind == "search")
            )
            == 1
        )
    del bars[end - 5 * 60]
    assert evaluate(bars, {}, end, end, rules) is None


def test_inflight_search_does_not_hold_up_direct_coverage(scanner, monkeypatch):
    w, now = scanner
    now[0] = int(NEWS - 30)
    s = list(sources(["AMD"]))[0]
    w.ingest(s, [], now[0], {}, {})
    w.detect("AMD", sample(now[0]), {}, now[0], now[0], "test")
    monkeypatch.setattr(
        requests, "post", lambda *args, **kwargs: SimpleNamespace(status_code=204)
    )
    w.deliver()
    started, release = threading.Event(), threading.Event()
    payloads = []

    def wait_response(cfg, payload):
        payloads.append(payload)
        started.set()
        assert release.wait(5)
        return {
            "status": "completed",
            "usage": {"input_tokens": 30, "output_tokens": 2},
            "output": [
                {"type": "web_search_call", "action": {"sources": []}},
                {"type": "message", "content": [{"type": "output_text", "text": "{}"}]},
            ],
        }

    monkeypatch.setattr(ai, "respond", wait_response)
    t = threading.Thread(target=w.enrich)
    t.start()
    try:
        assert started.wait(3)
        now[0] = NEWS + 2
        w.ingest(
            s,
            parse(s, rss(TITLE, NEWS_URL, "newsletter", NEWS), ["AMD"]),
            now[0],
            w.state(s.name),
            {},
        )
        assert w.deliver()
        with w.engine.connect() as c:
            event = next(e for e in store.feed(c) if e["kind"] == "move")
            assert event["updates"][0]["relation"] == "Reported afterward"
            assert event["cause"] == "Cause unknown"
    finally:
        release.set()
        t.join(5)
    assert (
        len(payloads) == 1
        and payloads[0]["model"] == "gpt-6-luna"
        and payloads[0]["max_tool_calls"] == 1
    )


def test_cap_prevents_concurrent_reservations_and_raw_continues(scanner, monkeypatch):
    w, now = scanner
    s = list(sources(["AMD"]))[0]
    with w.engine.begin() as c:
        store.headline(c, s, Item("1", "AMD update", NEWS_URL, NEWS, ("AMD",)), NEWS)
    w.cfg.budget_usd = 0
    monkeypatch.setattr(
        requests, "post", lambda *args, **kwargs: SimpleNamespace(status_code=204)
    )
    monkeypatch.setattr(ai, "respond", lambda *a: pytest.fail("Cap must block request"))
    w.deliver()
    w.enrich()
    with w.engine.connect() as c:
        e = store.feed(c)[0]
        assert (
            "cap" in e["jobs"]["summary"]["detail"]
            and e["jobs"]["delivery"]["state"] == "dispatched"
        )
    barrier = threading.Barrier(2)
    ids = []

    def reserve():
        barrier.wait(5)
        with w.engine.begin() as c:
            ids.append(store.reserve(c, "cycle", 1, "test", 7, 10))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(ids) == 2 and ids.count(None) == 1


def test_identity_routine_filing_and_no_credentials(scanner):
    w, _ = scanner
    assert not matches("TSM esports results", "TSM")
    assert matches("NYSE: TSM responds", "TSM") and matches(
        "TSMC monthly revenue", "TSM"
    )
    assert matches("Advanced Micro Devices reports", "AMD")
    w.prices()
    assert w.state("Equity prices")["status"] == "Price feed not configured"
    import json

    data = {
        "cik": 1046179,
        "filings": {
            "recent": {
                "accessionNumber": ["x", "y"],
                "form": ["6-K", "4"],
                "acceptanceDateTime": ["2026-11-19T15:25:00Z"] * 2,
                "primaryDocument": ["report.htm"] * 2,
                "primaryDocDescription": ["", ""],
            }
        },
    }
    src = next(s for s in sources(["TSM"]) if s.kind == "sec")
    items = list(parse(src, json.dumps(data), ["TSM"]))
    assert [i.urgent for i in items] == [True, False]
    with w.engine.begin() as c:
        for i in items:
            store.headline(c, src, i, NEWS)
        assert sum(e["urgent"] for e in store.feed(c)) == 1
    with Session(w.engine) as session:
        assert not routes.scanner_feed(session)["events"]


def test_search_requires_verified_timestamp_quote_and_source(scanner, monkeypatch):
    event = SimpleNamespace(tickers=" AMD ", move_start=NEWS - 90, received=NEWS - 30)
    quote = "AMD disclosed a change to its business guidance."

    def result(date):
        monkeypatch.setattr(ai, "article", lambda url: (quote, date))
        import json

        return ai.result(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "web_search_call",
                        "action": {"sources": [{"url": NEWS_URL}]},
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "url": NEWS_URL,
                                        "quote": quote,
                                        "explanation": "The issuer revised its guidance.",
                                    }
                                ),
                            }
                        ],
                    },
                ],
            },
            "search",
            event,
            "",
            NEWS + 120,
        )

    assert not result(NEWS - 7200)
    assert "Reported afterward" in result(NEWS)["cause"]
    assert "Known when the move began" in result(NEWS - 100)["cause"]


def test_delivery_timeout_not_duplicated_on_restart(scanner):
    w, now = scanner
    with w.engine.begin() as c:
        store.headline(
            c,
            list(sources(["AMD"]))[0],
            Item("x", "AMD", NEWS_URL, NEWS, ("AMD",)),
            NEWS,
        )
        assert store.claim(c, ["delivery"], now[0])
    now[0] += 301
    assert not w.deliver()
    with w.engine.connect() as c:
        assert store.feed(c)[0]["jobs"]["delivery"]["detail"] == "Outcome unconfirmed"


def test_sec_followup_uses_same_identified_access_and_cooldown(scanner, monkeypatch):
    w, _ = scanner
    calls = []

    def limited(url, headers, limit):
        calls.append(headers["User-Agent"])
        response = requests.Response()
        response.status_code = 429
        raise requests.HTTPError(response=response)

    monkeypatch.setattr("app.scanner.worker.fetch", limited)
    url = "https://www.sec.gov/Archives/edgar/data/2488/filing.htm"
    assert w.lookup_article(url) == ("", None)
    w.cfg.sec_user_agent = "TopSignal contact@example.org"
    assert w.lookup_article(url) == ("", None)
    assert w.sec_slot() > 590
    assert w.lookup_article(url) == ("", None)
    assert calls == [w.cfg.sec_user_agent]


def test_issuer_response_and_site_correction_attach_to_original_newsletter(scanner):
    w, _ = scanner
    newsletter, website, issuer = list(sources(["AMD"]))[:3]
    with w.engine.begin() as c:
        original = store.headline(
            c, newsletter, Item("n", TITLE, NEWS_URL, NEWS, ("AMD",)), NEWS + 2
        )
        store.headline(c, website, Item("s", TITLE, WEB_URL, WEB, ("AMD",)), WEB + 3)
        store.headline(
            c,
            website,
            Item("s", TITLE + " (corrected)", WEB_URL, WEB, ("AMD",)),
            WEB + 60,
        )
        store.headline(
            c,
            issuer,
            Item(
                "r",
                "AMD responds to Hunterbrook",
                "https://ir.amd.com/r",
                WEB + 120,
                ("AMD",),
            ),
            WEB + 122,
        )
        story = next(e for e in store.feed(c) if e["id"] == original)
        assert [u["relation"] for u in story["updates"]] == [
            "Related coverage",
            "Publisher correction",
            "Company response",
        ]
        assert (story["title"], story["published"], story["received"]) == (
            TITLE,
            NEWS,
            NEWS + 2,
        )
