"""`Lake.pending_migrations`: the stored tables whose schema version is behind the registry's
(the check behind `atlas data migrate`)."""

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from atlas.core.storage import Lake, SchemaRegistry
from atlas.data.news import NEWS, normalize_news
from atlas.data.news.base import RAW_COLUMNS


def old_news_lake(root: Path) -> Lake:
    """A lake whose `news` table was written by the version-1 schema (old primary key)."""
    v1 = NEWS.model_copy(update={"version": 1, "primary_key": ("article_id", "published_at")})
    lake = Lake(root, registry=SchemaRegistry([v1]))
    row = {
        "article_id": "alpaca-1",
        "published_at": datetime(2020, 3, 2, 15, tzinfo=UTC),
        "updated_at": None,
        "provider": "alpaca",
        "source": "benzinga",
        "headline": "Apple shares rise",
        "summary": "",
        "url": None,
        "symbols": ["AAPL"],
    }
    lake.write_table("news", normalize_news(pd.DataFrame([row], columns=list(RAW_COLUMNS))))
    return lake


def test_a_table_behind_the_registry_is_pending(tmp_path: Path) -> None:
    old_news_lake(tmp_path / "lake")
    assert Lake(tmp_path / "lake").pending_migrations() == {"news": (1, 2)}


def test_a_migrated_table_is_not_pending(tmp_path: Path) -> None:
    old_news_lake(tmp_path / "lake")
    current = Lake(tmp_path / "lake")
    current.migrate("news")
    assert current.pending_migrations() == {}


def test_an_empty_lake_has_nothing_pending(tmp_path: Path) -> None:
    assert Lake(tmp_path / "lake").pending_migrations() == {}
