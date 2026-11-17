"""Phase 3 checkpoint probe: memory ids in prompts (finding 6).

Two memories built the same way render differently, because `add_lesson` mints a random id
and `render_memory` prints it. A second backtest run that rebuilds its memory therefore sends
other prompts than the first: the LLM cache cannot replay it.
Usage: uv run python <this file>
"""

from datetime import UTC, date, datetime

from atlas.agents.memory import AgentMemory, HashEmbedder, memory_block
from atlas.core.clock import SimClock
from atlas.core.config import AgentsConfig, AtlasConfig, MemoryConfig
from atlas.core.types.ids import SeededIds, use_ids

CONFIG = AtlasConfig(agents=AgentsConfig(memory=MemoryConfig(embedder="hash", min_similarity=0.1)))


def prompt_text() -> str:
    memory = AgentMemory(HashEmbedder(), config=CONFIG)
    memory.add_lesson(
        "news",
        "AAA profitability news was followed by gains",
        created_at=datetime(2024, 2, 20, 21, tzinfo=UTC),
    )
    return memory_block(memory.recall("news", "AAA news", date(2024, 3, 1), 3))


first, second = prompt_text(), prompt_text()
print("two identically built memories give the same prompt text:", first == second)
print("  run 1:", first.splitlines()[1][:64])
print("  run 2:", second.splitlines()[1][:64])
seeded = []
for _ in range(2):
    with use_ids(SeededIds(7, SimClock(datetime(2024, 3, 1, 13, tzinfo=UTC)))):
        seeded.append(prompt_text())
print("same text under one id seed (ids are not taken from the id source):", seeded[0] == seeded[1])
