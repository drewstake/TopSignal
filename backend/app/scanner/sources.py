"""Public source identities, bounded retrieval and deterministic matching."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import requests


@dataclass(frozen=True)
class Issuer:
    name: str
    aliases: tuple[str, ...]
    cik: str
    releases: str


ISSUERS = {
    "AMD": Issuer(
        "Advanced Micro Devices",
        ("AMD",),
        "0000002488",
        "https://ir.amd.com/news-events/press-releases/rss",
    ),
    "NVDA": Issuer(
        "NVIDIA",
        ("Nvidia",),
        "0001045810",
        "https://nvidianews.nvidia.com/cats/press_release.xml",
    ),
    "AVGO": Issuer(
        "Broadcom",
        ("Broadcom Inc",),
        "0001730168",
        "https://www.broadcom.com/api/news/newsroom?parent=bltf3a3dd90a030e144&source_mode=Both&category=Financial&locale=en-us&microsite=broadcom",
    ),
    "INTC": Issuer(
        "Intel",
        ("Intel Corporation",),
        "0000050863",
        "https://www.intc.com/news-events/press-releases/rss",
    ),
    "TSM": Issuer(
        "Taiwan Semiconductor Manufacturing",
        ("TSMC", "Taiwan Semiconductor"),
        "0001046179",
        "https://pr.tsmc.com/english/latest-news",
    ),
}


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    interval: int
    kind: str = "rss"
    ticker: str = ""


NVIDIA_WIRE = Source(
    "NVDA GlobeNewswire",
    "https://rss.globenewswire.com/rssfeed/organization/Z9ZspHFg7E0Lf8obA4Udeg==",
    30,
    ticker="NVDA",
)
NVIDIA_NEWSROOM = Source(
    "NVDA investor relations", ISSUERS["NVDA"].releases, 30, ticker="NVDA"
)


def sources(watch):
    yield Source("Hunterbrook newsletter", "https://newsletter.hntrbrk.com/feed", 30)
    yield Source("Hunterbrook website", "https://hntrbrk.com/feed", 60)
    for ticker in watch:
        issuer = ISSUERS[ticker]
        if ticker == "NVDA":
            yield NVIDIA_NEWSROOM
            yield NVIDIA_WIRE
        else:
            yield Source(
                f"{ticker} investor relations",
                issuer.releases,
                120,
                {"TSM": "tsmc", "AVGO": "broadcom"}.get(ticker, "rss"),
                ticker,
            )
        if ticker == "AVGO":
            yield Source(
                "AVGO product releases",
                "https://www.broadcom.com/api/news/productnews?id=bltce3a61fa876974ff&type=news_category&locale=en-us&years=10&microsite=broadcom",
                300,
                "broadcom",
                ticker,
            )
        yield Source(
            f"{ticker} EDGAR",
            f"https://data.sec.gov/submissions/CIK{issuer.cik}.json",
            90,
            "sec",
            ticker,
        )


@dataclass(frozen=True)
class Item:
    uid: str
    title: str
    link: str
    published: float
    tickers: tuple[str, ...]
    excerpt: str = ""
    urgent: bool = True


def canonical(url):
    try:
        p = urlsplit(url.strip())
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.port not in (None, 443)
        ):
            return ""
        # Preserve article-identifying query parameters.
        query = "&".join(
            k
            for k in p.query.split("&")
            if not k.lower().startswith(("utm_", "fbclid="))
        )
        return urlunsplit(("https", p.hostname.lower(), p.path.rstrip("/"), query, ""))[
            :2000
        ]
    except ValueError:
        return ""


def stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        dt = parsedate_to_datetime(value)
    if not dt or dt.utcoffset() is None:
        raise ValueError("Missing publication timezone")
    return dt.astimezone(UTC).timestamp()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = False

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(html):
    parser = PlainText()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def matches(text, ticker):
    issuer = ISSUERS[ticker]
    names = "|".join(re.escape(n) for n in (issuer.name, *issuer.aliases))
    # Short symbols require an exchange or cashtag unless an issuer name is present.
    return bool(
        re.search(rf"\b(?:{names})\b", text, re.I)
        or re.search(rf"(?:\$|NASDAQ:\s*|NYSE:\s*){ticker}\b", text, re.I)
        or (ticker != "TSM" and re.search(rf"\b{ticker}\b", text))
    )


def parse(source, body, watch):
    if source.kind == "broadcom":
        rows = json.loads(body)
        if isinstance(rows, dict):
            rows = [
                item
                for group in rows["news_categories"]
                for year, entries in group.items()
                if year.isdigit()
                for item in entries
            ]
        if not isinstance(rows, list) or not rows:
            raise ValueError("Broadcom release list unavailable")
        for row in rows:
            if row.get("content_type") != "press_release":
                continue
            path = row["url"]
            if not path.startswith("/company/news/"):
                continue
            yield Item(
                str(row["content_id"]),
                plain(row["title"]),
                "https://www.broadcom.com" + path,
                stamp(row["publish_date"]),
                ("AVGO",),
                plain(row.get("summary") or "")[:2600],
            )
        return
    if source.kind == "sec":
        info = json.loads(body)
        if int(info["cik"]) != int(ISSUERS[source.ticker].cik):
            raise ValueError("Wrong SEC issuer")
        recent = info["filings"]["recent"]
        for accession, form, accepted, doc, label in zip(
            *(
                recent[k]
                for k in (
                    "accessionNumber",
                    "form",
                    "acceptanceDateTime",
                    "primaryDocument",
                    "primaryDocDescription",
                )
            ),
            strict=True,
        ):
            url = f"https://www.sec.gov/Archives/edgar/data/{int(info['cik'])}/{accession.replace('-', '')}/{doc}"
            material = form.split("/")[0] in {"8-K", "10-Q", "10-K", "6-K", "20-F"}
            yield Item(
                accession,
                f"{ISSUERS[source.ticker].name} filed {form}"
                + (f" · {label}" if label and label != form else ""),
                canonical(url),
                stamp(accepted),
                (source.ticker,),
                urgent=material,
            )
        return
    if source.kind == "tsmc":
        articles = re.findall(
            r'<article\b[^>]*\babout="(/english/news/\d+)"[^>]*>(.*?)</article>',
            body.decode(),
            re.S,
        )
        if not articles:
            raise ValueError("TSMC release list unavailable")
        for path, html in articles:
            date = re.search(r'<time\b[^>]*datetime="([^"]+)"', html)
            title = re.search(r"<h2\b[^>]*>(.*?)</h2>", html, re.S)
            if not date or not title:
                raise ValueError("Incomplete TSMC listing")
            yield Item(
                path,
                plain(title[1]),
                "https://pr.tsmc.com" + path,
                stamp(date[1]),
                ("TSM",),
            )
        return
    root = ElementTree.fromstring(body)
    if root.find("channel") is None:
        raise ValueError("RSS channel unavailable")
    for node in root.findall("./channel/item"):
        title = unescape(node.findtext("title", "")).strip()
        link = canonical(node.findtext("link", ""))
        uid = node.findtext("guid") or link
        if not title or not uid:
            raise ValueError("Incomplete RSS item")
        desc = plain(node.findtext("description", ""))
        content = node.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "")
        tickers = (
            (source.ticker,)
            if source.ticker
            else tuple(t for t in watch if matches(title, t))
        )
        if not tickers:
            tickers = tuple(t for t in watch if matches(desc, t))
        if not tickers and content:
            lead = plain(content)[:1200]
            tickers = tuple(t for t in watch if matches(lead, t))
        if tickers:
            excerpt = plain(content) if content else desc
            # Preserve the disclosure even when it appears below the bounded excerpt.
            disclosure = re.search(
                r"[^.!?]{0,130}\b(?:short position|is short|was short)\b[^.!?]{0,160}",
                excerpt,
                re.I,
            )
            excerpt = excerpt[:2600] + (" " + disclosure[0] if disclosure else "")
            yield Item(
                uid[:2000],
                title[:600],
                link,
                stamp(node.findtext("pubDate", "")),
                tickers,
                excerpt,
            )


def fetch(url, headers=None, limit=4_000_000):
    with requests.get(
        url, headers=headers, stream=True, timeout=(4, 10), allow_redirects=False
    ) as response:
        if response.status_code == 304:
            return 304, response.headers, b""
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Unexpected source response")
        buf = bytearray()
        for chunk in response.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > limit:
                raise ValueError("Source exceeded size bound")
        return 200, response.headers, bytes(buf)
