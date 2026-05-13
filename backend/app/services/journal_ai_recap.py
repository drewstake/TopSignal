from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from ..journal_schemas import AIJournalRecapMode, JournalMood
from ..models import Account, JournalEntry, ProjectXTradeEvent
from .gemini_client import GeminiClient, GeminiClientError
from .journal import _compute_trade_stats_snapshot, create_journal_entry, normalize_tags, update_journal_entry
from .projectx_accounts import (
    get_projectx_account_row,
    resolve_projectx_account_effective_name,
    resolve_projectx_account_provider_name,
)
from .projectx_trades import _non_voided_trade_event_expr, serialize_trade_event
from .trading_day import trading_day_bounds_utc

AI_RECAP_START_MARKER = "<!-- topsignal-ai-recap:start -->"
AI_RECAP_END_MARKER = "<!-- topsignal-ai-recap:end -->"

_SYSTEM_INSTRUCTION = (
    "You are TopSignal's AI trading journal assistant. Create a concise, accurate daily trading recap from actual "
    "trading data. Use only the supplied data. Do not invent trades, PnL, emotions, prices, market context, or rules. "
    "Separate facts from interpretation. Do not provide financial advice, trade predictions, or trade recommendations. "
    "Focus on execution, risk, behavior, and process."
)
_AI_RECAP_TAG = "ai-recap"


class TextGenerationClient(Protocol):
    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: str | None = None,
        generation_config: dict[str, Any] | None = None,
    ) -> str:
        ...


GeminiClientFactory = Callable[[], TextGenerationClient]


def generate_ai_journal_recap(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    entry_date: date,
    mode: AIJournalRecapMode | str = AIJournalRecapMode.APPEND_OR_CREATE,
    include_existing_notes: bool = True,
    gemini_client_factory: GeminiClientFactory | None = None,
) -> dict[str, Any]:
    normalized_mode = _normalize_mode(mode)
    if normalized_mode != AIJournalRecapMode.APPEND_OR_CREATE.value:
        raise ValueError("unsupported AI journal recap mode")

    account = get_projectx_account_row(db, account_id, user_id=user_id)
    if account is None:
        raise LookupError("Account not found.")

    rows = _load_closed_trade_events_for_day(db, user_id=user_id, account_id=account_id, entry_date=entry_date)
    source_trade_count = len(rows)
    generated_at = _utcnow()
    if source_trade_count == 0:
        return {
            "account_id": account_id,
            "entry_date": entry_date,
            "journal_entry_id": None,
            "created": False,
            "updated": False,
            "skipped": True,
            "skip_reason": "no_trades_for_day",
            "source_trade_count": 0,
            "recap_markdown": "",
            "generated_at": generated_at,
        }

    existing_entry = _get_entry_for_account_and_date(db, user_id=user_id, account_id=account_id, entry_date=entry_date)
    existing_notes = (
        _strip_ai_recap_section(existing_entry.body or "")
        if existing_entry is not None and include_existing_notes
        else ""
    )
    prompt = _build_prompt(
        account=account,
        account_id=account_id,
        entry_date=entry_date,
        day_summary=_build_day_summary(rows),
        trades=[serialize_trade_event(row) for row in rows],
        existing_notes=existing_notes,
    )

    client_factory = gemini_client_factory or GeminiClient.from_env
    recap_markdown = _normalize_recap_markdown(
        client_factory().generate_text(
            prompt,
            system_instruction=_SYSTEM_INSTRUCTION,
            generation_config={"temperature": 0.1},
        )
    )
    if not recap_markdown:
        raise GeminiClientError("Gemini returned an empty recap.", status_code=502)

    generated_at = _utcnow()
    latest_entry = _get_entry_for_account_and_date(db, user_id=user_id, account_id=account_id, entry_date=entry_date)
    if latest_entry is None:
        row, already_existed = create_journal_entry(
            db,
            user_id=user_id,
            account_id=account_id,
            entry_date=entry_date,
            title=f"AI Recap - {entry_date.isoformat()}",
            mood=JournalMood.NEUTRAL,
            tags=_merge_tags([], _extract_suggested_tags(recap_markdown)),
            body=_wrap_ai_recap(recap_markdown),
        )
        if not already_existed:
            return {
                "account_id": account_id,
                "entry_date": entry_date,
                "journal_entry_id": int(row.id),
                "created": True,
                "updated": False,
                "skipped": False,
                "skip_reason": None,
                "source_trade_count": source_trade_count,
                "recap_markdown": recap_markdown,
                "generated_at": generated_at,
            }
        latest_entry = row

    next_body = _upsert_ai_recap_section(latest_entry.body or "", recap_markdown)
    next_tags = _merge_tags(_copy_tags(latest_entry.tags), _extract_suggested_tags(recap_markdown))
    updated_row = update_journal_entry(
        db,
        user_id=user_id,
        account_id=account_id,
        entry_id=int(latest_entry.id),
        version=int(latest_entry.version or 1),
        tags=next_tags,
        body=next_body,
    )
    return {
        "account_id": account_id,
        "entry_date": entry_date,
        "journal_entry_id": int(updated_row.id),
        "created": False,
        "updated": True,
        "skipped": False,
        "skip_reason": None,
        "source_trade_count": source_trade_count,
        "recap_markdown": recap_markdown,
        "generated_at": generated_at,
    }


