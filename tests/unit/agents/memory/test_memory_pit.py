"""Stage 27 Task 5 (brief task 3): point-in-time recall. Memories created at or after the
decision cutoff are invisible; outcomes are invisible until settled before it."""

import socket
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from memory_world import AGENT, OTHER, as_of, at, build_memory

from atlas.agents.memory.embedder import HashEmbedder
from atlas.agents.memory.index import Hit
from atlas.agents.memory.retrieval import AgentMemory, Memory
from atlas.agents.memory.store import GLOBAL
from atlas.core.calendar import MARKET_TZ
from atlas.core.config import AtlasConfig, CalendarConfig
from atlas.core.errors import DataError, LookAheadError
from atlas.core.pit import AsOf

QUERY = "earnings beat raised guidance"


def ids(memories: list[Memory]) -> set[str]:
    return {m.id for m in memories}


@pytest.fixture
def memory() -> AgentMemory:
    mem = AgentMemory(HashEmbedder(), config=AtlasConfig())
    yield mem
    mem.close()


def test_future_episodes_are_invisible() -> None:
    memory, seeds = build_memory()
    try:
        for day in (date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 12), date(2024, 3, 29)):
            got = memory.recall(AGENT, QUERY, as_of(day), k=50)
            assert got, day
            for m in got:
                assert m.created_at < as_of(day).cutoff
            expected = {i for i, s in seeds.items() if s.agent == AGENT and s.day < day}
            relevant = {m.id for m in got}
            assert relevant <= expected
        for day in (date(2024, 3, 1), date(2024, 3, 4)):  # first episode: 3/4 at noon
            assert memory.recall(AGENT, QUERY, as_of(day), k=50) == []
    finally:
        memory.close()


def test_a_memory_created_exactly_at_the_cutoff_is_invisible(memory: AgentMemory) -> None:
    point = as_of(date(2024, 3, 4))
    exact = memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=point.cutoff)
    just_before = memory.record_episode(
        AGENT, "earnings beat again", {"a": 2}, created_at=point.cutoff - timedelta(microseconds=1)
    )
    got = memory.recall(AGENT, "earnings beat", point, k=10)
    assert ids(got) == {just_before.id}
    later = AsOf(day=point.day, cutoff=point.cutoff + timedelta(microseconds=1))
    assert ids(memory.recall(AGENT, "earnings beat", later, k=10)) == {exact.id, just_before.id}


def test_outcomes_are_hidden_until_settled_before_the_cutoff(memory: AgentMemory) -> None:
    ep = memory.record_episode(
        AGENT, "earnings beat", {"action": "buy"}, created_at=at(date(2024, 3, 4))
    )
    settled_at = at(date(2024, 3, 11), 17)
    memory.settle_episode(ep.id, {"return_5d": 0.04}, settled_at=settled_at)

    (before,) = memory.recall(AGENT, "earnings beat", as_of(date(2024, 3, 11)))
    assert before.outcome is None and before.settled_at is None
    assert before.decision == {"action": "buy"}

    exactly = AsOf(day=date(2024, 3, 11), cutoff=settled_at)
    (at_cutoff,) = memory.recall(AGENT, "earnings beat", exactly)
    assert at_cutoff.outcome is None and at_cutoff.settled_at is None

    (after,) = memory.recall(AGENT, "earnings beat", as_of(date(2024, 3, 12)))
    assert after.outcome == {"return_5d": 0.04} and after.settled_at == settled_at


def test_unsettled_episodes_never_show_an_outcome(memory: AgentMemory) -> None:
    memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 4)))
    (m,) = memory.recall(AGENT, "earnings beat", as_of(date(2030, 1, 2)))
    assert m.outcome is None and m.settled_at is None


def test_a_date_uses_the_configured_decision_time() -> None:
    late = AtlasConfig(calendar=CalendarConfig(decision_time=time(15, 30)))
    memory = AgentMemory(HashEmbedder(), config=late)
    try:
        noon = datetime.combine(date(2024, 3, 4), time(12), tzinfo=MARKET_TZ)
        memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=noon)
        assert len(memory.recall(AGENT, "earnings beat", date(2024, 3, 4))) == 1  # 15:30 cutoff
    finally:
        memory.close()
    default = AgentMemory(HashEmbedder(), config=AtlasConfig())  # 08:00 cutoff
    try:
        default.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=noon)
        assert default.recall(AGENT, "earnings beat", date(2024, 3, 4)) == []
    finally:
        default.close()


