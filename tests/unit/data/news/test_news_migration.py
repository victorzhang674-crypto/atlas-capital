"""A lake written by the version-1 news schema migrates and then accepts an edit."""

from datetime import date

import pandas as pd
from news_helpers import FakeNewsProvider, article, articles_frame, utc

from atlas.core.pit import AsOf
from atlas.core.storage import Lake, SchemaRegistry
from atlas.data.news import NEWS, News, ingest_news


def test_a_v1_news_table_keeps_its_rows_when_an_edit_is_ingested(tmp_path) -> None:
    published = utc(2020, 3, 2, 15)
    updated = utc(2020, 3, 2, 18)
    v1 = NEWS.model_copy(update={"version": 1, "primary_key": ("article_id", "published_at")})
    root = tmp_path / "lake"
    old = Lake(root, registry=SchemaRegistry([v1]))
    original = articles_frame([article(headline="Apple up", published_at=published)])
    old.write_table("news", original, mode="upsert")
    meta = (root / "curated" / "_meta.json").read_text(encoding="utf-8")
    assert '"version": 1' in meta

    current = Lake(root)
    assert current.migrate("news") == 2
    revised = articles_frame(
        [
            article(
                headline="Apple jumps 4%",
                published_at=published,
                updated_at=updated,
            )
        ]
    )
    ingest_news(
        current,
        FakeNewsProvider("alpaca", revised),
        None,
        utc(2020, 3, 1),
        utc(2020, 3, 4),
    )
    stored = current.read_table("news").sort_values("available_at")
    assert list(stored["headline"]) == ["Apple up", "Apple jumps 4%"]
    assert pd.Timestamp(stored["available_at"].iloc[0]) == pd.Timestamp(published)
    early = AsOf(day=date(2020, 3, 2), cutoff=utc(2020, 3, 2, 16))
    late = AsOf(day=date(2020, 3, 2), cutoff=utc(2020, 3, 2, 20))
    assert News(current).pit("AAPL", early)[0].text == "Apple up"
    assert News(current).pit("AAPL", late)[0].text == "Apple jumps 4%"
