"""Stage 27 Task 2 (brief task 1): store CRUD for episodes and lessons, in DuckDB and memory."""

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.agents.memory.store import GLOBAL, Episode, MemoryStore, StoredLesson
from atlas.core.errors import DataError
from atlas.core.storage import Database, Lake
from atlas.core.types.ids import IdPrefix, new_id

MADE = datetime(2024, 3, 1, 13, tzinfo=UTC)
LATER = MADE + timedelta(days=30)


def episode(**overrides: object) -> Episode:
    values: dict[str, object] = {
        "id": new_id(IdPrefix.EPISODE),
        "agent": "fundamental_analyst",
        "symbol": "AAA",
        "created_at": MADE,
        "situation_text": "AAA beat earnings and raised guidance",
        "decision": {"action": "buy", "weight": 0.02},
        "run_id": "01RUN",
    }
    values.update(overrides)
    return Episode.model_validate(values)


def lesson(**overrides: object) -> StoredLesson:
    values: dict[str, object] = {
        "id": new_id(IdPrefix.LESSON),
        "agent": GLOBAL,
        "created_at": MADE,
        "text": "Guidance raises matter more than the beat itself",
        "tags": ("earnings",),
        "source_episode_ids": (),
        "quality_score": 0.7,
    }
    values.update(overrides)
    return StoredLesson.model_validate(values)