def test_future_lessons_are_invisible(memory: AgentMemory) -> None:
    early = memory.add_lesson(
        GLOBAL,
        "earnings beats with raised guidance drift up",
        created_at=at(date(2024, 3, 4)),
        quality_score=0.8,
    )
    late = memory.add_lesson(
        AGENT,
        "earnings beat guidance raised but priced in",
        created_at=at(date(2024, 3, 20)),
        quality_score=0.8,
    )
    got = memory.recall(AGENT, QUERY, as_of(date(2024, 3, 10)), kinds=["lesson"])
    assert ids(got) == {early.lesson_id}
    got = memory.recall(AGENT, QUERY, as_of(date(2024, 3, 21)), kinds=["lesson"])
    assert ids(got) == {early.lesson_id, late.lesson_id}


def test_a_superseded_lesson_is_visible_only_before_its_supersession(memory: AgentMemory) -> None:
    old = memory.add_lesson(
        AGENT,
        "earnings beats with raised guidance drift up for weeks",
        created_at=at(date(2024, 3, 4)),
        quality_score=0.4,
    )
    new = memory.add_lesson(
        AGENT,
        "earnings beats with raised guidance drift up for weeks",
        created_at=at(date(2024, 3, 20)),
        quality_score=0.9,
    )
    assert new.stored and new.supersedes == old.lesson_id
    assert ids(memory.recall(AGENT, QUERY, as_of(date(2024, 3, 10)), kinds=["lesson"])) == {
        old.lesson_id
    }
    assert ids(memory.recall(AGENT, QUERY, as_of(date(2024, 3, 21)), kinds=["lesson"])) == {
        new.lesson_id
    }


def test_another_agents_memories_are_invisible_but_global_lessons_are_shared() -> None:
    memory, seeds = build_memory()
    try:
        mine = memory.add_lesson(
            AGENT, "earnings beat guidance lesson for me", created_at=at(date(2024, 3, 4))
        )
        theirs = memory.add_lesson(
            OTHER, "earnings beat guidance lesson for them", created_at=at(date(2024, 3, 4))
        )
        shared = memory.add_lesson(
            GLOBAL, "earnings beat guidance lesson for all", created_at=at(date(2024, 3, 4))
        )
        got = memory.recall(AGENT, QUERY, as_of(date(2024, 3, 29)), k=50)
        assert all(m.agent in {AGENT, GLOBAL} for m in got)
        assert {mine.lesson_id, shared.lesson_id} <= ids(got)
        assert theirs.lesson_id not in ids(got)
        other_eps = {i for i, s in seeds.items() if s.agent == OTHER}
        assert not other_eps & ids(got)
        assert other_eps <= ids(memory.recall(OTHER, QUERY, as_of(date(2024, 3, 29)), k=50))
    finally:
        memory.close()


def test_an_index_returning_a_future_memory_raises(memory: AgentMemory) -> None:
    point = as_of(date(2024, 3, 10))
    future = memory.record_episode(
        AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 20))
    )

    def corrupt(*args: Any, **kwargs: Any) -> list[Hit]:
        return [Hit(id=future.id, similarity=0.9)]

    memory.index.query = corrupt  # type: ignore[method-assign]
    with pytest.raises(LookAheadError, match=future.id):
        memory.recall(AGENT, "earnings beat", point, kinds=["episode"])


def test_an_index_returning_another_agents_episode_raises(memory: AgentMemory) -> None:
    theirs = memory.record_episode(
        OTHER, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 4))
    )
    memory.index.query = lambda *a, **k: [Hit(id=theirs.id, similarity=0.9)]  # type: ignore[method-assign]
    with pytest.raises(DataError, match=theirs.id):
        memory.recall(AGENT, "earnings beat", as_of(date(2024, 3, 10)), kinds=["episode"])


def test_an_index_returning_a_hidden_lesson_raises(memory: AgentMemory) -> None:
    memory.add_lesson(AGENT, "earnings lesson", created_at=at(date(2024, 3, 4)))
    late = memory.add_lesson(AGENT, "rates lesson", created_at=at(date(2024, 3, 20)))
    memory.index.query = lambda *a, **k: [Hit(id=late.lesson_id, similarity=0.9, vector=None)]  # type: ignore[method-assign]
    with pytest.raises(LookAheadError, match=late.lesson_id):
        memory.recall(AGENT, "earnings lesson", as_of(date(2024, 3, 10)), kinds=["lesson"])


