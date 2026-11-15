"""Stage 27 Task 5: `AgentMemory` writes and recall on the synthetic corpus (relevance,
filters, rebuild from the records)."""

from datetime import date
from pathlib import Path

import pytest
from memory_world import AGENT, CORPUS, OTHER, as_of, at, build_memory

from atlas.agents.memory.embedder import HashEmbedder
from atlas.agents.memory.retrieval import AgentMemory
from atlas.agents.memory.store import GLOBAL
from atlas.core.config import AgentsConfig, AtlasConfig, MemoryConfig
from atlas.core.errors import ConfigError
from atlas.core.storage import Database, Lake

END = as_of(date(2024, 3, 29))


def config(**memory: object) -> AtlasConfig:
    return AtlasConfig(agents=AgentsConfig(memory=MemoryConfig.model_validate(memory)))


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    memory, seeds = build_memory()
    yield memory, seeds
    memory.close()


@pytest.mark.parametrize(
    ("query", "topic"),
    [
        ("company beat earnings estimates and raised guidance", "earnings"),
        ("central bank interest rate hike", "rates"),
        ("supply disruption hits shipments and supply of components", "supply"),
        ("antitrust lawsuit by the regulator", "legal"),
        ("acquisition premium paid in cash", "deal"),
    ],
)
def test_recall_returns_relevant_items_first(world, query: str, topic: str) -> None:  # type: ignore[no-untyped-def]
    memory, seeds = world
    got = memory.recall(AGENT, query, END, k=3)
    assert got and seeds[got[0].id].topic == topic
    on_topic = sum(1 for s in seeds.values() if s.agent == AGENT and s.topic == topic)
    assert [seeds[m.id].topic for m in got[: min(on_topic, 2)]] == [topic] * min(on_topic, 2)
    assert [m.similarity for m in got] == sorted((m.similarity for m in got), reverse=True)


def test_recalled_episode_fields(world) -> None:  # type: ignore[no-untyped-def]
    memory, seeds = world
    (top,) = memory.recall(AGENT, "AAA beat quarterly earnings estimates", END, k=1)
    seed = seeds[top.id]
    assert top.kind == "episode" and top.agent == AGENT and top.symbol == seed.symbol
    assert top.text == seed.text and top.created_at == at(seed.day)
    assert top.decision == {"action": "buy", "topic": "earnings"}
    assert 0 < top.similarity <= 1 and top.quality is None


def test_symbol_filters_episodes(world) -> None:  # type: ignore[no-untyped-def]
    memory, seeds = world
    got = memory.recall(AGENT, "earnings beat guidance", END, k=10, symbol="AAA")
    assert got and all(seeds[m.id].symbol == "AAA" for m in got)
    assert memory.recall(AGENT, "earnings", END, symbol="ZZZ") == []


def test_k_defaults_to_the_config_and_caps_the_result(world) -> None:  # type: ignore[no-untyped-def]
    memory, _ = world
    query = "earnings beat raised guidance rates supply"
    assert len(memory.recall(AGENT, query, END, k=2)) == 2
    assert len(memory.recall(AGENT, query, END)) <= memory.settings.default_k
    with pytest.raises(ValueError, match="k"):
        memory.recall(AGENT, query, END, k=0)


def test_min_similarity_drops_unrelated_memories() -> None:
    memory, _ = build_memory(config=config(embedder="hash", min_similarity=0.5))
    try:
        got = memory.recall(AGENT, "earnings beat raised guidance", END, k=50)
        assert got and all(m.similarity >= 0.5 for m in got)
        assert memory.recall(AGENT, "zebra giraffe", END, k=50) == []
    finally:
        memory.close()


