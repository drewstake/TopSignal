"""Bounded, fixed-endpoint official feeds and optional licensed calendar access.

BLS: https://www.bls.gov/help/hlpiCAL.htm
Fed: https://www.federalreserve.gov/feeds/feeds.htm and /monetarypolicy/fomccalendars.htm
TE timestamps/fields: https://docs.tradingeconomics.com/economic_calendar/schema/
No source timestamp is evidence that this application knew a value in the past.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
import os
import re
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

UTC = timezone.utc
EASTERN = ZoneInfo("America/New_York")
MAX_BYTES = 2_000_000
MAX_EVENTS = 3000
BLS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FED_NEWS_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
SOURCES = {
    "bls": {"label": "BLS release calendar", "scope": "BLS releases only", "ttl_seconds": 86400},
    "federal_reserve": {"label": "Federal Reserve news", "scope": "Federal Reserve press releases only", "ttl_seconds": 3600},
    "federal_reserve_calendar": {"label": "Federal Reserve meeting calendar", "scope": "FOMC meeting dates only", "ttl_seconds": 86400},
    "trading_economics": {"label": "Trading Economics US calendar", "scope": "US macro calendar", "ttl_seconds": 900},
}


class ProviderError(RuntimeError):
    """Only fixed safe error codes cross the provider boundary."""


def utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)  # SQLite loses offset; stored values are UTC.
    return value.astimezone(UTC)


def plain(value, limit=500):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", "", str(value if value is not None else "")))).strip()[:limit]


def safe_url(value):
    value = str(value or "").strip()
    parsed = urlparse(value)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.hostname not in {"www.bls.gov", "www.federalreserve.gov", "tradingeconomics.com", "www.tradingeconomics.com"}
            or parsed.query or parsed.fragment or len(value) > 1000):
        return None
    return value


def configured(source):
    return source != "trading_economics" or bool(os.getenv("TRADING_ECONOMICS_API_KEY", "").strip())


@dataclass
class FeedBatch:
    events: list[dict] = field(default_factory=list)
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    coverage_complete: bool = False


def _event(identity, title, kind="calendar", **values):
    return dict(source_event_id=plain(identity, 500), title=plain(title), kind=kind,
                country="United States", importance="unknown", scheduled_at=None,
                scheduled_end_at=None, scheduled_date=None, time_precision="exact",
                published_at=None, state="scheduled" if kind == "calendar" else "news",
                actual=None, forecast=None, previous=None, revised=None, url=None,
                raw_fields={}, **values)


def _build_event(identity, title, kind="calendar", **values):
    result = _event(identity, title, kind)
    result.update(values)
    if not result["source_event_id"] or not result["title"]:
        raise ProviderError("invalid_event")
    return result


def _importance(title):
    return "high" if any(term in title.lower() for term in (
        "consumer price", "employment situation", "producer price", "employment cost", "fomc", "federal funds", "monetary policy")) else "unknown"


def _bounded_text(body):
    if len(body) > MAX_BYTES:
        raise ProviderError("response_too_large")
    try:
        return body.decode("utf-8-sig")
    except UnicodeError:
        raise ProviderError("invalid_encoding") from None


def _ical_datetime(key, value):
    if "VALUE=DATE" in key or re.fullmatch(r"\d{8}", value):
        day = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(day, time.min, EASTERN).astimezone(UTC), "date"
    zone = UTC if value.endswith("Z") else EASTERN
    if "TZID=" in key:
        name = key.split("TZID=", 1)[1].split(";", 1)[0].strip('"')
        if name not in {"America/New_York", "US/Eastern", "Eastern Standard Time", "UTC", "Etc/UTC"}:
            raise ProviderError("unsupported_timezone")
        zone = UTC if name in {"UTC", "Etc/UTC"} else EASTERN
    value = value.removesuffix("Z")
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    local = naive.replace(tzinfo=zone)
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        raise ProviderError("invalid_local_time")
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ProviderError("ambiguous_local_time")
    return local.astimezone(UTC), "exact"


def parse_bls(body, observed_at):
    text = re.sub(r"\r?\n[ \t]", "", _bounded_text(body))
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ProviderError("invalid_calendar")
    blocks = re.findall(r"BEGIN:VEVENT\s*(.*?)END:VEVENT", text, re.S)
    if not blocks or len(blocks) > MAX_EVENTS:
        raise ProviderError("invalid_event_count")
    events = []
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key.split(";", 1)[0]] = (key, value.strip())
        if "RRULE" in fields:
            raise ProviderError("unsupported_recurrence")
        if "DTSTART" not in fields or "SUMMARY" not in fields:
            raise ProviderError("invalid_calendar_event")
        try:
            start, precision = _ical_datetime(*fields["DTSTART"])
        except ValueError:
            raise ProviderError("invalid_calendar_date") from None
        title = fields["SUMMARY"][1].replace("\\,", ",").replace("\\n", " ")
        uid = fields.get("UID", (None, hashlib.sha256((title + start.isoformat()).encode()).hexdigest()))[1]
        state = "cancelled" if fields.get("STATUS", (None, ""))[1] == "CANCELLED" else "scheduled"
        events.append(_build_event(uid, title, scheduled_at=start,
            scheduled_date=start.astimezone(EASTERN).date().isoformat(), time_precision=precision,
            scheduled_end_at=(datetime.combine(start.astimezone(EASTERN).date() + timedelta(days=1), time.min, EASTERN).astimezone(UTC)
                              if precision == "date" else None),
            importance=_importance(title), state=state,
            url=safe_url(fields.get("URL", (None, ""))[1]) or "https://www.bls.gov/schedule/",
            raw_fields={"uid": plain(uid), "calendar_kind": "schedule_only"}))
    times = [item["scheduled_at"] for item in events]
    return FeedBatch(events, min(times), max(times) + timedelta(days=1), False)


def parse_fed_news(body, observed_at):
    text = _bounded_text(body)
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ProviderError("unsafe_xml")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise ProviderError("invalid_news_xml") from None
    items = root.findall("./channel/item")
    if not items or len(items) > MAX_EVENTS:
        raise ProviderError("invalid_event_count")
    events = []
    for item in items:
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", ""))
            if published.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise ProviderError("invalid_publication_time") from None
        link = safe_url(item.findtext("link"))
        title = plain(item.findtext("title"))
        events.append(_build_event(item.findtext("guid") or link, title, "news",
            published_at=utc(published), importance=_importance(title + " " + item.findtext("category", "")),
            url=link, raw_fields={"category": plain(item.findtext("category")),
                                  "summary": plain(item.findtext("description"), 1000)}))
    return FeedBatch(events)


def parse_fed_calendar(body, observed_at):
    text = _bounded_text(body)
    sections = list(re.finditer(r"(20\d{2}) FOMC Meetings", text))
    events = []
    months = {name: number for number, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
    for index, match in enumerate(sections):
        year = int(match.group(1))
        section = text[match.end(): sections[index + 1].start() if index + 1 < len(sections) else len(text)]
        pairs = re.findall(r'class="[^\"]*\bfomc-meeting__month[^\"]*"[^>]*>(.*?)</div>\s*<div[^>]*class="[^\"]*\bfomc-meeting__date[^\"]*"[^>]*>(.*?)</div>', section, re.S)
        if len(pairs) != section.count("fomc-meeting__month"):
            raise ProviderError("incomplete_fomc_calendar")
        for month_text, day_text in pairs:
            month_parts = plain(month_text).lower().split("/")
            days = re.match(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?", plain(day_text))
            if not days or any(part[:3] not in months for part in month_parts):
                raise ProviderError("invalid_fomc_date")
            try:
                start = datetime(year, months[month_parts[0][:3]], int(days[1]), tzinfo=EASTERN)
                final = datetime(year, months[month_parts[-1][:3]], int(days[2] or days[1]), tzinfo=EASTERN)
            except ValueError:
                raise ProviderError("invalid_fomc_date") from None
            if final < start:
                raise ProviderError("invalid_fomc_date")
            events.append(_build_event("fomc-" + start.date().isoformat(), "FOMC meeting — announcement time not supplied",
                scheduled_at=utc(start), scheduled_end_at=utc(final + timedelta(days=1)),
                scheduled_date=start.date().isoformat(), time_precision="date", importance="high",
                url=FED_CALENDAR_URL, raw_fields={"meeting_dates": plain(month_text) + " " + plain(day_text), "time_not_published": True}))
    if not events or len(events) > MAX_EVENTS:
        raise ProviderError("invalid_fomc_calendar")
    return FeedBatch(events, min(e["scheduled_at"] for e in events), max(e["scheduled_end_at"] for e in events), False)


def parse_trading_economics(body, observed_at, start, end):
    try:
        rows = json.loads(_bounded_text(body))
    except (ValueError, TypeError):
        raise ProviderError("invalid_calendar_json") from None
    if not isinstance(rows, list) or not rows or len(rows) >= MAX_EVENTS:
        raise ProviderError("invalid_event_count")
    events = []
    complete = True
    for row in rows:
        if not isinstance(row, dict) or row.get("Country") != "United States":
            raise ProviderError("unexpected_calendar_scope")
        try:
            scheduled = utc(datetime.fromisoformat(str(row["Date"]).replace("Z", "+00:00")))
        except (KeyError, ValueError):
            raise ProviderError("invalid_calendar_date") from None
        precision = "exact" if str(row.get("DateSpan", "0")) == "0" else "date"
        complete = complete and precision == "exact" and str(row.get("Importance")) in {"1", "2", "3"}
        if not start <= scheduled < end:
            raise ProviderError("unexpected_calendar_window")
        if row.get("LastUpdate"):
            try:
                updated = utc(datetime.fromisoformat(str(row["LastUpdate"]).replace("Z", "+00:00")))
            except ValueError:
                raise ProviderError("invalid_update_time") from None
            if updated > observed_at:
                raise ProviderError("provider_clock_ahead")
        actual = plain(row.get("Actual"), 150) or None
        actual = actual if scheduled <= observed_at and precision == "exact" else None
        events.append(_build_event(row.get("CalendarId") or row.get("CalendarID"), row.get("Event"),
            scheduled_at=scheduled, scheduled_date=scheduled.date().isoformat(), time_precision=precision,
            scheduled_end_at=scheduled + timedelta(days=1) if precision == "date" else None,
            importance={"1": "low", "2": "medium", "3": "high"}.get(str(row.get("Importance")), "unknown"),
            state="released" if actual is not None else "scheduled", actual=actual,
            forecast=plain(row.get("Forecast"), 150) or None,
            previous=plain(row.get("Previous"), 150) or None,
            revised=plain(row.get("Revised"), 150) or None if scheduled <= observed_at else None,
            url=safe_url("https://tradingeconomics.com" + str(row.get("URL", ""))),
            raw_fields={key: plain(row.get(key), 200) for key in ("Reference", "Unit", "Category", "Source", "LastUpdate")}))
    return FeedBatch(events, start, end, complete)


def collect(source, observed_at, *, session=None):
    """Only these fixed public endpoints can be requested; redirects are rejected."""
    if source not in SOURCES:
        raise ProviderError("unknown_source")
    if not configured(source):
        raise ProviderError("not_configured")
    start = datetime.combine(observed_at.date() - timedelta(days=1), time.min, UTC)
    end = datetime.combine(observed_at.date() + timedelta(days=8), time.min, UTC)
    params = None
    url = {"bls": BLS_URL, "federal_reserve": FED_NEWS_URL, "federal_reserve_calendar": FED_CALENDAR_URL}.get(source)
    if source == "trading_economics":
        url = "https://api.tradingeconomics.com/calendar/country/united%20states/" + start.date().isoformat() + "/" + (end.date() - timedelta(days=1)).isoformat()
        params = {"c": os.environ["TRADING_ECONOMICS_API_KEY"].strip(), "f": "json"}
    client = session or requests.Session()
    try:
        with client.get(url, params=params, timeout=(5, 12), allow_redirects=False, stream=True,
                        headers={"User-Agent": "TopSignal/1.0 market-calendar", "Accept": "text/calendar, application/rss+xml, application/json, text/html"}) as response:
            if response.status_code != 200:
                raise ProviderError("provider_http_" + str(int(response.status_code)))
            payload = bytearray()
            for chunk in response.iter_content(65536):
                payload.extend(chunk)
                if len(payload) > MAX_BYTES:
                    raise ProviderError("response_too_large")
        body = bytes(payload)
        received_at = max(utc(observed_at), datetime.now(UTC))
        if source == "bls":
            return parse_bls(body, observed_at)
        if source == "federal_reserve":
            return parse_fed_news(body, observed_at)
        if source == "federal_reserve_calendar":
            return parse_fed_calendar(body, observed_at)
        return parse_trading_economics(body, received_at, start, end)
    except requests.Timeout:
        raise ProviderError("provider_timeout") from None
    except requests.RequestException:
        raise ProviderError("provider_connection_error") from None
    finally:
        if session is None:
            client.close()
