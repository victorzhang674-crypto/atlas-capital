"""Stage 11 Task 6: de-duplication by content hash and fuzzy headline within 48 hours."""

import random
from datetime import timedelta

import pandas as pd
from news_helpers import article, articles_frame, utc
from rapidfuzz import fuzz

from atlas.core.config import NewsConfig
from atlas.data.news import DUPLICATE_OF, empty_news, mark_duplicates, normalize_text


def _dups(frame: pd.DataFrame, **cfg: float) -> dict[str, str | None]:
    out = mark_duplicates(frame, NewsConfig(**cfg))  # type: ignore[arg-type]
    return dict(zip(out["article_id"], out[DUPLICATE_OF], strict=True))


def test_same_story_two_sources_is_one() -> None:
    frame = articles_frame(
        [
            article(
                article_id="yahoo-r1",
                provider="yahoo",
                source="Reuters",
                published_at=utc(2020, 3, 2, 16, 5),
                headline="Apple shares rise as analysts shrug off virus hit",
            ),
            article(
                article_id="alpaca-15000004",
                published_at=utc(2020, 3, 2, 16, 0),
                headline="Apple Shares Rise As Analysts Shrug Off Virus Hit",
                summary="Analysts said the supply disruption would be temporary.",
            ),
        ]
    )
    assert _dups(frame) == {"alpaca-15000004": None, "yahoo-r1": "alpaca-15000004"}


def test_exact_hash_match() -> None:
    frame = articles_frame(
        [
            article(article_id="a-1", headline="Fed cuts rates", summary="By half a point."),
            article(
                article_id="a-2",
                published_at=utc(2020, 3, 3),
                headline="FED CUTS RATES!",
                summary="by half a point",
            ),
        ]
    )
    assert frame.loc[0, "content_hash"] == frame.loc[1, "content_hash"]
    assert _dups(frame, fuzzy_threshold=100) == {"a-1": None, "a-2": "a-1"}


def test_fuzzy_threshold_boundary() -> None:
    first, second = "Apple Q1 revenue beats estimates", "Apple Q1 revenues beat estimate"
    score = fuzz.ratio(normalize_text(first), normalize_text(second))
    assert 80 < score < 100
    frame = articles_frame(
        [
            article(article_id="a-1", headline=first),
            article(article_id="a-2", headline=second, published_at=utc(2020, 3, 2, 16)),
        ]
    )
    assert _dups(frame, fuzzy_threshold=score)["a-2"] == "a-1"
    assert _dups(frame, fuzzy_threshold=score + 0.01)["a-2"] is None


def test_default_threshold_keeps_different_stories_apart() -> None:
    frame = articles_frame(
        [
            article(article_id="a-1", headline="Apple to reopen China stores"),
            article(
                article_id="a-2",
                headline="Microsoft says Surface supply hit by outbreak",
                published_at=utc(2020, 3, 2, 16),
            ),
        ]
    )
    assert _dups(frame) == {"a-1": None, "a-2": None}


def test_outside_48h_not_duplicate() -> None:
    t0 = utc(2020, 3, 2, 15)
    rows = [
        article(article_id="a-1", published_at=t0),
        article(article_id="a-2", published_at=t0 + timedelta(hours=48)),
        article(article_id="a-3", published_at=t0 + timedelta(hours=96, seconds=1)),
    ]
    assert _dups(articles_frame(rows)) == {"a-1": None, "a-2": "a-1", "a-3": None}


def test_later_copy_is_the_duplicate_whatever_the_input_order() -> None:
    rows = [
        article(article_id="z-late", published_at=utc(2020, 3, 2, 16)),
        article(article_id="b-early", published_at=utc(2020, 3, 2, 15)),
    ]
    frame = articles_frame(rows).iloc[::-1].reset_index(drop=True)
    out = mark_duplicates(frame)
    assert list(out["article_id"]) == list(frame["article_id"])  # input order kept
    assert _dups(frame) == {"b-early": None, "z-late": "b-early"}


def test_tie_on_time_breaks_by_article_id() -> None:
    rows = [article(article_id="yahoo-x"), article(article_id="alpaca-x")]
    assert _dups(articles_frame(rows)) == {"alpaca-x": None, "yahoo-x": "alpaca-x"}


def test_chain_points_to_root() -> None:
    t0 = utc(2020, 3, 2)
    rows = [
        article(article_id="a-1", published_at=t0),
        article(article_id="a-2", published_at=t0 + timedelta(hours=40)),
        article(article_id="a-3", published_at=t0 + timedelta(hours=80)),
    ]
    assert _dups(articles_frame(rows)) == {"a-1": None, "a-2": "a-1", "a-3": "a-1"}


def test_punctuation_only_headlines_not_fuzzy_matched() -> None:
    rows = [
        article(article_id="a-1", headline="!!!", summary="one"),
        article(article_id="a-2", headline="???", summary="two", published_at=utc(2020, 3, 2, 16)),
    ]
    assert _dups(articles_frame(rows)) == {"a-1": None, "a-2": None}


def test_window_independent() -> None:
    rng = random.Random(11)
    stories = [f"Story {i} about company {rng.randint(0, 50)} and topic {i * 7}" for i in range(40)]
    t0 = utc(2020, 1, 1)
    rows = [
        article(
            article_id=f"a-{i:03d}",
            published_at=t0 + timedelta(hours=rng.uniform(0, 24 * 20)),
            headline=rng.choice(stories) + rng.choice(["", "!", " update", " (Reuters)"]),
        )
        for i in range(200)
    ]
    full = mark_duplicates(articles_frame(rows))
    assert full[DUPLICATE_OF].notna().sum() > 20  # the generated set has many duplicates
    window_start = t0 + timedelta(days=10)
    context = window_start - timedelta(hours=48)
    part = mark_duplicates(full.loc[full["available_at"] >= context].drop(columns=DUPLICATE_OF))
    inside_full = full.loc[full["available_at"] >= window_start]
    inside_part = part.loc[part["available_at"] >= window_start]
    assert list(inside_full["article_id"]) == list(inside_part["article_id"])
    assert list(inside_full[DUPLICATE_OF].notna()) == list(inside_part[DUPLICATE_OF].notna())


def test_empty_frame() -> None:
    out = mark_duplicates(empty_news())
    assert out.empty and DUPLICATE_OF in out.columns
