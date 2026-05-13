import os
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.auth import DEFAULT_USER_ID
from app.db import Base
from app.journal_schemas import AIJournalRecapIn, JournalMood
from app.main import _to_gemini_http_exception, create_projectx_account_ai_journal_recap
from app.models import Account, JournalEntry, ProjectXTradeEvent
from app.services.gemini_client import GeminiClientError
from app.services.journal import create_journal_entry
from app.services.journal_ai_recap import (
    AI_RECAP_END_MARKER,
    AI_RECAP_START_MARKER,
    generate_ai_journal_recap,
)


RECAP = """# Daily Recap
## Session Summary
One MNQ trade closed for +100.00 gross.
## What Went Well
Execution stayed limited to the supplied trade.
## What Hurt Performance
Fees reduced realized performance.
## Execution Review
The close was recorded from actual ProjectX data.
## Risk Review
Size was 1 contract.
## Behavioral Flags
No behavioral notes were supplied.
## Tomorrow's Focus
Review execution quality.
## Suggested Tags
nq, execution
"""


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Account.__table__, JournalEntry.__table__, ProjectXTradeEvent.__table__])
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=[ProjectXTradeEvent.__table__, JournalEntry.__table__, Account.__table__])
        engine.dispose()


class FakeGemini:
    def __init__(self, text: str = RECAP):
        self.text = text
        self.calls: list[dict[str, object]] = []

    def generate_text(self, prompt, *, system_instruction=None, generation_config=None):
        self.calls.append(
            {
                "prompt": prompt,
                "system_instruction": system_instruction,
                "generation_config": generation_config,
            }
        )
        return self.text


class FailingGemini:
    def generate_text(self, *_args, **_kwargs):
        raise GeminiClientError("Gemini failed.", status_code=502)


def add_projectx_account(db_session, account_id: int, *, user_id: str = DEFAULT_USER_ID) -> None:
    db_session.add(
        Account(
            user_id=user_id,
            provider="projectx",
            external_id=str(account_id),
            name=f"Account {account_id}",
            account_state="ACTIVE",
        )
    )
    db_session.commit()


