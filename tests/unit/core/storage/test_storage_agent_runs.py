"""The native `agent_runs` table in `atlas.duckdb` (Stage 26): one row per agent run."""

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from atlas.core.errors import DataError
from atlas.core.storage import Database, Lake
from atlas.core.storage.db import AGENT_RUNS_COLUMNS


@pytest.fixture
def db(lake: Lake) -> Iterator[Database]:
    database = Database(lake)
    yield database
    database.close()


def row(agent_run_id: str = "01AGENTRUN", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "agent_run_id": agent_run_id,
        "run_id": "01RUN",
        "agent": "echo_analyst",
        "tier": "quick",
        "as_of": date(2024, 3, 1),
        "cutoff": datetime(2024, 3, 1, 13, tzinfo=UTC),
        "started_at": datetime(2026, 11, 14, 12, tzinfo=UTC),
        "finished_at": datetime(2026, 11, 14, 12, 0, 3, tzinfo=UTC),
        "status": "ok",
        "prompt_name": "echo_analyst.j2",
        "prompt_version": 1,
        "prompt_digest": "d" * 64,
        "versions": "echo_analyst.j2@v1|news@v1",
        "inputs_digest": "i" * 64,
        "tool_calls": "[]",
        "evidence_ids": '["news:1"]',
        "cited_ids": '["news:1"]',
        "unknown_ids": "[]",
        "uncited_claims": "[]",
        "output_json": '{"x":1}',
        "usage": '{"input_tokens":10}',
        "cost_usd": 0.001,
        "llm_calls": 1,
        "cache_hits": 0,
        "error": None,
    }
    values.update(overrides)
    return values


def test_columns_are_the_documented_ones() -> None:
    assert tuple(row()) == AGENT_RUNS_COLUMNS


def test_new_database_has_an_empty_agent_runs_table(db: Database) -> None:
    frame = db.query("select * from agent_runs")
    assert len(frame) == 0 and tuple(frame.columns) == AGENT_RUNS_COLUMNS


def test_insert_round_trips_every_column(db: Database) -> None:
    assert db.insert_agent_runs([row()]) == 1
    stored = db.query("select * from agent_runs").iloc[0].to_dict()
    expected = row()
    for column in ("cutoff", "started_at", "finished_at"):
        assert stored.pop(column).to_pydatetime() == expected.pop(column)
    assert pd.isna(stored.pop("error")) and expected.pop("error") is None
    assert stored == expected
    assert db.insert_agent_runs([]) == 0


def test_duplicate_id_is_refused_and_nothing_of_the_batch_is_kept(db: Database) -> None:
    db.insert_agent_runs([row("a")])
    with pytest.raises(DataError, match="agent_runs"):
        db.insert_agent_runs([row("b"), row("a")])
    assert db.query("select agent_run_id from agent_runs")["agent_run_id"].tolist() == ["a"]


@pytest.mark.parametrize("column", ["agent", "status", "as_of", "inputs_digest", "cost_usd"])
def test_required_columns_are_not_null(db: Database, column: str) -> None:
    with pytest.raises(DataError, match="agent_runs"):
        db.insert_agent_runs([row(**{column: None})])
    assert len(db.query("select * from agent_runs")) == 0


def test_nullable_columns(db: Database) -> None:
    db.insert_agent_runs([row(run_id=None, output_json=None, error="boom", status="failed")])
    stored = db.query("select run_id, output_json, error from agent_runs").iloc[0]
    assert pd.isna(stored["run_id"]) and pd.isna(stored["output_json"])
    assert stored["error"] == "boom"


def test_missing_or_unknown_column_is_refused(db: Database) -> None:
    incomplete = row()
    del incomplete["agent"]
    with pytest.raises(ValueError, match="agent"):
        db.insert_agent_runs([incomplete])
    with pytest.raises(ValueError, match="surprise"):
        db.insert_agent_runs([row(surprise=1)])


def test_read_only_database_refuses_inserts(lake: Lake) -> None:
    Database(lake).close()
    with Database(lake, read_only=True) as reader, pytest.raises(ValueError, match="read-only"):
        reader.insert_agent_runs([row()])


def test_rows_survive_reopening(lake: Lake) -> None:
    with Database(lake) as first:
        first.insert_agent_runs([row("a"), row("b")])
    with Database(lake) as second:
        assert second.query("select count(*) as n from agent_runs")["n"].tolist() == [2]
