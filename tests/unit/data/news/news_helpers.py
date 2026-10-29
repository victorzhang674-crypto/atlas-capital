"""Helpers shared by the Stage 11 news tests: fixture paths, raw article rows and a fake
provider that serves a fixed article frame."""

import io
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from atlas.core.logging import configure_logging, reset_logging
from atlas.data.news import RAW_COLUMNS, normalize_news

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "news"
REPO_ROOT = Path(__file__).resolve().parents[4]


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    """An aware UTC instant."""
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def article(**fields: Any) -> dict[str, Any]:
    """One raw provider row (`RAW_COLUMNS`) with defaults for every field not given."""
    row: dict[str, Any] = {
        "article_id": "alpaca-1",
        "published_at": utc(2020, 3, 2, 15, 0),
        "updated_at": None,
        "provider": "alpaca",
        "source": "benzinga",
        "headline": "Apple shares rise",
        "summary": "",
        "url": None,
        "symbols": ["AAPL"],
    }
    unknown = set(fields) - set(RAW_COLUMNS)
    if unknown:
        raise KeyError(f"unknown article fields {sorted(unknown)}")
    row.update(fields)
    return row


def raw_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Raw provider rows as a frame in `RAW_COLUMNS` order."""
    return pd.DataFrame(list(rows), columns=list(RAW_COLUMNS))


def articles_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Raw rows normalised into the article contract."""
    return normalize_news(raw_frame(rows))


class FakeNewsProvider:
    """A `NewsProvider` serving `frame` (article contract): rows tagged with a requested
    symbol (all rows for a market-wide request) published in `[start, end)`. `calls` records
    each request; `error` makes every call raise it."""

    def __init__(self, name: str, frame: pd.DataFrame, error: Exception | None = None) -> None:
        self.name = name
        self._frame = frame
        self._error = error
        self.calls: list[tuple[tuple[str, ...] | None, datetime, datetime]] = []

    def fetch_news(
        self, symbols: Sequence[str] | None, start: datetime, end: datetime
    ) -> pd.DataFrame:
        wanted = None if symbols is None else tuple(symbols)
        self.calls.append((wanted, start, end))
        if self._error is not None:
            raise self._error
        frame = self._frame
        inside = (frame["published_at"] >= start) & (frame["published_at"] < end)
        if wanted is not None:
            inside &= frame["symbols"].map(lambda tags: bool(set(tags) & set(wanted)))
        return frame.loc[inside].reset_index(drop=True)


def logged_events[T](log_file: Path, fn: Callable[[], T]) -> tuple[T, list[dict[str, Any]]]:
    """Run `fn` with Atlas logging sent to `log_file`; its result and the JSON log lines."""
    configure_logging(log_file=log_file, console=io.StringIO())
    try:
        result = fn()
    finally:
        reset_logging()
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    return result, lines