def _normalize_mode(mode: AIJournalRecapMode | str) -> str:
    if isinstance(mode, AIJournalRecapMode):
        return mode.value
    return str(mode).strip()


def _load_closed_trade_events_for_day(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    entry_date: date,
) -> list[ProjectXTradeEvent]:
    day_start, day_end = trading_day_bounds_utc(entry_date)
    query = (
        db.query(ProjectXTradeEvent)
        .filter(ProjectXTradeEvent.user_id == user_id)
        .filter(ProjectXTradeEvent.account_id == account_id)
        .filter(ProjectXTradeEvent.pnl.isnot(None))
        .filter(_non_voided_trade_event_expr())
        .filter(ProjectXTradeEvent.trade_timestamp >= day_start)
        .filter(ProjectXTradeEvent.trade_timestamp <= day_end)
    )
    return query.order_by(ProjectXTradeEvent.trade_timestamp.asc(), ProjectXTradeEvent.id.asc()).all()


def _get_entry_for_account_and_date(
    db: Session,
    *,
    user_id: str,
    account_id: int,
    entry_date: date,
) -> JournalEntry | None:
    return (
        db.query(JournalEntry)
        .filter(JournalEntry.user_id == user_id)
        .filter(JournalEntry.account_id == account_id)
        .filter(JournalEntry.entry_date == entry_date)
        .one_or_none()
    )


def _build_day_summary(rows: list[ProjectXTradeEvent]) -> dict[str, Any]:
    snapshot = _compute_trade_stats_snapshot(rows)
    pnls = [float(row.pnl) for row in rows if row.pnl is not None]
    symbols = sorted({str(row.symbol or row.contract_id) for row in rows if row.symbol or row.contract_id})
    first_trade_at = min((_as_utc(row.trade_timestamp) for row in rows), default=None)
    last_trade_at = max((_as_utc(row.trade_timestamp) for row in rows), default=None)
    return {
        **snapshot,
        "source_trade_count": len(rows),
        "winning_trade_count": len([value for value in pnls if value > 0]),
        "losing_trade_count": len([value for value in pnls if value < 0]),
        "breakeven_trade_count": len([value for value in pnls if value == 0]),
        "symbols": symbols,
        "first_trade_at": first_trade_at,
        "last_trade_at": last_trade_at,
    }


def _build_account_payload(account: Account, *, account_id: int) -> dict[str, Any]:
    provider_name = resolve_projectx_account_provider_name(account.name, account_id=account_id)
    effective_name = resolve_projectx_account_effective_name(
        provider_name=provider_name,
        display_name=account.display_name,
    )
    return {
        "account_id": account_id,
        "provider": account.provider,
        "name": effective_name,
        "provider_name": provider_name,
        "custom_display_name": account.display_name,
        "account_state": account.account_state,
        "can_trade": account.can_trade,
        "is_visible": account.is_visible,
        "is_main": bool(account.is_main),
    }


