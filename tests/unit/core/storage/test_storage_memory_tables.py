"""The native `episodes` and `lessons` tables in `atlas.duckdb` (Stage 27): agent memory."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from atlas.core.errors import DataError
from atlas.core.storage import Database, Lake
from atlas.core.storage.db import EPISODES_COLUMNS, LESSONS_COLUMNS

MADE = datetime(2024, 3, 1, 13, tzinfo=UTC)
LATER = datetime(2024, 4, 1, 13, tzinfo=UTC)


@pytest.fixture
def db(lake: Lake) -> Iterator[Database]:
    database = Database(lake)
    yield database
    database.close()


def episode(id_: str = "epi_1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": id_,
        "agent": "fundamental_analyst",
        "symbol": "AAA",
        "created_at": MADE,
        "situation_text": "AAA beat earnings",
        "decision_json": '{"action":"buy"}',
        "outcome_json": None,
        "settled_at": None,
        "run_id": "01RUN",
    }
    values.update(overrides)
    return values


def lesson(id_: str = "les_1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": id_,
        "agent": "global",
        "created_at": MADE,
        "text": "Earnings beats fade within a week",
        "tags": '["earnings"]',
        "source_episode_ids": '["epi_1"]',
        "quality_score": 0.7,
        "superseded_by": None,
        "superseded_at": None,
    }
    values.update(overrides)
    return values


def test_columns_are_the_documented_ones() -> None:
    assert tuple(episode()) == EPISODES_COLUMNS
    assert tuple(lesson()) == LESSONS_COLUMNS


def test_new_database_has_empty_memory_tables(db: Database) -> None:
    for table, columns in (("episodes", EPISODES_COLUMNS), ("lessons", LESSONS_COLUMNS)):
        frame = db.query(f"select * from {table}")
        assert len(frame) == 0 and tuple(frame.columns) == columns


def test_insert_episodes_round_trip(db: Database) -> None:
    assert db.insert_episodes([episode()]) == 1
    assert db.insert_episodes([]) == 0
    stored = db.query("select * from episodes").iloc[0].to_dict()
    assert stored["id"] == "epi_1" and stored["created_at"] == MADE
    assert stored["decision_json"] == '{"action":"buy"}'


def test_duplicate_episode_id_stores_nothing(db: Database) -> None:
    db.insert_episodes([episode("epi_a")])
    with pytest.raises(DataError, match="episodes"):
        db.insert_episodes([episode("epi_b"), episode("epi_a")])
    assert db.query("select id from episodes")["id"].tolist() == ["epi_a"]


def test_episode_rows_need_exactly_the_columns(db: Database) -> None:
    bad = episode()
    del bad["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        db.insert_episodes([bad])


def test_settle_episode_once(db: Database) -> None:
    db.insert_episodes([episode()])
    db.settle_episode("epi_1", '{"return":0.05}', LATER)
    row = db.query("select outcome_json, settled_at from episodes").iloc[0]
    assert row["outcome_json"] == '{"return":0.05}' and row["settled_at"] == LATER
    with pytest.raises(DataError, match="already settled"):
        db.settle_episode("epi_1", '{"return":0.06}', LATER)
    with pytest.raises(DataError, match="no episode"):
        db.settle_episode("epi_missing", "{}", LATER)


def test_insert_lesson_and_supersede_in_one_step(db: Database) -> None:
    db.insert_lesson(lesson("les_old"))
    db.insert_lesson(lesson("les_new", created_at=LATER), supersedes="les_old")
    frame = db.query("select id, superseded_by, superseded_at from lessons order by id")
    rows = frame.astype(object).where(frame.notna(), None).to_dict(orient="records")
    assert rows == [
        {"id": "les_new", "superseded_by": None, "superseded_at": None},
        {"id": "les_old", "superseded_by": "les_new", "superseded_at": LATER},
    ]


def test_supersede_is_atomic_and_once_only(db: Database) -> None:
    db.insert_lesson(lesson("les_old"))
    db.insert_lesson(lesson("les_new", created_at=LATER), supersedes="les_old")
    with pytest.raises(DataError, match="already superseded"):
        db.insert_lesson(lesson("les_third", created_at=LATER), supersedes="les_old")
    with pytest.raises(DataError, match="no lesson"):
        db.insert_lesson(lesson("les_fourth", created_at=LATER), supersedes="les_missing")
    with pytest.raises(DataError, match="lessons"):
        db.insert_lesson(lesson("les_new"))
    assert sorted(db.query("select id from lessons")["id"]) == ["les_new", "les_old"]


def test_memory_writes_refused_on_a_read_only_database(lake: Lake) -> None:
    Database(lake).close()
    with Database(lake, read_only=True) as database:
        with pytest.raises(ValueError, match="read-only"):
            database.insert_episodes([episode()])
        with pytest.raises(ValueError, match="read-only"):
            database.insert_lesson(lesson())
        with pytest.raises(ValueError, match="read-only"):
            database.settle_episode("epi_1", "{}", LATER)