def test_kinds_and_lessons_mix(world) -> None:  # type: ignore[no-untyped-def]
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        memory.record_episode(
            AGENT, "earnings beat guidance", {"a": 1}, created_at=at(date(2024, 3, 4))
        )
        lesson = memory.add_lesson(
            GLOBAL, "earnings beat guidance lesson", created_at=at(date(2024, 3, 4))
        )
        both = memory.recall(AGENT, "earnings beat guidance", END)
        assert {m.kind for m in both} == {"episode", "lesson"}
        (only,) = memory.recall(AGENT, "earnings beat guidance", END, kinds=["lesson"])
        assert only.id == lesson.lesson_id and only.kind == "lesson" and only.agent == GLOBAL
        assert only.quality is not None and only.decision is None
        assert {m.kind for m in memory.recall(AGENT, "earnings", END, kinds=["episode"])} == {
            "episode"
        }
        with pytest.raises(ValueError, match="kinds"):
            memory.recall(AGENT, "earnings", END, kinds=["thesis"])  # type: ignore[list-item]
        assert memory.recall(AGENT, "earnings", END, kinds=[]) == []
    finally:
        memory.close()


def test_bad_inputs_are_refused() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        with pytest.raises(ValueError, match="query"):
            memory.recall(AGENT, "   ", END)
        with pytest.raises(ValueError, match="global"):
            memory.recall(GLOBAL, "earnings", END)
        with pytest.raises(ValueError, match="global"):
            memory.record_episode(GLOBAL, "text", {}, created_at=at(date(2024, 3, 4)))
        with pytest.raises(ValueError):
            memory.record_episode(AGENT, "  ", {}, created_at=at(date(2024, 3, 4)))
        with pytest.raises(ValueError):
            memory.add_lesson(AGENT, "  ", created_at=at(date(2024, 3, 4)))
    finally:
        memory.close()


def test_records_and_index_live_in_the_lake(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake")
    with Database(lake) as database:
        memory = AgentMemory(HashEmbedder(), database=database, config=AtlasConfig())
        ep = memory.record_episode(
            AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 4))
        )
        memory.close()
        assert (lake.root / "memory" / "chroma").is_dir()
    with Database(lake) as database:
        again = AgentMemory(HashEmbedder(), database=database, config=AtlasConfig())
        assert [m.id for m in again.recall(AGENT, "earnings beat", END)] == [ep.id]
        again.close()


def test_sync_reindexes_records_missing_from_the_index(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake")
    with Database(lake) as database:
        memory = AgentMemory(HashEmbedder(), database=database, config=AtlasConfig())
        ep = memory.record_episode(
            AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 4))
        )
        les = memory.add_lesson(AGENT, "earnings lesson", created_at=at(date(2024, 3, 4)))
        memory.index.delete("episodes", [ep.id])  # lost from the index (e.g. a crash)
        memory.index.delete("lessons", [les.lesson_id])
        memory.index.add("episodes", [])  # nothing
        assert memory.recall(AGENT, "earnings beat", END) == []
        assert memory.sync() == 2
        assert memory.sync() == 0
        assert {m.id for m in memory.recall(AGENT, "earnings beat", END, k=5)} == {
            ep.id,
            les.lesson_id,
        }
        memory.close()


def test_sync_drops_index_entries_without_a_record() -> None:
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        ep = memory.record_episode(
            AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 4))
        )
        from atlas.agents.memory.index import IndexEntry

        orphan = IndexEntry(
            id="epi_orphan",
            vector=HashEmbedder().embed(["x"])[0],
            agent=AGENT,
            created_at=at(date(2024, 3, 4)),
        )
        memory.index.add("episodes", [orphan])
        memory.sync()
        assert memory.index.ids("episodes") == {ep.id}
    finally:
        memory.close()


def test_another_embedder_needs_a_rebuild(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake")
    with Database(lake) as database:
        memory = AgentMemory(HashEmbedder(dim=64), database=database, config=AtlasConfig())
        ep = memory.record_episode(
            AGENT, "earnings beat", {"a": 1}, created_at=at(date(2024, 3, 4))
        )
        memory.close()
        with pytest.raises(ConfigError, match="rebuild"):
            AgentMemory(HashEmbedder(dim=128), database=database, config=AtlasConfig())
        rebuilt = AgentMemory(
            HashEmbedder(dim=128), database=database, config=AtlasConfig(), rebuild=True
        )
        assert [m.id for m in rebuilt.recall(AGENT, "earnings beat", END)] == [ep.id]
        rebuilt.close()


def test_the_corpus_covers_both_agents() -> None:
    assert {s.agent for s in CORPUS} == {AGENT, OTHER}
