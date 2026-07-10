from datetime import datetime, timezone

from app.services.trading_day import futures_session_is_open, trading_day_key


def test_trading_day_key_keeps_559pm_et_on_same_day():
    # 2026-03-02 17:59 ET (UTC-5) -> same trading day.
    assert trading_day_key(datetime(2026, 3, 2, 22, 59, tzinfo=timezone.utc)) == "2026-03-02"


def test_trading_day_key_rolls_600pm_et_to_next_day():
    # 2026-03-02 18:00 ET (UTC-5) -> next trading day.
    assert trading_day_key(datetime(2026, 3, 2, 23, 0, tzinfo=timezone.utc)) == "2026-03-03"


def test_trading_day_key_rolls_monday_609pm_et_to_tuesday():
    # Reported case: Monday 2026-03-02 18:09 ET should bucket as Tuesday.
    assert trading_day_key(datetime(2026, 3, 2, 23, 9, tzinfo=timezone.utc)) == "2026-03-03"


def test_futures_session_open_excludes_daily_maintenance_and_weekend_closures():
    assert futures_session_is_open(datetime(2026, 7, 10, 20, 59, tzinfo=timezone.utc)) is True
    assert futures_session_is_open(datetime(2026, 7, 10, 21, 0, tzinfo=timezone.utc)) is False
    assert futures_session_is_open(datetime(2026, 7, 12, 21, 59, tzinfo=timezone.utc)) is False
    assert futures_session_is_open(datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc)) is True


def test_equity_schedule_excludes_the_daily_halt_without_assuming_it_for_unknown_products():
    before_halt = datetime(2026, 7, 6, 20, 14, tzinfo=timezone.utc)
    halt = datetime(2026, 7, 6, 20, 15, tzinfo=timezone.utc)
    after_halt = datetime(2026, 7, 6, 20, 30, tzinfo=timezone.utc)

    assert futures_session_is_open(before_halt, symbol="MNQ") is True
    assert futures_session_is_open(halt, symbol="CON.F.US.MNQ.U26") is False
    assert futures_session_is_open(after_halt, symbol="F.US.MNQ") is True
    assert futures_session_is_open(halt, symbol="UNKNOWN") is True


def test_equity_schedule_excludes_modern_holiday_closures():
    assert futures_session_is_open(
        datetime(2026, 4, 3, 13, 14, tzinfo=timezone.utc),
        symbol="MNQ",
    ) is True
    assert futures_session_is_open(
        datetime(2026, 4, 3, 13, 15, tzinfo=timezone.utc),
        symbol="MNQ",
    ) is False
    assert futures_session_is_open(
        datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc),
        symbol="MNQ",
    ) is False
    assert futures_session_is_open(
        datetime(2026, 12, 25, 15, 0, tzinfo=timezone.utc),
        symbol="MNQ",
    ) is False
