"""Compact scanner-only schema. All instants are Unix seconds in UTC."""

import hashlib
import re
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    and_,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .sources import NVIDIA_NEWSROOM, NVIDIA_WIRE

meta = MetaData(schema="topsignal_local")
events = Table(
    "scanner_event",
    meta,
    Column("id", Integer, primary_key=True),
    Column("kind", String, nullable=False),
    Column("tickers", String, nullable=False),
    Column("title", Text, nullable=False),
    Column("source", String, nullable=False),
    Column("link", Text, nullable=False),
    Column("received", Float, nullable=False),
    Column("published", Float),
    Column("baseline", Boolean, nullable=False),
    Column("urgent", Boolean, nullable=False),
    Column("material", Boolean, nullable=False),
    Column("delayed", Boolean, nullable=False),
    Column("excerpt", Text, nullable=False),
    Column("summary", Text),
    Column("cause", Text),
    Column("citation", Text),
    Column("citation_time", Float),
    Column("move_start", Float),
)
Index("scanner_feed_order", events.c.received, events.c.id)
aliases = Table(
    "scanner_identity",
    meta,
    Column("key", String(64), primary_key=True),
    Column("event_id", ForeignKey(events.c.id), nullable=False),
)
links = Table(
    "scanner_related",
    meta,
    Column("parent", ForeignKey(events.c.id), primary_key=True),
    Column("child", ForeignKey(events.c.id), primary_key=True),
    Column("relation", String, nullable=False),
)
health = Table(
    "scanner_source",
    meta,
    Column("name", String, primary_key=True),
    Column("status", String),
    Column("success", Float),
    Column("since", Float),
    Column("etag", Text),
    Column("modified", Text),
    Column("next_check", Float),
    Column("failures", Integer),
)
jobs = Table(
    "scanner_job",
    meta,
    Column("id", Integer, primary_key=True),
    Column("event_id", ForeignKey(events.c.id), nullable=False),
    Column("kind", String, nullable=False),
    Column("state", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("due", Float, nullable=False),
    Column("finished", Float),
    Column("detail", String),
    UniqueConstraint("event_id", "kind"),
)
Index("scanner_due", jobs.c.kind, jobs.c.state, jobs.c.due)
spend = Table(
    "scanner_budget",
    meta,
    Column("cycle", String, primary_key=True),
    Column("micro_usd", Integer, nullable=False),
)
usage = Table(
    "scanner_usage",
    meta,
    Column("id", Integer, primary_key=True),
    Column("cycle", String, nullable=False),
    Column("job", ForeignKey(jobs.c.id)),
    Column("model", String),
    Column("charge", Integer),
    Column("inputs", Integer),
    Column("outputs", Integer),
    Column("searches", Integer),
)


def upsert(c, table):
    return (pg_insert if c.dialect.name == "postgresql" else sqlite_insert)(table)


def serial(c):
    if c.dialect.name == "postgresql":
        c.execute(text("SELECT pg_advisory_xact_lock(18752304)"))


def source_state(c, name, **values):
    stmt = upsert(c, health).values(name=name, **values)
    c.execute(stmt.on_conflict_do_update(index_elements=["name"], set_=values))


def enqueue(c, eid, kind):
    c.execute(
        upsert(c, jobs)
        .values(event_id=eid, kind=kind, state="pending", attempts=0, due=0)
        .on_conflict_do_nothing(index_elements=["event_id", "kind"])
    )


def add_link(c, parent, child, relation):
    if parent != child:
        c.execute(
            upsert(c, links)
            .values(parent=parent, child=child, relation=relation)
            .on_conflict_do_nothing(index_elements=["parent", "child"])
        )


def key(value):
    return hashlib.sha256(value.encode()).hexdigest()


def coverage_root(c, event_id):
    # Related copies and corrections always link directly to the first report.
    parent = c.scalar(
        select(links.c.parent)
        .where(
            links.c.child == event_id,
            links.c.relation.in_(["Related coverage", "Publisher correction"]),
        )
        .limit(1)
    )
    return parent or event_id


def ticker_filter(tickers):
    return or_(*(events.c.tickers.like(f"% {t} %") for t in tickers))


def nvidia_release_copy(c, source, item, title, baseline):
    other = NVIDIA_WIRE if source == NVIDIA_NEWSROOM else NVIDIA_NEWSROOM
    # The wire timestamps items to the minute. Match only NVIDIA's own releases.
    minute = item.published // 60 * 60
    query = select(events).where(
        events.c.source == other.name,
        events.c.published >= minute,
        events.c.published < minute + 60,
    )
    if not baseline:
        query = query.where(events.c.urgent)
    normalized = " ".join(title.casefold().split())
    return next(
        (
            e
            for e in c.execute(query).mappings()
            if " ".join(e.title.casefold().split()) == normalized
        ),
        None,
    )


def headline(c, source, item, received, baseline=False):
    serial(c)
    ids = [key(source.name + "\n" + item.uid)]
    if item.link:
        ids.append(key(item.link))
    matched = (
        c.execute(select(events).join(aliases).where(aliases.c.key.in_(ids)).limit(1))
        .mappings()
        .first()
    )
    title = item.title.strip()[:600]
    if matched:
        for k in ids:
            c.execute(
                upsert(c, aliases)
                .values(key=k, event_id=matched.id)
                .on_conflict_do_nothing()
            )
        correction = re.search(
            r"(?:correction|editor.s note|clarification)\s*[:–-]\s*([^.!?]{12,250})",
            item.excerpt,
            re.I,
        )
        if title == matched.title and (
            not correction or correction[0] in matched.excerpt
        ):
            return matched.id
        if title == matched.title and correction:
            title = ("Correction · " + correction[1])[:600]
        # A publisher editing a stable item preserves the original and its dispatch.
        ids = [key(ids[0] + "\n" + title)]
        seen = c.scalar(select(aliases.c.event_id).where(aliases.c.key == ids[0]))
        if seen:
            return seen
    nearby = list(
        c.execute(
            select(events)
            .where(
                ticker_filter(item.tickers),
                events.c.received >= received - 7 * 86400,
                events.c.received <= received,
            )
            .order_by(events.c.received.desc(), events.c.id.desc())
        ).mappings()
    )
    copy = None
    if source in (NVIDIA_NEWSROOM, NVIDIA_WIRE):
        # Receipt timestamps can be out of order across independent pollers.
        copy = nvidia_release_copy(c, source, item, title, baseline)
    elif source.name.startswith("Hunterbrook"):
        copy = next(
            (
                e
                for e in nearby
                if e.source.startswith("Hunterbrook")
                and e.source != source.name
                and e.title.casefold() == title.casefold()
            ),
            None,
        )
    urgent = item.urgent and not baseline and not copy
    eid = c.scalar(
        insert(events)
        .values(
            kind="headline",
            tickers=" " + " ".join(item.tickers) + " ",
            title=title,
            source=source.name,
            link=item.link,
            published=item.published,
            received=received,
            baseline=baseline,
            urgent=bool(urgent),
            material=item.urgent,
            delayed=received - item.published > max(300, 2 * source.interval),
            excerpt=item.excerpt[:3200],
        )
        .returning(events.c.id)
    )
    for k in ids:
        c.execute(insert(aliases).values(key=k, event_id=eid))
    if copy:
        add_link(c, coverage_root(c, copy.id), eid, "Related coverage")
    if matched:
        add_link(c, coverage_root(c, matched.id), eid, "Publisher correction")
    if urgent:
        enqueue(c, eid, "delivery")
        enqueue(c, eid, "summary")
    if not baseline and item.urgent:
        for e in nearby:
            if (
                e.kind == "move"
                and e.move_start - 1800 <= item.published <= e.received + 1800
            ):
                add_link(c, e.id, eid, timing(item.published, e.move_start))
        if source.ticker and re.search(
            r"\b(responds?|response|statement|correction|clarif\w*)\b", title, re.I
        ):
            original = next(
                (
                    e
                    for e in nearby
                    if e.source.startswith("Hunterbrook")
                    and (
                        "hunterbrook" in (title + " " + item.excerpt).lower()
                        or any(term in title.lower() for term in topic_words(e.title))
                    )
                    and e.published <= item.published
                ),
                None,
            )
            if original:
                add_link(c, coverage_root(c, original.id), eid, "Company response")
    return eid


def topic_words(title):
    return set(re.findall(r"[a-z]{5,}", title.lower())) - {
        "their",
        "about",
        "report",
        "investigation",
        "press",
        "release",
        "statement",
        "responds",
    }


def timing(published, start):
    return "Known when the move began" if published <= start else "Reported afterward"


def price_move(c, ticker, stats, received, rules, provider):
    if not stats or not stats["qualifies"]:
        return None
    serial(c)
    recent = c.scalar(
        select(events.c.id)
        .where(
            events.c.kind == "move",
            ticker_filter([ticker]),
            events.c.move_start > stats["start"] - rules.cooldown * 60,
        )
        .limit(1)
    )
    if recent:
        return None
    sector = stats["sector_percent"]
    comparison = (
        f" · SOXX {sector:+.2f}%" if sector is not None else " · SOXX unavailable"
    )
    title = f"{ticker} {stats['percent']:+.2f}% in {stats['minutes']} min · {stats['rvol']:.2f}× relative volume{comparison}"
    eid = c.scalar(
        insert(events)
        .values(
            kind="move",
            tickers=f" {ticker} ",
            title=title,
            source=provider,
            link="",
            published=None,
            received=received,
            move_start=stats["start"],
            baseline=False,
            delayed=False,
            urgent=True,
            material=True,
            excerpt="",
            cause="Cause unknown",
        )
        .returning(events.c.id)
    )
    enqueue(c, eid, "delivery")
    direct = (
        c.execute(
            select(events)
            .where(
                ticker_filter([ticker]),
                events.c.kind == "headline",
                events.c.material,
                events.c.received <= received,
                events.c.published.between(stats["start"] - 1800, received),
            )
            .order_by(events.c.published.desc())
            .limit(5)
        )
        .mappings()
        .all()
    )
    for e in direct:
        add_link(c, eid, e.id, timing(e.published, stats["start"]))
    if not direct:
        enqueue(c, eid, "search")
    return eid


def claim(c, kinds, now):
    # The caller holds a short transaction. An expired, ambiguous external send/search
    # requires reconciliation. Never blindly issue it again after a process crash.
    c.execute(
        update(jobs)
        .where(
            jobs.c.kind.in_(kinds),
            jobs.c.kind.in_(["delivery", "search"]),
            jobs.c.state == "working",
            jobs.c.due <= now,
        )
        .values(state="failed", detail="Outcome unconfirmed")
    )
    row = (
        c.execute(
            select(jobs)
            .where(
                jobs.c.kind.in_(kinds),
                jobs.c.due <= now,
                or_(
                    jobs.c.state == "pending",
                    and_(jobs.c.kind == "summary", jobs.c.state == "working"),
                ),
            )
            .order_by(jobs.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .first()
    )
    if not row:
        return None
    c.execute(
        update(jobs)
        .where(jobs.c.id == row.id)
        .values(state="working", due=now + 300, attempts=row.attempts + 1)
    )
    event = (
        c.execute(select(events).where(events.c.id == row.event_id)).mappings().one()
    )
    return row, event


def finish(c, job, state, now, detail=None, delay=0):
    c.execute(
        update(jobs)
        .where(
            jobs.c.id == job.id,
            jobs.c.state == "working",
            jobs.c.attempts == job.attempts + 1,
        )
        .values(
            state=state,
            finished=now if state == "dispatched" else None,
            detail=detail,
            due=now + delay,
        )
    )


def cycle_key(now, day):
    dt = datetime.fromtimestamp(now, UTC)
    if dt.day < day:
        dt = datetime.fromtimestamp(
            dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
            - 1,
            UTC,
        )
    return dt.strftime("%Y-%m") + f"-{day:02d}"


def reserve(c, cycle, job, model, bound, cap):
    c.execute(
        upsert(c, spend).values(cycle=cycle, micro_usd=0).on_conflict_do_nothing()
    )
    accepted = c.execute(
        update(spend)
        .where(spend.c.cycle == cycle, spend.c.micro_usd <= cap - bound)
        .values(micro_usd=spend.c.micro_usd + bound)
    )
    if not accepted.rowcount:
        return None
    return c.scalar(
        insert(usage)
        .values(cycle=cycle, job=job, model=model, charge=bound)
        .returning(usage.c.id)
    )


def settle(c, attempt, cost, counts):
    row = (
        c.execute(select(usage).where(usage.c.id == attempt).with_for_update())
        .mappings()
        .one()
    )
    if row.inputs is not None:
        return
    c.execute(
        update(spend)
        .where(spend.c.cycle == row.cycle)
        .values(micro_usd=spend.c.micro_usd - row.charge + cost)
    )
    c.execute(
        update(usage)
        .where(usage.c.id == attempt)
        .values(charge=cost, inputs=counts[0], outputs=counts[1], searches=counts[2])
    )


def feed(c, limit=60, before=None, after=None):
    q = select(events)
    if after is not None:
        q = q.where(events.c.urgent, events.c.id > after).order_by(events.c.id)
    else:
        if before is not None:
            q = q.where(events.c.id < before)
        q = q.order_by(events.c.received.desc(), events.c.id.desc())
    rows = c.execute(q.limit(limit)).mappings().all()
    ids = [e.id for e in rows]
    updates = {}
    for r in c.execute(
        select(
            links.c.parent,
            links.c.relation,
            events.c.title,
            events.c.source,
            events.c.link,
            events.c.received,
            events.c.published,
        )
        .join(events, links.c.child == events.c.id)
        .where(links.c.parent.in_(ids))
        .order_by(events.c.received)
    ).mappings():
        updates.setdefault(r.parent, []).append(dict(r))
    tasks = {}
    for r in c.execute(select(jobs).where(jobs.c.event_id.in_(ids))).mappings():
        tasks.setdefault(r.event_id, {})[r.kind] = dict(
            state=r.state, detail=r.detail, finished=r.finished
        )
    return [
        {
            **{k: v for k, v in e.items() if k != "excerpt"},
            "tickers": e.tickers.split(),
            "updates": updates.get(e.id, []),
            "jobs": tasks.get(e.id, {}),
        }
        for e in rows
    ]
