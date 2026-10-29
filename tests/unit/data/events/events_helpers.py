"""Helpers shared by the Stage 12 event tests: fixture paths, instants and event rows."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from atlas.core.config import AtlasConfig
from atlas.data.events import EVENT_COLUMNS, MACRO_EVENT_COLUMNS

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "events"
REPO_ROOT = Path(__file__).resolve().parents[4]
NY = ZoneInfo("America/New_York")


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    """An aware UTC instant."""
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def et(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    """An aware New York instant."""
    return datetime(y, m, d, h, mi, tzinfo=NY)


def event_row(**fields: Any) -> dict[str, Any]:
    """One `events` row (`EVENT_COLUMNS`) with defaults for every field not given: an AAPL
    earnings 8-K accepted 2023-08-03 16:30 ET."""
    row: dict[str, Any] = {
        "event_id": "sec-0000320193-23-000075",
        "symbol": "AAPL",
        "event_type": "earnings",
        "event_date": date(2023, 8, 3),
        "event_time": utc(2023, 8, 3, 20, 30),
        "time_of_day": "AMC",
        "source": "sec_8k",
        "available_at": utc(2023, 8, 3, 20, 30),
    }
    unknown = set(fields) - set(EVENT_COLUMNS)
    if unknown:
        raise KeyError(f"unknown event fields {sorted(unknown)}")
    row.update(fields)
    return row


def macro_row(**fields: Any) -> dict[str, Any]:
    """One `macro_events` row (`MACRO_EVENT_COLUMNS`) with defaults: CPI on 2024-01-11 08:30 ET,
    scheduled (known from 2024-01-01 00:00 ET)."""
    row: dict[str, Any] = {
        "event_id": "cpi-2024-01-11",
        "event_type": "cpi",
        "event_date": date(2024, 1, 11),
        "event_time": utc(2024, 1, 11, 13, 30),
        "source": "fred_release",
        "available_at": utc(2024, 1, 1, 5, 0),
        "withdrawn_at": None,
    }
    unknown = set(fields) - set(MACRO_EVENT_COLUMNS)
    if unknown:
        raise KeyError(f"unknown macro event fields {sorted(unknown)}")
    row.update(fields)
    return row


def frame(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    """Rows as a frame with `columns` in order."""
    return pd.DataFrame(rows, columns=list(columns))


SEC_FIXTURES = FIXTURES.parent / "fundamentals" / "submissions"


def serve_sec(request: httpx.Request) -> httpx.Response:
    """respx side effect: an EDGAR submissions URL answered from `fixtures/events/submissions`
    (Stage 12) or, for history pages, `fixtures/fundamentals/submissions` (Stage 09)."""
    name = request.url.path.rsplit("/", 1)[-1]
    for folder in (FIXTURES / "submissions", SEC_FIXTURES):
        if (folder / name).exists():
            return httpx.Response(200, content=(folder / name).read_bytes())
    return httpx.Response(404)


def config_with(**events: Any) -> AtlasConfig:
    """`AtlasConfig()` with `data.events` fields replaced."""
    base = AtlasConfig()
    settings = base.data.events.model_copy(update=events)
    return base.model_copy(update={"data": base.data.model_copy(update={"events": settings})})


class NoSleep:
    """A fake `sleep` that records the requested pauses (seconds)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