def test_a_lesson_citing_a_later_episode_raises(memory: AgentMemory) -> None:
    ep = memory.record_episode(AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 20)))
    with pytest.raises(LookAheadError, match=ep.id):
        memory.add_lesson(
            AGENT, "lesson", created_at=at(date(2024, 3, 10)), source_episode_ids=[ep.id]
        )
    with pytest.raises(DataError, match="epi_"):
        memory.add_lesson(
            AGENT,
            "lesson",
            created_at=at(date(2024, 3, 10)),
            source_episode_ids=["epi_01J0000000000000000000000X"],
        )
    ok = memory.add_lesson(
        AGENT, "lesson", created_at=at(date(2024, 3, 20)), source_episode_ids=[ep.id]
    )
    assert ok.stored and memory.store.lesson(ok.lesson_id).source_episode_ids == (ep.id,)  # type: ignore[union-attr]


def test_no_socket_is_opened_in_a_full_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("memory opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    memory, _ = build_memory()
    try:
        memory.add_lesson(GLOBAL, "earnings lesson", created_at=at(date(2024, 3, 4)))
        assert memory.recall(AGENT, QUERY, as_of(date(2024, 3, 29)))
    finally:
        memory.close()


# --- property: recall equals a brute-force point-in-time reference -----------------------

WORDS = [
    "earnings",
    "beat",
    "miss",
    "guidance",
    "rates",
    "hike",
    "supply",
    "chain",
    "lawsuit",
    "merger",
    "margin",
    "demand",
    "inflation",
    "chips",
]
START = datetime(2024, 3, 1, 14, tzinfo=UTC)

_episode = st.tuples(
    st.sampled_from([AGENT, OTHER]),
    st.lists(st.sampled_from(WORDS), min_size=1, max_size=5),
    st.integers(min_value=0, max_value=40 * 24),  # creation, in hours after START
    st.none() | st.integers(min_value=1, max_value=20 * 24),  # settles this many hours later
)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    corpus=st.lists(_episode, min_size=1, max_size=25),
    query=st.lists(st.sampled_from(WORDS), min_size=1, max_size=4),
    cutoff_hours=st.integers(min_value=0, max_value=50 * 24),
    k=st.integers(min_value=1, max_value=8),
)
def test_recall_matches_a_brute_force_point_in_time_reference(
    corpus: list[tuple[str, list[str], int, int | None]],
    query: list[str],
    cutoff_hours: int,
    k: int,
) -> None:
    embedder = HashEmbedder()
    memory = AgentMemory(embedder, config=AtlasConfig())
    try:
        stored = []
        for agent, words, made, settle in corpus:
            created = START + timedelta(hours=made)
            ep = memory.record_episode(agent, " ".join(words), {"w": words}, created_at=created)
            settled_at = None
            if settle is not None:
                settled_at = created + timedelta(hours=settle)
                memory.settle_episode(ep.id, {"r": settle}, settled_at=settled_at)
            stored.append((ep.id, agent, " ".join(words), created, settled_at))
        cutoff = START + timedelta(hours=cutoff_hours)
        point = AsOf(day=cutoff.astimezone(MARKET_TZ).date(), cutoff=cutoff)
        got = memory.recall(AGENT, " ".join(query), point, k=k)

        floor = memory.settings.min_similarity
        qv = embedder.embed([" ".join(query)])[0]
        reference = []
        for id_, agent, text, created, _ in stored:
            if agent == AGENT and created < cutoff:
                sim = float(np.dot(qv, embedder.embed([text])[0]))
                if sim >= floor:
                    reference.append((sim, created, id_))
        reference.sort(key=lambda r: (-r[0], -r[1].timestamp(), r[2]))

        by_id = {s[0]: s for s in stored}
        for m in got:  # never a future memory, never an unsettled outcome
            _, agent, _, created, settled_at = by_id[m.id]
            assert agent == AGENT and created < cutoff
            if settled_at is not None and settled_at < cutoff:
                assert m.outcome is not None and m.settled_at == settled_at
            else:
                assert m.outcome is None and m.settled_at is None
        assert len(got) == min(k, len(reference))
        sims = [r[0] for r in reference]
        boundary_tie = len(sims) > k and abs(sims[k - 1] - sims[k]) < 1e-5
        if not boundary_tie:
            assert ids(got) == {r[2] for r in reference[:k]}
        assert [m.similarity for m in got] == pytest.approx(sims[: len(got)], abs=1e-5)
    finally:
        memory.close()
