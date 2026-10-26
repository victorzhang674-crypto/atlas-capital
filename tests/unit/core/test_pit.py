"""Tests for `atlas.core.pit`: `AsOf`, availability rules, `pit_filter`, `assert_no_lookahead`."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from atlas.core.calendar import MARKET_TZ, TradingCalendar
from atlas.core.clock import SimClock
from atlas.core.config import load_config
from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import (
    AVAILABILITY_RULES,
    AsOf,
    assert_no_lookahead,
    bar_available_at,
    pit_filter,
    to_available_at,
)


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    return TradingCalendar()


# --- AsOf -----------------------------------------------------------------------------------


def test_for_session_default_cutoff() -> None:
    as_of = AsOf.for_session(date(2024, 3, 1))
    assert as_of.day == date(2024, 3, 1)
    assert as_of.cutoff == datetime(2024, 3, 1, 13, 0, tzinfo=UTC)  # 08:00 EST
    assert as_of.cutoff.tzinfo == UTC


def test_for_session_custom_decision_time() -> None:
    as_of = AsOf.for_session(date(2024, 3, 1), decision_time=time(17, 0))
    assert as_of.cutoff == datetime(2024, 3, 1, 22, 0, tzinfo=UTC)


def test_cutoff_tracks_dst() -> None:  # Review focus 1
    assert AsOf.for_session(date(2024, 3, 11)).cutoff == datetime(2024, 3, 11, 12, 0, tzinfo=UTC)


def test_asof_validation() -> None:
    with pytest.raises(ValidationError):
        AsOf(day=date(2024, 3, 1), cutoff=datetime(2024, 3, 1, 13, 0))  # naive
    with pytest.raises(ValidationError, match="day"):
        # 03:00 UTC on 2 March is still 1 March in New York
        AsOf(day=date(2024, 3, 2), cutoff=datetime(2024, 3, 2, 3, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="naive"):
        AsOf.for_session(date(2024, 3, 1), decision_time=time(8, 0, tzinfo=UTC))


def test_asof_accepts_any_aware_timezone() -> None:
    cutoff = datetime(2024, 3, 1, 8, 0, tzinfo=MARKET_TZ)
    as_of = AsOf(day=date(2024, 3, 1), cutoff=cutoff)
    assert as_of.cutoff == datetime(2024, 3, 1, 13, 0, tzinfo=UTC)
    assert as_of.cutoff.tzinfo == UTC


def test_asof_is_frozen_and_hashable() -> None:
    a, b = AsOf.for_session(date(2024, 3, 1)), AsOf.for_session(date(2024, 3, 1))
    with pytest.raises(ValidationError):
        a.day = date(2024, 3, 4)  # type: ignore[misc]
    assert a == b
    assert hash(a) == hash(b)
    assert {a: "x"}[b] == "x"


def test_knows_is_strict() -> None:
    as_of = AsOf.for_session(date(2024, 3, 1))
    assert as_of.knows(as_of.cutoff) is False
    assert as_of.knows(as_of.cutoff - timedelta(microseconds=1)) is True
    assert as_of.knows(as_of.cutoff.astimezone(MARKET_TZ) - timedelta(seconds=1)) is True
    with pytest.raises(ValueError, match="naive"):
        as_of.knows(datetime(2024, 3, 1, 12, 0))


# --- availability rules ---------------------------------------------------------------------


def test_bar_not_visible_same_morning(cal: TradingCalendar) -> None:
    as_of = AsOf.for_session(date(2024, 3, 1))
    assert as_of.knows(bar_available_at(date(2024, 3, 1), cal)) is False


def test_bar_visible_next_session_morning(cal: TradingCalendar) -> None:
    as_of = AsOf.for_session(date(2024, 3, 4))
    assert as_of.knows(bar_available_at(date(2024, 3, 1), cal)) is True


def test_bar_available_at_regular_and_early_close(cal: TradingCalendar) -> None:  # focus 2
    assert bar_available_at(date(2024, 3, 1), cal) == datetime(2024, 3, 1, 21, 15, tzinfo=UTC)
    assert bar_available_at(date(2024, 11, 29), cal) == datetime(2024, 11, 29, 18, 15, tzinfo=UTC)
    assert bar_available_at(date(2024, 3, 1), cal, lag=timedelta(0)) == cal.session_close(
        date(2024, 3, 1)
    )
    with pytest.raises(ValueError, match="not a trading session"):
        bar_available_at(date(2024, 7, 4), cal)


def test_bar_available_at_rejects_negative_lag(cal: TradingCalendar) -> None:  # review
    with pytest.raises(ValueError, match="lag"):
        bar_available_at(date(2024, 3, 1), cal, lag=timedelta(hours=-9))


def test_loaded_calendar_config_changes_behaviour(  # review I1
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("ATLAS__CALENDAR__BAR_PUBLISH_LAG_MINUTES", "60")
    monkeypatch.setenv("ATLAS__CALENDAR__DECISION_TIME", "09:30")
    settings = load_config(config_dir=tmp_path / "config").calendar
    cal = TradingCalendar(config=settings)
    day = date(2024, 3, 1)
    assert bar_available_at(day, cal) == datetime(2024, 3, 1, 22, 0, tzinfo=UTC)  # close + 60m
    assert AsOf.for_session(day, config=settings).cutoff == datetime(2024, 3, 1, 14, 30, tzinfo=UTC)
    assert SimClock(start=day, calendar=cal).now() == datetime(2024, 3, 1, 14, 30, tzinfo=UTC)
    assert cal.config == settings


def test_to_available_at() -> None:
    ts = datetime(2024, 3, 1, 16, 5, tzinfo=MARKET_TZ)
    out = to_available_at(ts)
    assert out == ts
    assert out.tzinfo == UTC
    with pytest.raises(ValueError, match="naive"):
        to_available_at(datetime(2024, 3, 1, 16, 5))


def test_availability_rules_cover_all_sources() -> None:
    assert set(AVAILABILITY_RULES) == {"daily_bar", "sec_filing", "fred_observation", "news"}
    assert all(rule.strip() for rule in AVAILABILITY_RULES.values())


# --- pit_filter / assert_no_lookahead -------------------------------------------------------

AS_OF = AsOf.for_session(date(2024, 3, 4))  # cutoff 2024-03-04 13:00 UTC
STAMPS = [
    "2024-03-01 21:15:00",  # a: Friday's bar, known by Monday morning
    "2024-03-04 12:59:59",  # b: one second before the cutoff
    "2024-03-04 13:00:00",  # c: exactly at the cutoff -> not known
    "2024-03-04 21:15:00",  # d: Monday's own bar -> future
]


def _frame(stamps: pd.Series | pd.DatetimeIndex, col: str = "available_at") -> pd.DataFrame:
    return pd.DataFrame({"value": [1.0, 2.0, 3.0, 4.0], col: stamps}, index=list("abcd"))


@pytest.fixture
def df() -> pd.DataFrame:
    return _frame(pd.to_datetime(STAMPS, utc=True))


def test_pit_filter_keeps_only_known_rows(df: pd.DataFrame) -> None:
    before = df.copy()
    out = pit_filter(df, AS_OF)
    assert list(out.index) == ["a", "b"]
    assert list(out.columns) == ["value", "available_at"]
    assert out is not df
    pd.testing.assert_frame_equal(df, before)  # input untouched


def test_pit_filter_empty_frame() -> None:
    empty = pd.DataFrame({"available_at": pd.to_datetime([], utc=True), "value": []})
    out = pit_filter(empty, AS_OF)
    assert out.empty
    assert list(out.columns) == ["available_at", "value"]


def test_pit_filter_mixed_timezones() -> None:  # Review focus 3
    ny = _frame(pd.to_datetime(STAMPS, utc=True).tz_convert("America/New_York"))
    assert list(pit_filter(ny, AS_OF).index) == ["a", "b"]


def test_pit_filter_custom_time_col() -> None:
    frame = _frame(pd.to_datetime(STAMPS, utc=True), col="accepted_at")
    assert list(pit_filter(frame, AS_OF, time_col="accepted_at").index) == ["a", "b"]


def test_pit_filter_rejects_naive_column() -> None:  # Review focus 3
    naive = _frame(pd.to_datetime(STAMPS))
    with pytest.raises(DataError, match="available_at"):
        pit_filter(naive, AS_OF)
    with pytest.raises(DataError, match="available_at"):
        assert_no_lookahead(naive, AS_OF)


def test_pit_filter_rejects_non_datetime_column() -> None:  # Review focus 3
    with pytest.raises(DataError, match="available_at"):
        pit_filter(_frame(pd.Series(STAMPS, index=list("abcd"))), AS_OF)
    aware_objects = pd.Series(
        [datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=MARKET_TZ)] * 2,
        index=list("abcd"),
        dtype=object,
    )
    with pytest.raises(DataError, match="available_at"):
        pit_filter(_frame(aware_objects), AS_OF)


def test_missing_time_col_raises(df: pd.DataFrame) -> None:
    with pytest.raises(DataError, match="available_at"):
        pit_filter(df.drop(columns="available_at"), AS_OF)
    with pytest.raises(DataError, match="available_at"):
        assert_no_lookahead(df.drop(columns="available_at"), AS_OF)


def test_nat_rows_are_never_visible() -> None:  # Review focus 4
    frame = _frame(pd.to_datetime([STAMPS[0], None, STAMPS[1], None], utc=True))
    assert list(pit_filter(frame, AS_OF).index) == ["a", "c"]
    with pytest.raises(LookAheadError, match="2 row"):
        assert_no_lookahead(frame, AS_OF)


def test_assert_no_lookahead_passes_on_filtered(df: pd.DataFrame) -> None:
    assert assert_no_lookahead(pit_filter(df, AS_OF), AS_OF) is None


def test_assert_no_lookahead_raises_on_future_row(df: pd.DataFrame) -> None:
    with pytest.raises(LookAheadError, match=r"2 row.*2024-03-04 21:15"):
        assert_no_lookahead(df, AS_OF)


def test_injected_future_bar_is_caught(cal: TradingCalendar) -> None:
    """Stage protocol §5 look-ahead test: a bar from the decision day itself is future data."""
    sessions = [date(2024, 2, 28), date(2024, 2, 29), date(2024, 3, 1)]
    bars = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0],
            "available_at": pd.to_datetime([bar_available_at(d, cal) for d in sessions], utc=True),
        }
    )
    assert_no_lookahead(bars, AS_OF)  # all past bars are fine
    future = pd.DataFrame(
        {
            "close": [103.0],
            "available_at": pd.to_datetime([bar_available_at(date(2024, 3, 4), cal)], utc=True),
        }
    )
    injected = pd.concat([bars, future], ignore_index=True)
    with pytest.raises(LookAheadError):
        assert_no_lookahead(injected, AS_OF)
    assert len(pit_filter(injected, AS_OF)) == 3
