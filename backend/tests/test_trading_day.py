from datetime import datetime, timezone

from app.services.trading_day import trading_day_key


def test_trading_day_key_keeps_559pm_et_on_same_day():
    # 2026-03-02 17:59 ET (UTC-5) -> same trading day.
    assert trading_day_key(datetime(2026, 3, 2, 22, 59, tzinfo=timezone.utc)) == "2026-03-02"


def test_trading_day_key_rolls_600pm_et_to_next_day():
    # 2026-03-02 18:00 ET (UTC-5) -> next trading day.
    assert trading_day_key(datetime(2026, 3, 2, 23, 0, tzinfo=timezone.utc)) == "2026-03-03"


def test_trading_day_key_rolls_monday_609pm_et_to_tuesday():
    # Reported case: Monday 2026-03-02 18:09 ET should bucket as Tuesday.
    assert trading_day_key(datetime(2026, 3, 2, 23, 9, tzinfo=timezone.utc)) == "2026-03-03"