def _build_prompt(
    *,
    account: Account,
    account_id: int,
    entry_date: date,
    day_summary: dict[str, Any],
    trades: list[dict[str, Any]],
    existing_notes: str,
) -> str:
    existing_notes_or_empty = existing_notes.strip()
    return (
        "Create a daily trading journal recap for this account and trading day.\n\n"
        "Account:\n"
        f"{_json_text(_build_account_payload(account, account_id=account_id))}\n\n"
        "Trading day:\n"
        f"{entry_date.isoformat()}\n\n"
        "Day summary:\n"
        f"{_json_text(day_summary)}\n\n"
        "Trades:\n"
        f"{_json_text(trades)}\n\n"
        "Existing journal notes:\n"
        f"{existing_notes_or_empty}\n\n"
        "Return markdown in exactly this structure:\n"
        "# Daily Recap\n"
        "## Session Summary\n"
        "## What Went Well\n"
        "## What Hurt Performance\n"
        "## Execution Review\n"
        "## Risk Review\n"
        "## Behavioral Flags\n"
        "## Tomorrow's Focus\n"
        "## Suggested Tags\n\n"
        "Rules:\n"
        "- Use specific values from the data.\n"
        "- If a field is missing, do not discuss it.\n"
        "- If there are no trades, write a no-trade recap, though the backend should normally skip no-trade days.\n"
        "- Keep it concise and useful.\n"
        "- Do not mention that you are an AI.\n"
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _normalize_recap_markdown(value: str) -> str:
    text = (value or "").strip()
    fence_match = re.fullmatch(r"```(?:markdown|md)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    return text


def _wrap_ai_recap(recap_markdown: str) -> str:
    return f"{AI_RECAP_START_MARKER}\n{recap_markdown.strip()}\n{AI_RECAP_END_MARKER}"


def _upsert_ai_recap_section(body: str, recap_markdown: str) -> str:
    wrapped = _wrap_ai_recap(recap_markdown)
    start_index = body.find(AI_RECAP_START_MARKER)
    end_index = body.find(AI_RECAP_END_MARKER)
    if start_index >= 0 and end_index >= start_index:
        end_index += len(AI_RECAP_END_MARKER)
        return f"{body[:start_index]}{wrapped}{body[end_index:]}"

    normalized_body = body.rstrip()
    if not normalized_body:
        return wrapped
    return f"{normalized_body}\n\n---\n\n{wrapped}"


def _strip_ai_recap_section(body: str) -> str:
    start_index = body.find(AI_RECAP_START_MARKER)
    end_index = body.find(AI_RECAP_END_MARKER)
    if start_index < 0 or end_index < start_index:
        return body
    end_index += len(AI_RECAP_END_MARKER)
    return f"{body[:start_index]}{body[end_index:]}".strip()


def _extract_suggested_tags(markdown: str) -> list[str]:
    match = re.search(r"(?ims)^##\s+Suggested Tags\s*$\s*(?P<body>.*?)(?=^##\s+|\Z)", markdown)
    if not match:
        return []

    raw_section = match.group("body")
    tags: list[str] = []
    for segment in re.split(r"[\n,]", raw_section):
        tag = segment.strip()
        tag = re.sub(r"^[-*+]\s*", "", tag).strip()
        tag = tag.strip("`*_ ")
        if tag.startswith("#"):
            tag = tag[1:]
        tag = re.sub(r"\s+", "-", tag)
        tag = re.sub(r"[^A-Za-z0-9_-]", "", tag)
        if tag:
            tags.append(tag)
    return tags


def _merge_tags(existing_tags: list[str], suggested_tags: list[str]) -> list[str]:
    return normalize_tags([*existing_tags, _AI_RECAP_TAG, *suggested_tags])


def _copy_tags(value: Any) -> list[str]:
    raw_tags = value if isinstance(value, list) else []
    return [str(tag) for tag in raw_tags]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
