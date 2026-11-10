"""The native `llm_usage` table in `atlas.duckdb` (Stage 24): one row per billed LLM response."""

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from atlas.core.errors import DataError
from atlas.core.storage import Database, Lake
from atlas.core.storage.db import LLM_USAGE_COLUMNS


@pytest.fixture
def db(lake: Lake) -> Iterator[Database]:
    database = Database(lake)
    yield database
    database.close()


def row(call_id: str = "01CALL", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "call_id": call_id,
        "ts": datetime(2026, 11, 9, 2, 30, tzinfo=UTC),
        "day": date(2026, 11, 9),
        "run_id": "01RUN",
        "agent": "analyst",
        "step": "draft",
        "tier": "deep",
        "provider": "openai_compatible",
        "model": "deepseek-v4-pro",
        "input_tokens": 1000,
        "cached_input_tokens": 4000,
        "cache_write_tokens": 0,
        "output_tokens": 500,
        "reasoning_tokens": 100,
        "cost_usd": 0.003476,
        "latency_ms": 812.5,
        "cache_hit": False,
        "attempt": 1,
        "status": "ok",
    }
    values.update(overrides)
    return values


def test_columns_are_the_documented_ones() -> None:
    assert tuple(row()) == LLM_USAGE_COLUMNS


def test_new_database_has_an_empty_llm_usage_table(db: Database) -> None:
    frame = db.query("select * from llm_usage")
    assert len(frame) == 0
    assert tuple(frame.columns) == LLM_USAGE_COLUMNS


def test_insert_round_trips_every_column(db: Database) -> None:
    assert db.insert_llm_usage([row()]) == 1
    stored = db.query("select * from llm_usage").iloc[0].to_dict()
    expected = row()
    assert stored.pop("ts").to_pydatetime() == expected.pop("ts")
    assert stored == expected


def test_nullable_labels(db: Database) -> None:
    db.insert_llm_usage([row(run_id=None, agent=None, step=None)])
    frame = db.query("select run_id, agent, step from llm_usage where run_id is null")
    assert len(frame) == 1
    assert frame.iloc[0].isna().all()


def test_several_rows_and_sums(db: Database) -> None:
    rows = [
        row("a", cost_usd=1.0),
        row("b", cost_usd=2.0),
        row("c", cost_usd=4.0, run_id="01OTHER"),
    ]
    assert db.insert_llm_usage(rows) == 3
    total = db.query("select sum(cost_usd) as usd from llm_usage where run_id = ?", ["01RUN"])
    assert total["usd"].tolist() == [3.0]
    assert db.insert_llm_usage([]) == 0


def test_duplicate_call_id_is_refused_and_nothing_of_the_batch_is_kept(db: Database) -> None:
    db.insert_llm_usage([row("a")])
    with pytest.raises(DataError, match="llm_usage"):
        db.insert_llm_usage([row("b"), row("a")])
    assert db.query("select call_id from llm_usage")["call_id"].tolist() == ["a"]


@pytest.mark.parametrize(
    "bad",
    [
        {"tier": None},
        {"cost_usd": None},
        {"status": None},
    ],
)
def test_required_columns_are_not_null(db: Database, bad: dict[str, object]) -> None:
    with pytest.raises(DataError, match="llm_usage"):
        db.insert_llm_usage([row(**bad)])
    assert len(db.query("select * from llm_usage")) == 0


def test_missing_or_unknown_column_is_refused(db: Database) -> None:
    incomplete = row()
    del incomplete["model"]
    with pytest.raises(ValueError, match="model"):
        db.insert_llm_usage([incomplete])
    with pytest.raises(ValueError, match="surprise"):
        db.insert_llm_usage([row(surprise=1)])


def test_read_only_database_refuses_inserts(lake: Lake) -> None:
    Database(lake).close()
    with Database(lake, read_only=True) as reader:
        with pytest.raises(ValueError, match="read-only"):
            reader.insert_llm_usage([row()])
        assert len(reader.query("select * from llm_usage")) == 0


def test_rows_survive_reopening(lake: Lake) -> None:
    with Database(lake) as first:
        first.insert_llm_usage([row("a"), row("b")])
    with Database(lake) as second:
        assert second.query("select count(*) as n from llm_usage")["n"].tolist() == [2]


def test_query_still_cannot_write_llm_usage(db: Database) -> None:
    with pytest.raises(ValueError, match="SELECT"):
        db.query("delete from llm_usage")
