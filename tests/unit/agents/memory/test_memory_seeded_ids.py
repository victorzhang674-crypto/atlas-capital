"""Memory ids come from the current id source (Phase 3 checkpoint Tier 2 item 1).

`record_episode` and `add_lesson` minted `epi_…` / `les_…` ids with a bare `ULID()`, and
`memory_block` shows lesson ids to the model. Two backtest runs that build the same memories
under the same id seed therefore sent different prompts, so the second could not replay the
first from the LLM cache. The ids now come from `new_id`, like every other minted id.
"""

from datetime import UTC, date, datetime

from atlas.agents.memory import AgentMemory, HashEmbedder, memory_block
from atlas.core.clock import SimClock
from atlas.core.config import AgentsConfig, AtlasConfig, MemoryConfig
from atlas.core.types.ids import IdPrefix, SeededIds, id_pattern, use_ids

CONFIG = AtlasConfig(agents=AgentsConfig(memory=MemoryConfig(embedder="hash", min_similarity=0.1)))
WRITTEN = datetime(2024, 2, 20, 21, tzinfo=UTC)
DECIDED = datetime(2024, 3, 1, 13, tzinfo=UTC)


def _build() -> tuple[str, str, str]:
    """One memory built the same way each time: (episode id, lesson id, prompt block)."""
    memory = AgentMemory(HashEmbedder(), config=CONFIG)
    try:
        episode = memory.record_episode(
            "news", "AAA rallied on profitability news", {"view": "bullish"}, created_at=WRITTEN
        )
        write = memory.add_lesson(
            "news",
            "AAA profitability news was followed by gains",
            created_at=WRITTEN,
            source_episode_ids=[episode.id],
        )
        assert write.stored
        block = memory_block(memory.recall("news", "AAA news", date(2024, 3, 1), 3))
        return episode.id, write.lesson_id, block
    finally:
        memory.close()


def _seeded(seed: int) -> tuple[str, str, str]:
    with use_ids(SeededIds(seed, SimClock(DECIDED))):
        return _build()


def test_the_same_seed_gives_the_same_ids_and_the_same_prompt_text() -> None:
    first, second = _seeded(7), _seeded(7)
    assert first == second
    assert first[1] in first[2]  # the lesson id is what the model sees


def test_another_seed_gives_other_ids() -> None:
    assert _seeded(7)[:2] != _seeded(8)[:2]


def test_ids_keep_their_prefixes_and_shape() -> None:
    episode_id, lesson_id, _ = _seeded(7)
    import re

    assert re.fullmatch(id_pattern(IdPrefix.EPISODE), episode_id)
    assert re.fullmatch(id_pattern(IdPrefix.LESSON), lesson_id)


def test_without_a_seed_ids_stay_unique() -> None:
    assert _build()[:2] != _build()[:2]
