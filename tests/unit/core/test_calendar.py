"""Tests for `atlas.core.calendar`: XNYS sessions, offsets, open/close times, early closes."""

from datetime import UTC, date, datetime

import pytest

from atlas.core.calendar import TradingCalendar, market_date


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar()


def test_market_date() -> None:
    assert market_date(datetime(2024, 3, 1, 3, 0, tzinfo=UTC)) == date(2024, 2, 29)
    assert market_date(datetime(2024, 3, 1, 13, 0, tzinfo=UTC)) == date(2024, 3, 1)
    with pytest.raises(ValueError, match="naive"):
        market_date(datetime(2024, 3, 1, 13, 0))


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2024, 7, 4), False),  # Independence Day
        (date(2024, 3, 29), False),  # Good Friday
        (date(2024, 3, 30), False),  # Saturday
        (date(2024, 7, 3), True),
        (date(2024, 3, 28), True),
    ],
)
def test_holidays_are_not_sessions(cal: TradingCalendar, day: date, expected: bool) -> None:
    assert cal.is_session(day) is expected


def test_sessions_inclusive_range(cal: TradingCalendar) -> None:
    assert cal.sessions(date(2024, 3, 28), date(2024, 4, 2)) == (
        date(2024, 3, 28),
        date(2024, 4, 1),
        date(2024, 4, 2),
    )
    assert cal.sessions(date(2024, 3, 30), date(2024, 3, 31)) == ()


def test_next_prev_session(cal: TradingCalendar) -> None:
    assert cal.next_session(date(2024, 3, 28)) == date(2024, 4, 1)
    assert cal.next_session(date(2024, 3, 30)) == date(2024, 4, 1)
    assert cal.prev_session(date(2024, 4, 1)) == date(2024, 3, 28)
    assert cal.prev_session(date(2024, 3, 30)) == date(2024, 3, 28)


@pytest.mark.parametrize(
    ("day", "n", "expected"),
    [
        (date(2024, 3, 1), 1, date(2024, 3, 4)),  # Fri → Mon
        (date(2024, 3, 4), -1, date(2024, 3, 1)),  # Mon → Fri
        (date(2024, 4, 1), -1, date(2024, 3, 28)),  # skips Good Friday + weekend
        (date(2024, 7, 3), 1, date(2024, 7, 5)),  # skips July 4
        (date(2024, 3, 1), -21, date(2024, 1, 31)),  # Feb 2024 has 20 sessions
        (date(2024, 3, 1), 0, date(2024, 3, 1)),
    ],
)
def test_offset_across_weekends_and_holidays(
    cal: TradingCalendar, day: date, n: int, expected: date
) -> None:
    assert cal.offset(day, n) == expected


def test_offset_rejects_non_session(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="not a trading session"):
        cal.offset(date(2024, 3, 30), 1)


def test_regular_open_close(cal: TradingCalendar) -> None:
    assert cal.session_open(date(2024, 3, 1)) == datetime(2024, 3, 1, 14, 30, tzinfo=UTC)
    assert cal.session_close(date(2024, 3, 1)) == datetime(2024, 3, 1, 21, 0, tzinfo=UTC)
    assert cal.session_close(date(2024, 3, 1)).tzinfo == UTC
    assert cal.is_early_close(date(2024, 3, 1)) is False


def test_session_close_tracks_dst(cal: TradingCalendar) -> None:  # Review focus 1
    assert cal.session_close(date(2024, 3, 11)) == datetime(2024, 3, 11, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "close"),
    [
        (date(2024, 11, 29), datetime(2024, 11, 29, 18, 0, tzinfo=UTC)),  # day after Thanksgiving
        (date(2024, 7, 3), datetime(2024, 7, 3, 17, 0, tzinfo=UTC)),
        (date(2024, 12, 24), datetime(2024, 12, 24, 18, 0, tzinfo=UTC)),
    ],
)
def test_early_closes(cal: TradingCalendar, day: date, close: datetime) -> None:
    assert cal.session_close(day) == close
    assert cal.is_early_close(day) is True


def test_open_close_reject_non_session(cal: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="not a trading session"):
        cal.session_close(date(2024, 7, 4))
    with pytest.raises(ValueError, match="not a trading session"):
        cal.session_open(date(2024, 7, 4))


def test_out_of_bounds_raises() -> None:
    small = TradingCalendar(start=date(2020, 1, 1), end=date(2020, 12, 31))
    with pytest.raises(ValueError, match="outside the calendar"):
        small.is_session(date(2021, 1, 4))
    with pytest.raises(ValueError, match="outside the calendar"):
        small.next_session(date(2020, 12, 31))  # the next session is past `end`


def test_default_bounds_from_config(cal: TradingCalendar) -> None:
    assert cal.is_session(date(2000, 1, 3)) is True
    assert cal.is_session(date(2030, 12, 31)) is True


# --- Phase 0 checkpoint, Tier 2 -------------------------------------------------------------

_INSTANT = datetime(2024, 3, 1, 15, 0, tzinfo=UTC)

_DATE_METHODS = {
    "is_session": lambda c, d: c.is_session(d),
    "sessions": lambda c, d: c.sessions(d, date(2024, 3, 8)),
    "next_session": lambda c, d: c.next_session(d),
    "prev_session": lambda c, d: c.prev_session(d),
    "offset": lambda c, d: c.offset(d, 1),
    "session_open": lambda c, d: c.session_open(d),
    "session_close": lambda c, d: c.session_close(d),
    "is_early_close": lambda c, d: c.is_early_close(d),
}


@pytest.mark.parametrize("method", sorted(_DATE_METHODS))
def test_datetime_rejected_with_market_date_hint(cal: TradingCalendar, method: str) -> None:
    # `datetime` subclasses `date`; without the check this raised "can't compare datetime".
    with pytest.raises(TypeError, match=r"market_date\(\)"):
        _DATE_METHODS[method](cal, _INSTANT)
    _DATE_METHODS[method](cal, market_date(_INSTANT))  # the hinted fix works


def test_datetime_bounds_rejected() -> None:
    with pytest.raises(TypeError, match=r"market_date\(\)"):
        TradingCalendar(start=_INSTANT, end=date(2024, 12, 31))
    with pytest.raises(TypeError, match=r"market_date\(\)"):
        TradingCalendar(start=date(2024, 1, 1), end=datetime(2024, 12, 31, 15, tzinfo=UTC))


def test_dst_fall_back_close(cal: TradingCalendar) -> None:
    # Clocks go back on Sunday 2024-11-03: the close moves from 20:00 to 21:00 UTC.
    assert cal.session_close(date(2024, 11, 1)) == datetime(2024, 11, 1, 20, 0, tzinfo=UTC)
    assert cal.session_close(date(2024, 11, 4)) == datetime(2024, 11, 4, 21, 0, tzinfo=UTC)
