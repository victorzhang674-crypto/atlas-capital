"""The native `llm_cache` table in `atlas.duckdb` (Stage 25): one row per recorded LLM answer."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from atlas.core.errors import DataError
from atlas.core.storage import Database, Lake
from atlas.core.storage.db import LLM_CACHE_COLUMNS

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(lake: Lake) -> Iterator[Database]:
    database = Database(lake)
    yield database
    database.close()


def row(key: str = "k1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "key": key,
        "created_at": T0,
        "provider": "fake",
        "model": "deepseek-flash",
        "request_json": '{"messages":[]}',
        "response_json": '{"content":"hello"}',
        "usage": '{"input_tokens":3,"cost_usd":0.001}',
    }
    values.update(overrides)
    return values


def test_columns_are_the_documented_ones() -> None:
    assert tuple(row()) == LLM_CACHE_COLUMNS


def test_new_database_has_an_empty_llm_cache_table(db: Database) -> None:
    frame = db.query("select * from llm_cache")
    assert len(frame) == 0
    assert tuple(frame.columns) == LLM_CACHE_COLUMNS


def test_put_round_trips_every_column(db: Database) -> None:
    assert db.put_llm_cache([row()], replace=False) == 1
    stored = db.query("select * from llm_cache").iloc[0].to_dict()
    expected = row()
    assert stored.pop("created_at").to_pydatetime() == expected.pop("created_at")
    assert stored == expected


def test_put_without_replace_keeps_the_first_writer(db: Database) -> None:
    assert db.put_llm_cache([row(response_json='{"content":"first"}')], replace=False) == 1
    assert db.put_llm_cache([row(response_json='{"content":"second"}')], replace=False) == 0
    assert db.query("select response_json from llm_cache")["response_json"].tolist() == [
        '{"content":"first"}'
    ]


def test_put_with_replace_overwrites(db: Database) -> None:
    db.put_llm_cache([row(response_json='{"content":"first"}')], replace=False)
    assert db.put_llm_cache([row(response_json='{"content":"second"}')], replace=True) == 1
    frame = db.query("select response_json from llm_cache")
    assert frame["response_json"].tolist() == ['{"content":"second"}']


def test_put_empty_and_bad_rows(db: Database) -> None:
    assert db.put_llm_cache([], replace=False) == 0
    with pytest.raises(ValueError, match="missing or unknown"):
        db.put_llm_cache([{"key": "k"}], replace=False)
    with pytest.raises(DataError, match="llm_cache"):
        db.put_llm_cache([row(model=None)], replace=False)


def test_prune_removes_entries_created_before_the_cutoff(db: Database) -> None:
    db.put_llm_cache(
        [
            row("old", created_at=T0 - timedelta(days=40)),
            row("edge", created_at=T0),
            row("new", created_at=T0 + timedelta(days=1)),
        ],
        replace=False,
    )
    assert db.prune_llm_cache(T0) == 1  # strictly older than the cutoff
    assert sorted(db.query("select key from llm_cache")["key"]) == ["edge", "new"]
    assert db.prune_llm_cache(T0 - timedelta(days=100)) == 0


def test_prune_rejects_a_naive_cutoff(db: Database) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        db.prune_llm_cache(datetime(2026, 11, 1))


def test_read_only_database_refuses_writes(lake: Lake) -> None:
    Database(lake).close()
    with Database(lake, read_only=True) as ro:
        with pytest.raises(ValueError, match="read-only"):
            ro.put_llm_cache([row()], replace=False)
        with pytest.raises(ValueError, match="read-only"):
            ro.prune_llm_cache(T0)


# Backlog follow-up F1: compare-and-set and single-key delete.


def test_swap_writes_only_when_the_current_row_matches(db: Database) -> None:
    seen: list[object] = []

    def nothing_stored(current: dict[str, object] | None) -> bool:
        seen.append(current)
        return current is None

    assert db.swap_llm_cache(row(response_json='{"content":"a"}'), matches=nothing_stored) is True
    assert seen == [None]
    assert db.swap_llm_cache(row(response_json='{"content":"b"}'), matches=nothing_stored) is False
    assert seen[1] is not None and seen[1]["response_json"] == '{"content":"a"}'  # type: ignore[index]
    assert db.swap_llm_cache(row(response_json='{"content":"c"}'), matches=lambda c: True) is True
    assert db.query("select response_json from llm_cache")["response_json"].tolist() == [
        '{"content":"c"}'
    ]


def test_swap_failures_store_nothing(db: Database) -> None:
    db.put_llm_cache([row()], replace=False)
    with pytest.raises(ValueError, match="missing or unknown"):
        db.swap_llm_cache({"key": "k1"}, matches=lambda c: True)
    with pytest.raises(DataError, match="llm_cache"):
        db.swap_llm_cache(row(model=None), matches=lambda c: True)

    def boom(current: dict[str, object] | None) -> bool:
        raise RuntimeError("matcher failed")

    with pytest.raises(RuntimeError, match="matcher failed"):
        db.swap_llm_cache(row(response_json="{}"), matches=boom)
    stored = db.query("select model, response_json from llm_cache").iloc[0].to_dict()
    assert stored == {"model": "deepseek-flash", "response_json": '{"content":"hello"}'}


def test_delete_one_key(db: Database) -> None:
    db.put_llm_cache([row("a"), row("b")], replace=False)
    assert db.delete_llm_cache("a") is True
    assert db.delete_llm_cache("a") is False
    assert db.query("select key from llm_cache")["key"].tolist() == ["b"]


def test_read_only_database_refuses_swap_and_delete(lake: Lake) -> None:
    Database(lake).close()
    with Database(lake, read_only=True) as ro:
        with pytest.raises(ValueError, match="read-only"):
            ro.swap_llm_cache(row(), matches=lambda c: True)
        with pytest.raises(ValueError, match="read-only"):
            ro.delete_llm_cache("k1")


def test_a_put_that_fails_midway_stores_nothing(db: Database) -> None:
    import duckdb

    with pytest.raises(duckdb.Error):
        db.put_llm_cache([row("a"), row("b", created_at="not a timestamp")], replace=False)
    assert len(db.query("select * from llm_cache")) == 0
