"""Stage 27 Task 6 (brief task 4): lesson curation — near-duplicates, caps per agent and
quality decay, all point-in-time."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from atlas.agents.memory.embedder import HashEmbedder
from atlas.agents.memory.index import Hit
from atlas.agents.memory.retrieval import AgentMemory, effective_quality
from atlas.agents.memory.store import GLOBAL, StoredLesson
from atlas.core.config import AgentsConfig, AtlasConfig, LessonCurationConfig, MemoryConfig
from atlas.core.pit import AsOf
from atlas.core.types.ids import IdPrefix, new_id

AGENT = "fundamental_analyst"
T0 = datetime(2024, 3, 4, 17, tzinfo=UTC)
TEXT = "earnings beats with raised guidance drift up for weeks"
QUERY = "earnings beats raised guidance drift"


def point(at: datetime) -> AsOf:
    return AsOf(day=at.date(), cutoff=at)


def make(**lessons: object) -> AgentMemory:
    memory_cfg = MemoryConfig(embedder="hash", lessons=LessonCurationConfig.model_validate(lessons))
    return AgentMemory(HashEmbedder(), config=AtlasConfig(agents=AgentsConfig(memory=memory_cfg)))


@pytest.fixture
def memory() -> Iterator[AgentMemory]:
    mem = make()
    yield mem
    mem.close()


def stored(quality: float | None = 0.8, days: float = 0) -> StoredLesson:
    return StoredLesson(
        id=new_id(IdPrefix.LESSON),
        agent=AGENT,
        created_at=T0 + timedelta(days=days),
        text="t",
        quality_score=quality,
    )


# --- decay ----------------------------------------------------------------------------


def test_effective_quality_halves_every_half_life() -> None:
    curation = LessonCurationConfig(half_life_days=180)
    lesson = stored(0.8)
    assert effective_quality(lesson, T0, curation) == pytest.approx(0.8)
    assert effective_quality(lesson, T0 + timedelta(days=180), curation) == pytest.approx(0.4)
    assert effective_quality(lesson, T0 + timedelta(days=360), curation) == pytest.approx(0.2)
    assert effective_quality(lesson, T0 + timedelta(days=90), curation) == pytest.approx(
        0.8 * 0.5**0.5
    )


def test_effective_quality_of_unrated_lessons_and_negative_ages() -> None:
    curation = LessonCurationConfig(default_quality=0.5)
    assert effective_quality(stored(None), T0, curation) == pytest.approx(0.5)
    assert effective_quality(stored(0.8), T0 - timedelta(days=30), curation) == pytest.approx(0.8)


# --- dedupe at write ------------------------------------------------------------------


def test_a_near_duplicate_that_is_not_better_is_not_stored(memory: AgentMemory) -> None:
    first = memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.7)
    again = memory.add_lesson(
        AGENT, TEXT.upper(), created_at=T0 + timedelta(days=1), quality_score=0.6
    )
    assert not again.stored and again.duplicate_of == first.lesson_id
    assert again.lesson_id == first.lesson_id and again.similarity is not None
    assert again.similarity > 0.92
    same = memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.7)
    assert not same.stored  # equal quality does not beat it
    assert memory.store.lesson_ids() == {first.lesson_id}
    assert memory.index.ids("lessons") == {first.lesson_id}


def test_a_better_near_duplicate_supersedes_the_old_lesson(memory: AgentMemory) -> None:
    old = memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.7)
    year = T0 + timedelta(days=365)  # old effective quality 0.7 * 0.5^(365/180) ≈ 0.17
    new = memory.add_lesson(AGENT, TEXT, created_at=year, quality_score=0.6)
    assert new.stored and new.supersedes == old.lesson_id and new.duplicate_of is None
    kept = memory.store.lesson(old.lesson_id)
    assert kept is not None and kept.superseded_by == new.lesson_id and kept.superseded_at == year
    before = memory.recall(AGENT, QUERY, point(year), kinds=["lesson"])
    assert [m.id for m in before] == [old.lesson_id]
    after = memory.recall(AGENT, QUERY, point(year + timedelta(seconds=1)), kinds=["lesson"])
    assert [m.id for m in after] == [new.lesson_id]


def test_unrated_lessons_use_the_default_quality_when_deduped(memory: AgentMemory) -> None:
    memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.4)
    assert memory.add_lesson(AGENT, TEXT, created_at=T0).stored  # default 0.5 beats 0.4


def test_the_dedupe_threshold_is_strict(
    memory: AgentMemory, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = memory.add_lesson(AGENT, "first lesson", created_at=T0, quality_score=0.9)

    def at(similarity: float) -> Any:
        return lambda *a, **k: [Hit(id=old.lesson_id, similarity=similarity)]

    monkeypatch.setattr(memory.index, "query", at(0.92))
    assert memory.add_lesson(AGENT, "second lesson", created_at=T0, quality_score=0.1).stored
    monkeypatch.setattr(memory.index, "query", at(0.9201))
    assert not memory.add_lesson(AGENT, "third lesson", created_at=T0, quality_score=0.1).stored


def test_the_threshold_comes_from_the_config() -> None:
    loose = make(dedupe_cosine=0.3)
    try:
        loose.add_lesson(AGENT, "earnings beats drift up", created_at=T0, quality_score=0.9)
        result = loose.add_lesson(AGENT, "earnings beats fade", created_at=T0, quality_score=0.1)
        assert not result.stored
    finally:
        loose.close()


def test_only_the_same_scope_and_earlier_active_lessons_are_compared(memory: AgentMemory) -> None:
    mine = memory.add_lesson(AGENT, TEXT, created_at=T0 + timedelta(days=10), quality_score=0.9)
    shared = memory.add_lesson(GLOBAL, TEXT, created_at=T0 + timedelta(days=10), quality_score=0.1)
    assert shared.stored  # another scope
    earlier = memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.1)
    assert earlier.stored and earlier.supersedes is None  # `mine` was created after it
    assert mine.stored


def test_superseded_lessons_are_not_compared_again(memory: AgentMemory) -> None:
    old = memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.2)
    new = memory.add_lesson(AGENT, TEXT, created_at=T0 + timedelta(days=1), quality_score=0.9)
    third = memory.add_lesson(AGENT, TEXT, created_at=T0 + timedelta(days=2), quality_score=0.5)
    assert new.supersedes == old.lesson_id
    assert not third.stored and third.duplicate_of == new.lesson_id


# --- cap per agent, min quality, recall-time dedupe -----------------------------------


def test_each_scope_keeps_only_its_best_lessons() -> None:
    memory = make(max_per_agent=2)
    try:
        texts = ["earnings beat drift one", "earnings beat drift two", "earnings beat drift six"]
        ids = [
            memory.add_lesson(AGENT, t, created_at=T0, quality_score=q).lesson_id
            for t, q in zip(texts, (0.9, 0.5, 0.7), strict=True)
        ]
        shared = memory.add_lesson(
            GLOBAL, "earnings beat drift global", created_at=T0, quality_score=0.1
        ).lesson_id
        got = memory.recall(
            AGENT, "earnings beat drift", point(T0 + timedelta(days=1)), k=10, kinds=["lesson"]
        )
        assert {m.id for m in got} == {ids[0], ids[2], shared}
        visible = memory.visible_lessons(AGENT, point(T0 + timedelta(days=1)))
        assert set(visible) == {ids[0], ids[2], shared}
    finally:
        memory.close()


def test_the_cap_is_point_in_time() -> None:
    memory = make(max_per_agent=1)
    try:
        old = memory.add_lesson(
            AGENT, "earnings beat drift old", created_at=T0, quality_score=0.9
        ).lesson_id
        young = memory.add_lesson(
            AGENT,
            "earnings beat drift young",
            created_at=T0 + timedelta(days=200),
            quality_score=0.6,
        ).lesson_id
        query = "earnings beat drift"
        early = memory.recall(AGENT, query, point(T0 + timedelta(days=100)), kinds=["lesson"])
        assert [m.id for m in early] == [old]  # `young` does not exist yet
        late = memory.recall(AGENT, query, point(T0 + timedelta(days=201)), kinds=["lesson"])
        assert [m.id for m in late] == [young]  # old decayed to 0.9 * 0.5^(201/180) ≈ 0.41
        assert late[0].quality == pytest.approx(0.6 * 0.5 ** (1 / 180), rel=1e-6)
    finally:
        memory.close()


def test_decayed_lessons_below_min_quality_are_not_recalled() -> None:
    memory = make(min_quality=0.1, half_life_days=100)
    try:
        lesson = memory.add_lesson(AGENT, "earnings beat drift", created_at=T0, quality_score=0.5)
        fresh = memory.recall(AGENT, "earnings beat drift", point(T0 + timedelta(days=200)))
        assert [m.id for m in fresh] == [lesson.lesson_id]  # 0.125
        stale = memory.recall(AGENT, "earnings beat drift", point(T0 + timedelta(days=300)))
        assert stale == []  # 0.0625 < 0.1
    finally:
        memory.close()


def test_an_agent_lesson_and_a_global_duplicate_are_shown_once(memory: AgentMemory) -> None:
    memory.add_lesson(AGENT, TEXT, created_at=T0, quality_score=0.8)
    shared = memory.add_lesson(GLOBAL, TEXT, created_at=T0 + timedelta(hours=1), quality_score=0.8)
    memory.add_lesson(AGENT, "rates rise slowly", created_at=T0, quality_score=0.8)
    got = memory.recall(AGENT, QUERY, point(T0 + timedelta(days=1)), k=5, kinds=["lesson"])
    assert [m.id for m in got if m.text == TEXT] == [shared.lesson_id]  # the newer of a tie
