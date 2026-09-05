from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.services.trading_day import TRADING_TZ, futures_holiday_schedule, futures_session_is_open, trading_day_key


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


def test_equity_schedule_retains_historical_halt_before_its_elimination():
    before_halt = datetime(2021, 6, 25, 20, 14, tzinfo=timezone.utc)
    halt = datetime(2021, 6, 25, 20, 15, tzinfo=timezone.utc)
    after_halt = datetime(2021, 6, 25, 20, 30, tzinfo=timezone.utc)

    assert futures_session_is_open(before_halt, symbol="MNQ") is True
    assert futures_session_is_open(halt, symbol="CON.F.US.MNQ.U21") is False
    assert futures_session_is_open(after_halt, symbol="F.US.MNQ") is True
    assert futures_session_is_open(halt, symbol="UNKNOWN") is True


def test_equity_schedule_opens_former_halt_from_june_28_2021():
    for year, month, day in ((2021, 6, 28), (2026, 7, 6)):
        for minute in (15, 29, 30):
            assert futures_session_is_open(
                datetime(year, month, day, 20, minute, tzinfo=timezone.utc),
                symbol="MNQ",
            ) is True
    # The 17:00-18:00 ET maintenance break was not eliminated.
    assert futures_session_is_open(
        datetime(2021, 6, 28, 21, 0, tzinfo=timezone.utc), symbol="MNQ"
    ) is False


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


def test_unknown_root_fails_closed_for_obvious_major_holiday_closures():
    assert futures_session_is_open(
        datetime(2026, 12, 25, 15, 0, tzinfo=timezone.utc),
        symbol="UNMAPPED",
    ) is False
    assert futures_session_is_open(
        datetime(2026, 7, 3, 17, 0, tzinfo=timezone.utc),
        symbol="UNMAPPED",
    ) is False


def test_new_year_observed_on_prior_december_31_is_closed():
    # Jan 1, 2022 was Saturday, so the exchange holiday was observed Friday.
    assert futures_session_is_open(
        datetime(2021, 12, 31, 17, 0, tzinfo=timezone.utc),
        symbol="MNQ",
    ) is False


@pytest.mark.parametrize("year", [2019, 2023, 2024, 2025])
def test_verified_independence_eve_stops_at_1315_new_york_and_reopens_at_18(year):
    holiday = futures_holiday_schedule(date(year, 7, 3), symbol="MNQ")
    assert holiday is not None and holiday.early_close == time(13, 15)
    assert futures_session_is_open(datetime(year, 7, 3, 13, 14, tzinfo=TRADING_TZ), symbol="MNQ")
    assert not futures_session_is_open(datetime(year, 7, 3, 13, 15, tzinfo=TRADING_TZ), symbol="MNQ")
    assert futures_session_is_open(datetime(year, 7, 3, 18, tzinfo=TRADING_TZ), symbol="MNQ")


@pytest.mark.parametrize("day", [date(2020, 4, 10), date(2022, 4, 15), date(2024, 3, 29), date(2025, 4, 18)])
def test_verified_good_friday_full_close_also_excludes_previous_evening(day):
    holiday = futures_holiday_schedule(day, symbol="MNQ")
    assert holiday is not None and holiday.full_close
    friday = datetime.combine(day, time(), tzinfo=TRADING_TZ)
    assert not futures_session_is_open(friday - timedelta(hours=6), symbol="MNQ")
    assert not futures_session_is_open(friday + timedelta(hours=8), symbol="MNQ")
    assert futures_session_is_open(friday + timedelta(days=2, hours=18), symbol="MNQ")


@pytest.mark.parametrize("day", [date(2021, 4, 2), date(2023, 4, 7), date(2026, 4, 3)])
def test_employment_report_good_friday_sessions_retain_0915_close(day):
    holiday = futures_holiday_schedule(day, symbol="MNQ")
    assert holiday is not None and not holiday.full_close
    assert holiday.early_close == time(9, 15)
    assert futures_session_is_open(datetime.combine(day, time(9, 14), tzinfo=TRADING_TZ), symbol="MNQ")
    assert not futures_session_is_open(datetime.combine(day, time(9, 15), tzinfo=TRADING_TZ), symbol="MNQ")


def test_carter_day_of_mourning_is_an_0930_early_close_with_evening_reopen():
    day = date(2025, 1, 9)
    holiday = futures_holiday_schedule(day, symbol="MNQ")
    assert holiday is not None and holiday.early_close == time(9, 30)
    assert futures_session_is_open(datetime(2025, 1, 9, 9, 29, tzinfo=TRADING_TZ), symbol="MNQ")
    assert not futures_session_is_open(datetime(2025, 1, 9, 9, 30, tzinfo=TRADING_TZ), symbol="MNQ")
    assert futures_session_is_open(datetime(2025, 1, 9, 18, tzinfo=TRADING_TZ), symbol="MNQ")


def test_date_specific_equity_exceptions_do_not_invent_other_years_or_products():
    assert futures_holiday_schedule(date(2028, 7, 3), symbol="MNQ") is None
    assert futures_session_is_open(datetime(2025, 7, 3, 14, tzinfo=TRADING_TZ), symbol="UNKNOWN")
    assert futures_session_is_open(datetime(2025, 1, 9, 10, tzinfo=TRADING_TZ), symbol="UNKNOWN")


def test_research_clock_exits_july3_before_observed_exchange_close():
    from tools.fixtures.topbot_research import should_flatten

    entry = datetime(2025, 7, 3, 10, 5, tzinfo=TRADING_TZ)
    assert not should_flatten(entry, datetime(2025, 7, 3, 13, 9, tzinfo=TRADING_TZ), "orb30_both")
    assert should_flatten(entry, datetime(2025, 7, 3, 13, 10, tzinfo=TRADING_TZ), "orb30_both")
    assert not should_flatten(entry, datetime(2025, 7, 3, 13, 10, tzinfo=TRADING_TZ), "baseline_v5")
