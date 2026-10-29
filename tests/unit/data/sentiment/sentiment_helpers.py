"""Helpers shared by the Stage 12 sentiment tests: fixture paths, the sample lexicon and news
article rows."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from atlas.data.news import RAW_COLUMNS, normalize_news
from atlas.data.sentiment.lexicon import Lexicon, build_lm_lexicon, load_lexicon

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "sentiment"
SAMPLE_MASTER = FIXTURES / "lm_master_sample.csv"


def utc(y: int, m: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    """An aware UTC instant."""
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def sample_lexicon(folder: Path) -> Lexicon:
    """The lexicon built from the master-dictionary excerpt, written to `folder` and loaded."""
    path = folder / "lm_lexicon.csv"
    build_lm_lexicon(pd.read_csv(SAMPLE_MASTER)).to_csv(path, index=False)
    return load_lexicon(path)


def article(**fields: Any) -> dict[str, Any]:
    """One raw news row (`RAW_COLUMNS`) with defaults for every field not given."""
    row: dict[str, Any] = {
        "article_id": "alpaca-1",
        "published_at": utc(2024, 3, 1, 15),
        "updated_at": None,
        "provider": "alpaca",
        "source": "benzinga",
        "headline": "Apple posts strong gains",
        "summary": "",
        "url": None,
        "symbols": ["AAPL"],
    }
    unknown = set(fields) - set(RAW_COLUMNS)
    if unknown:
        raise KeyError(f"unknown article fields {sorted(unknown)}")
    row.update(fields)
    return row


def articles(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Raw rows normalised into the Stage 11 article contract."""
    return normalize_news(pd.DataFrame(list(rows), columns=list(RAW_COLUMNS)))
