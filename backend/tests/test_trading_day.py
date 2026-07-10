from datetime import date, datetime, time, timezone

from app.services.trading_day import (
    futures_holiday_schedule,
    futures_session_intervals_utc,
    is_expected_futures_candle,
    is_futures_market_open,
    trading_day_key,
)


def test_trading_day_key_keeps_559pm_et_on_same_day():
    # 2026-03-02 17:59 ET (UTC-5) -> same trading day.
    assert trading_day_key(datetime(2026, 3, 2, 22, 59, tzinfo=timezone.utc)) == "2026-03-02"


def test_trading_day_key_rolls_600pm_et_to_next_day():
    # 2026-03-02 18:00 ET (UTC-5) -> next trading day.
    assert trading_day_key(datetime(2026, 3, 2, 23, 0, tzinfo=timezone.utc)) == "2026-03-03"


def test_trading_day_key_rolls_monday_609pm_et_to_tuesday():
    # Reported case: Monday 2026-03-02 18:09 ET should bucket as Tuesday.
    assert trading_day_key(datetime(2026, 3, 2, 23, 9, tzinfo=timezone.utc)) == "2026-03-03"


def test_cme_equity_session_excludes_daily_halt_and_maintenance():
    symbol = "F.US.MNQ"

    assert is_futures_market_open(datetime(2026, 6, 9, 20, 14, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 9, 20, 15, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 9, 20, 29, tzinfo=timezone.utc), symbol=symbol)
    assert is_futures_market_open(datetime(2026, 6, 9, 20, 30, tzinfo=timezone.utc), symbol=symbol)
    assert is_futures_market_open(datetime(2026, 6, 9, 20, 59, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 9, 21, 59, tzinfo=timezone.utc), symbol=symbol)
    assert is_futures_market_open(datetime(2026, 6, 9, 22, 0, tzinfo=timezone.utc), symbol=symbol)


def test_cme_equity_session_excludes_weekend():
    symbol = "CON.F.US.MNQ.M26"

    assert is_futures_market_open(datetime(2026, 6, 5, 20, 59, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 5, 21, 0, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 6, 15, 0, tzinfo=timezone.utc), symbol=symbol)
    assert not is_futures_market_open(datetime(2026, 6, 7, 21, 59, tzinfo=timezone.utc), symbol=symbol)
    assert is_futures_market_open(datetime(2026, 6, 7, 22, 0, tzinfo=timezone.utc), symbol=symbol)


def test_cme_equity_sunday_open_is_dst_safe():
    # March 1 is EST (18:00 ET == 23:00 UTC); March 8 is EDT
    # (18:00 ET == 22:00 UTC) after the spring transition.
    assert not is_futures_market_open(datetime(2026, 3, 1, 22, 59, tzinfo=timezone.utc), symbol="MNQ")
    assert is_futures_market_open(datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc), symbol="MNQ")
    assert not is_futures_market_open(datetime(2026, 3, 8, 21, 59, tzinfo=timezone.utc), symbol="MNQ")
    assert is_futures_market_open(datetime(2026, 3, 8, 22, 0, tzinfo=timezone.utc), symbol="MNQ")

    # The November 1 fall transition returns the Sunday open to 23:00 UTC.
    assert not is_futures_market_open(datetime(2026, 11, 1, 22, 59, tzinfo=timezone.utc), symbol="MNQ")
    assert is_futures_market_open(datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc), symbol="MNQ")


