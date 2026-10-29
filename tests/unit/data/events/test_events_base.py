"""Stage 12 Task 2: the event contracts, the `events` / `macro_events` tables, time of day."""

from datetime import date, datetime

import pandas as pd
import pytest
from events_helpers import event_row, frame, macro_row, utc

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY
from atlas.data.events import (
    EVENT_COLUMNS,
    EVENT_VIEW_COLUMNS,
    EVENTS,
    MACRO_EVENT_COLUMNS,
    MACRO_EVENTS,
    classify_time_of_day,
    empty_events,
    empty_macro_events,
    normalize_events,
    normalize_macro_events,
)

CAL = TradingCalendar()


def test_events_table_declaration() -> None:
    assert EVENTS.name == "events"
    assert EVENTS.column_names == EVENT_COLUMNS
    assert EVENTS.primary_key == ("symbol", "event_id", "available_at")
    assert EVENTS.partition_by == ("symbol",)
    assert EVENTS.column("event_time").nullable
    assert not EVENTS.column("available_at").nullable


def test_macro_events_table_declaration() -> None:
    assert MACRO_EVENTS.name == "macro_events"
    assert MACRO_EVENTS.column_names == MACRO_EVENT_COLUMNS
    assert MACRO_EVENTS.primary_key == ("event_id",)
    assert MACRO_EVENTS.column("withdrawn_at").nullable
    assert (*EVENT_COLUMNS, "withdrawn_at") == EVENT_VIEW_COLUMNS


def test_import_registers_event_tables() -> None:
    assert "events" in DEFAULT_REGISTRY.names()
    assert "macro_events" in DEFAULT_REGISTRY.names()


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (utc(2023, 8, 3, 20, 30), "AMC"),  # 16:30 EDT
        (utc(2023, 8, 3, 13, 0), "BMO"),  # 09:00 EDT
        (utc(2023, 8, 3, 13, 30), "unknown"),  # 09:30 EDT: the open itself
        (utc(2023, 8, 3, 15, 0), "unknown"),  # 11:00 EDT: during the session
        (utc(2023, 8, 3, 20, 0), "AMC"),  # 16:00 EDT: the close itself
        (utc(2024, 11, 29, 18, 30), "AMC"),  # 13:30 EST after the 13:00 early close
        (utc(2024, 2, 1, 21, 30), "AMC"),  # 16:30 EST
        (utc(2023, 8, 5, 14, 0), "unknown"),  # Saturday
        (None, "unknown"),
        (pd.NaT, "unknown"),
    ],
)
def test_classify_time_of_day(instant: datetime | None, expected: str) -> None:
    assert classify_time_of_day(instant, CAL) == expected


def test_classify_time_of_day_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive"):
        classify_time_of_day(datetime(2023, 8, 3, 16, 30), CAL)


def test_normalize_events_contract() -> None:
    rows = [
        event_row(),
        event_row(
            event_id="yahoo-AAPL-2023-11-02",
            symbol="aapl",
            event_date=date(2023, 11, 2),
            event_time=None,
            time_of_day="unknown",
            source="yahoo",
            available_at=utc(2023, 10, 1, 12),
        ),
    ]
    out = normalize_events(frame(rows, EVENT_COLUMNS))
    assert tuple(out.columns) == EVENT_COLUMNS
    assert list(out["symbol"]) == ["AAPL", "AAPL"]
    assert list(out["event_date"]) == [date(2023, 8, 3), date(2023, 11, 2)]
    assert str(out["event_time"].dtype) == "datetime64[us, UTC]"
    assert str(out["available_at"].dtype) == "datetime64[us, UTC]"
    assert pd.isna(out["event_time"].iloc[1])
    assert out.index.tolist() == [0, 1]


@pytest.mark.parametrize(
    ("fields", "pattern"),
    [
        ({"available_at": datetime(2023, 8, 3, 20, 30)}, "timezone-aware"),
        ({"event_time": datetime(2023, 8, 3, 20, 30)}, "timezone-aware"),
        ({"available_at": None}, "available_at is missing"),
        ({"time_of_day": "noon"}, "time_of_day"),
        ({"event_id": ""}, "event_id"),
    ],
)
def test_normalize_events_rejects(fields: dict[str, object], pattern: str) -> None:
    with pytest.raises(DataError, match=pattern):
        normalize_events(frame([event_row(**fields)], EVENT_COLUMNS))


def test_normalize_events_missing_column() -> None:
    with pytest.raises(DataError, match="missing columns"):
        normalize_events(frame([event_row()], EVENT_COLUMNS).drop(columns=["source"]))


def test_normalize_macro_events_contract() -> None:
    rows = [
        macro_row(),
        macro_row(
            event_id="fomc-2020-03-18",
            event_type="fomc",
            event_date=date(2020, 3, 18),
            event_time=utc(2020, 3, 18, 18),
            source="fomc_calendar",
            available_at=utc(2020, 1, 1, 5),
            withdrawn_at=utc(2020, 3, 15, 21),
        ),
    ]
    out = normalize_macro_events(frame(rows, MACRO_EVENT_COLUMNS))
    assert tuple(out.columns) == MACRO_EVENT_COLUMNS
    assert list(out["event_id"]) == ["fomc-2020-03-18", "cpi-2024-01-11"]
    assert str(out["withdrawn_at"].dtype) == "datetime64[us, UTC]"


def test_empty_frames_have_contract_columns() -> None:
    assert tuple(empty_events().columns) == EVENT_COLUMNS
    assert tuple(empty_macro_events().columns) == MACRO_EVENT_COLUMNS
    assert empty_events().empty and empty_macro_events().empty