@pytest.fixture(params=["duckdb", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MemoryStore]:
    if request.param == "memory":
        yield MemoryStore()
        return
    database = Database(Lake(tmp_path / "lake"))
    yield MemoryStore(database)
    database.close()


# --- models ---------------------------------------------------------------------------


def test_episode_validation() -> None:
    assert episode().settled_at is None and episode().outcome is None
    assert episode(symbol=None).symbol is None
    with pytest.raises(ValidationError, match="global"):
        episode(agent=GLOBAL)
    with pytest.raises(ValidationError):
        episode(created_at=datetime(2024, 3, 1, 13))  # naive
    with pytest.raises(ValidationError):
        episode(situation_text="  ")
    with pytest.raises(ValidationError):
        episode(id=new_id(IdPrefix.LESSON))
    with pytest.raises(ValidationError, match="both"):
        episode(outcome={"return": 0.1})
    with pytest.raises(ValidationError, match="after"):
        episode(outcome={"return": 0.1}, settled_at=MADE)
    with pytest.raises(ValidationError, match="JSON"):
        episode(decision={"x": float("nan")})
    with pytest.raises(ValidationError, match="JSON"):
        episode(decision={"x": {1, 2}})


def test_episode_decision_is_its_json_round_trip() -> None:
    ep = episode(decision={"weight": Decimal("0.020"), "when": MADE, "notes": ["a", 1]})
    assert ep.decision == {
        "weight": "0.020",
        "when": "2024-03-01T13:00:00+00:00",
        "notes": ["a", 1],
    }


def test_stored_lesson_validation() -> None:
    assert lesson().superseded_by is None
    assert lesson(quality_score=None).quality_score is None
    with pytest.raises(ValidationError, match="both"):
        lesson(superseded_by=new_id(IdPrefix.LESSON))
    with pytest.raises(ValidationError, match="before"):
        lesson(superseded_by=new_id(IdPrefix.LESSON), superseded_at=MADE - timedelta(1))
    with pytest.raises(ValidationError):
        lesson(quality_score=1.5)
    with pytest.raises(ValidationError):
        lesson(id=new_id(IdPrefix.EPISODE))


# --- episodes -------------------------------------------------------------------------


def test_add_and_get_episodes(store: MemoryStore) -> None:
    first, second = episode(), episode(symbol=None, agent="news_analyst")
    store.add_episode(first)
    store.add_episode(second)
    assert store.episode(first.id) == first
    assert store.episode(new_id(IdPrefix.EPISODE)) is None
    assert store.episodes([second.id, first.id, "epi_missing"]) == {
        first.id: first,
        second.id: second,
    }
    assert store.episodes([]) == {}
    assert store.episode_ids() == {first.id, second.id}
    assert sorted(e.id for e in store.all_episodes()) == sorted([first.id, second.id])


def test_duplicate_episode_raises(store: MemoryStore) -> None:
    ep = episode()
    store.add_episode(ep)
    with pytest.raises(DataError, match=ep.id):
        store.add_episode(ep)


def test_add_settled_episode_is_refused(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="settle"):
        store.add_episode(episode(outcome={"r": 1}, settled_at=LATER))


def test_settle_episode(store: MemoryStore) -> None:
    ep = episode()
    store.add_episode(ep)
    settled = store.settle_episode(ep.id, {"return": 0.05, "hit": True}, settled_at=LATER)
    assert settled.outcome == {"return": 0.05, "hit": True} and settled.settled_at == LATER
    assert store.episode(ep.id) == settled
    with pytest.raises(DataError, match="already settled"):
        store.settle_episode(ep.id, {"return": 0.06}, settled_at=LATER)
    assert store.episode(ep.id) == settled


def test_settle_needs_a_known_episode_and_a_later_aware_time(store: MemoryStore) -> None:
    ep = episode()
    store.add_episode(ep)
    with pytest.raises(DataError, match="no episode"):
        store.settle_episode(new_id(IdPrefix.EPISODE), {}, settled_at=LATER)
    with pytest.raises(ValueError, match="after"):
        store.settle_episode(ep.id, {"r": 0}, settled_at=MADE)
    with pytest.raises(ValueError, match="naive"):
        store.settle_episode(ep.id, {"r": 0}, settled_at=datetime(2024, 4, 1))
    with pytest.raises(ValueError, match="JSON"):
        store.settle_episode(ep.id, {"r": float("inf")}, settled_at=LATER)
    assert store.episode(ep.id) == ep  # nothing changed


# --- lessons --------------------------------------------------------------------------


def test_add_and_list_lessons(store: MemoryStore) -> None:
    mine = lesson(agent="news_analyst", tags=("news", "fade"), source_episode_ids=("epi_x",))
    shared, other = lesson(), lesson(agent="macro_economist", quality_score=None)
    for item in (mine, shared, other):
        store.add_lesson(item)
    assert store.lesson(mine.id) == mine
    assert store.lesson(new_id(IdPrefix.LESSON)) is None
    assert {x.id for x in store.lessons(["news_analyst", GLOBAL])} == {mine.id, shared.id}
    assert store.lessons([]) == []
    assert store.lesson_ids() == {mine.id, shared.id, other.id}
    assert {x.id for x in store.all_lessons()} == {mine.id, shared.id, other.id}


def test_add_lesson_superseding_an_older_one(store: MemoryStore) -> None:
    old = lesson()
    new = lesson(created_at=LATER, quality_score=0.9)
    store.add_lesson(old)
    store.add_lesson(new, supersedes=old.id)
    assert store.lesson(old.id) == old.model_copy(
        update={"superseded_by": new.id, "superseded_at": LATER}
    )
    assert store.lesson(new.id) == new
    with pytest.raises(DataError, match="already superseded"):
        store.add_lesson(lesson(created_at=LATER), supersedes=old.id)
    with pytest.raises(DataError, match="no lesson"):
        store.add_lesson(lesson(created_at=LATER), supersedes=new_id(IdPrefix.LESSON))
    with pytest.raises(DataError, match=new.id):
        store.add_lesson(new)
    assert store.lesson_ids() == {old.id, new.id}


def test_superseding_needs_the_same_scope_and_an_older_lesson(store: MemoryStore) -> None:
    old = lesson(created_at=LATER)
    store.add_lesson(old)
    with pytest.raises(ValueError, match="before"):
        store.add_lesson(lesson(created_at=MADE), supersedes=old.id)
    with pytest.raises(ValueError, match="scope"):
        store.add_lesson(lesson(agent="news_analyst", created_at=LATER), supersedes=old.id)
    with pytest.raises(ValueError, match="superseded"):
        store.add_lesson(lesson(superseded_by=old.id, superseded_at=LATER, created_at=LATER))


def test_records_survive_reopening(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake")
    ep, les = episode(), lesson(tags=("a", "b"), source_episode_ids=("epi_1",))
    with Database(lake) as database:
        store = MemoryStore(database)
        store.add_episode(ep)
        store.settle_episode(ep.id, {"return": -0.02}, settled_at=LATER)
        store.add_lesson(les)
    with Database(lake) as database:
        store = MemoryStore(database)
        assert store.episode(ep.id) == ep.model_copy(
            update={"outcome": {"return": -0.02}, "settled_at": LATER}
        )
        assert store.lesson(les.id) == les


def test_concurrent_adds_all_land(store: MemoryStore) -> None:
    items = [episode() for _ in range(40)]
    threads = [threading.Thread(target=store.add_episode, args=(item,)) for item in items]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert store.episode_ids() == {item.id for item in items}