def test_cme_equity_recurring_holiday_rules_report_full_and_early_closes():
    mlk = futures_holiday_schedule(date(2026, 1, 19), symbol="MNQ")
    good_friday = futures_holiday_schedule(date(2026, 4, 3), symbol="MNQ")
    independence_eve = futures_holiday_schedule(date(2024, 7, 3), symbol="MNQ")
    independence_day = futures_holiday_schedule(date(2024, 7, 4), symbol="MNQ")
    thanksgiving = futures_holiday_schedule(date(2026, 11, 26), symbol="MNQ")
    thanksgiving_friday = futures_holiday_schedule(date(2026, 11, 27), symbol="MNQ")
    christmas_eve = futures_holiday_schedule(date(2026, 12, 24), symbol="MNQ")
    christmas = futures_holiday_schedule(date(2026, 12, 25), symbol="MNQ")

    assert mlk is not None and mlk.early_close == time(13, 0) and not mlk.full_close
    assert good_friday is not None and good_friday.early_close == time(9, 15)
    assert independence_eve is not None and independence_eve.early_close == time(13, 15)
    assert independence_day is not None and independence_day.early_close == time(13, 0)
    assert thanksgiving is not None and thanksgiving.early_close == time(13, 0)
    assert thanksgiving_friday is not None and thanksgiving_friday.early_close == time(13, 15)
    assert christmas_eve is not None and christmas_eve.early_close == time(13, 15)
    assert christmas is not None and christmas.full_close and christmas.early_close is None


def test_cme_equity_holiday_closes_are_applied_to_market_open_checks():
    # MLK Day closes at 13:00 ET and the next trading date opens at 18:00 ET.
    assert is_futures_market_open(datetime(2026, 1, 19, 17, 59, tzinfo=timezone.utc), symbol="MNQ")
    assert not is_futures_market_open(datetime(2026, 1, 19, 18, 0, tzinfo=timezone.utc), symbol="MNQ")
    assert is_futures_market_open(datetime(2026, 1, 19, 23, 0, tzinfo=timezone.utc), symbol="MNQ")

    # Good Friday closes at 09:15 ET in the contemporary schedule.
    assert is_futures_market_open(datetime(2026, 4, 3, 13, 14, tzinfo=timezone.utc), symbol="MNQ")
    assert not is_futures_market_open(datetime(2026, 4, 3, 13, 15, tzinfo=timezone.utc), symbol="MNQ")

    # Christmas is a full close: the prior-evening session never opens.
    assert not is_futures_market_open(datetime(2026, 12, 24, 23, 0, tzinfo=timezone.utc), symbol="MNQ")
    assert not is_futures_market_open(datetime(2026, 12, 25, 16, 0, tzinfo=timezone.utc), symbol="MNQ")


def test_cme_equity_session_intervals_are_half_open_and_exclude_halt():
    assert futures_session_intervals_utc(date(2026, 6, 9), symbol="MNQ") == (
        (
            datetime(2026, 6, 8, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 9, 20, 15, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 6, 9, 20, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc),
        ),
    )
    assert futures_session_intervals_utc(date(2026, 12, 25), symbol="MNQ") == ()


def test_expected_candle_requires_bucket_to_overlap_open_session_time():
    def expected(start: datetime, end: datetime) -> bool:
        return is_expected_futures_candle(start, end, symbol="MNQ")

    # A bucket wholly inside the equity halt or maintenance break is scheduled empty.
    assert not expected(
        datetime(2026, 6, 9, 20, 15, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 20, 30, tzinfo=timezone.utc),
    )
    assert not expected(
        datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 22, 0, tzinfo=timezone.utc),
    )

    # A larger candle that has any actual session time remains expected.
    assert expected(
        datetime(2026, 6, 9, 20, 10, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 20, 20, tzinfo=timezone.utc),
    )
    assert expected(
        datetime(2026, 6, 9, 21, 55, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 22, 5, tzinfo=timezone.utc),
    )

    assert not expected(
        datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc),
    )
    assert not expected(
        datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 14, 5, tzinfo=timezone.utc),
    )


def test_known_non_equity_symbol_does_not_inherit_equity_only_halt_or_holidays():
    # Product-specific special schedules differ. Until a metals calendar is
    # modeled, MGC gets only the common Globex week and 17:00-18:00 maintenance.
    assert is_futures_market_open(datetime(2026, 6, 9, 20, 20, tzinfo=timezone.utc), symbol="F.US.MGC")
    assert futures_holiday_schedule(date(2026, 1, 19), symbol="MGC") is None