def add_closed_trade(
    db_session,
    account_id: int,
    *,
    user_id: str = DEFAULT_USER_ID,
    trade_id: str = "trade-1",
    timestamp: datetime = datetime(2026, 5, 12, 14, 30, tzinfo=timezone.utc),
    pnl: float = 100.0,
) -> ProjectXTradeEvent:
    row = ProjectXTradeEvent(
        user_id=user_id,
        account_id=account_id,
        contract_id="MNQM6",
        symbol="MNQ",
        side="SELL",
        size=1,
        price=20050,
        trade_timestamp=timestamp,
        fees=1.02,
        pnl=pnl,
        order_id=f"order-{trade_id}",
        source_trade_id=trade_id,
        raw_payload={},
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_ai_recap_skips_no_trade_day_without_calling_gemini(db_session):
    add_projectx_account(db_session, 7101)
    fake = FakeGemini()

    result = generate_ai_journal_recap(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7101,
        entry_date=date(2026, 5, 12),
        gemini_client_factory=lambda: fake,
    )

    assert result["skipped"] is True
    assert result["skip_reason"] == "no_trades_for_day"
    assert result["source_trade_count"] == 0
    assert fake.calls == []
    assert db_session.query(JournalEntry).count() == 0


def test_ai_recap_creates_journal_entry_when_trades_exist(db_session):
    add_projectx_account(db_session, 7102)
    add_closed_trade(db_session, 7102)
    fake = FakeGemini()

    result = generate_ai_journal_recap(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7102,
        entry_date=date(2026, 5, 12),
        gemini_client_factory=lambda: fake,
    )

    entry = db_session.query(JournalEntry).one()
    assert result["created"] is True
    assert result["updated"] is False
    assert result["journal_entry_id"] == int(entry.id)
    assert result["source_trade_count"] == 1
    assert entry.title == "AI Recap - 2026-05-12"
    assert entry.mood == JournalMood.NEUTRAL.value
    assert entry.tags == ["ai-recap", "nq", "execution"]
    assert entry.body.startswith(AI_RECAP_START_MARKER)
    assert "# Daily Recap" in entry.body
    assert fake.calls[0]["generation_config"] == {"temperature": 0.1}


def test_ai_recap_updates_existing_entry_without_losing_user_content(db_session):
    add_projectx_account(db_session, 7103)
    add_closed_trade(db_session, 7103)
    existing, _ = create_journal_entry(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7103,
        entry_date=date(2026, 5, 12),
        title="Manual review",
        mood=JournalMood.FOCUSED,
        tags=["manual"],
        body="My own notes stay here.",
    )

    result = generate_ai_journal_recap(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7103,
        entry_date=date(2026, 5, 12),
        gemini_client_factory=lambda: FakeGemini(),
    )

    db_session.refresh(existing)
    assert result["created"] is False
    assert result["updated"] is True
    assert existing.title == "Manual review"
    assert existing.mood == JournalMood.FOCUSED.value
    assert "My own notes stay here." in existing.body
    assert "# Daily Recap" in existing.body
    assert existing.tags == ["manual", "ai-recap", "nq", "execution"]


def test_ai_recap_replaces_existing_marked_section_without_duplication(db_session):
    add_projectx_account(db_session, 7104)
    add_closed_trade(db_session, 7104)
    body = (
        "Manual notes.\n\n"
        "---\n\n"
        f"{AI_RECAP_START_MARKER}\nold recap\n{AI_RECAP_END_MARKER}\n\n"
        "Afterword stays."
    )
    existing, _ = create_journal_entry(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7104,
        entry_date=date(2026, 5, 12),
        title="Replace section",
        mood=JournalMood.NEUTRAL,
        tags=["manual"],
        body=body,
    )

    fake = FakeGemini()
    generate_ai_journal_recap(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7104,
        entry_date=date(2026, 5, 12),
        gemini_client_factory=lambda: fake,
    )

    db_session.refresh(existing)
    assert "old recap" not in existing.body
    assert "Afterword stays." in existing.body
    assert existing.body.count(AI_RECAP_START_MARKER) == 1
    assert existing.body.count(AI_RECAP_END_MARKER) == 1
    assert "Manual notes." in str(fake.calls[0]["prompt"])
    assert "old recap" not in str(fake.calls[0]["prompt"])


def test_ai_recap_gemini_failure_does_not_create_or_update_journal_entry(db_session):
    add_projectx_account(db_session, 7105)
    add_closed_trade(db_session, 7105)
    existing, _ = create_journal_entry(
        db_session,
        user_id=DEFAULT_USER_ID,
        account_id=7105,
        entry_date=date(2026, 5, 12),
        title="Manual review",
        mood=JournalMood.NEUTRAL,
        tags=["manual"],
        body="Original body.",
    )

    with pytest.raises(GeminiClientError):
        generate_ai_journal_recap(
            db_session,
            user_id=DEFAULT_USER_ID,
            account_id=7105,
            entry_date=date(2026, 5, 12),
            gemini_client_factory=lambda: FailingGemini(),
        )

    db_session.refresh(existing)
    assert db_session.query(JournalEntry).count() == 1
    assert existing.body == "Original body."
    assert existing.tags == ["manual"]


def test_gemini_http_exception_surfaces_transient_upstream_message():
    exc = GeminiClientError(
        "Gemini request failed: This model is currently experiencing high demand. Please try again later.",
        status_code=503,
    )

    http_exc = _to_gemini_http_exception(exc)

    assert http_exc.status_code == 503
    assert http_exc.detail == (
        "Gemini request failed: This model is currently experiencing high demand. Please try again later."
    )


def test_gemini_http_exception_surfaces_non_transient_upstream_message():
    exc = GeminiClientError("Gemini request failed: API key not valid.", status_code=400)

    http_exc = _to_gemini_http_exception(exc)

    assert http_exc.status_code == 502
    assert http_exc.detail == "Gemini request failed: API key not valid."


def test_ai_recap_route_preserves_user_account_scope(db_session):
    add_projectx_account(db_session, 7106, user_id="11111111-1111-1111-1111-111111111111")

    with pytest.raises(HTTPException) as exc_info:
        create_projectx_account_ai_journal_recap(
            account_id=7106,
            payload=AIJournalRecapIn(entry_date=date(2026, 5, 12)),
            db=db_session,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Account not found."
