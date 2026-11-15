"""Agent memory (Stage 27): episodes and lessons, recalled by similarity, point-in-time.

`AgentMemory` is the entry point: `record_episode`, `settle_episode`, `add_lesson` and
`recall(agent, query_text, as_of, k=5, symbol=None) -> list[Memory]`. Records live in the
lake's native `episodes` / `lessons` tables (`store`), vectors in a ChromaDB index
(`index`) made by an `Embedder` (`embedder`); `render` turns memories into prompt lines.
"""

from atlas.agents.memory.embedder import Embedder, HashEmbedder, MiniLMEmbedder, make_embedder
from atlas.agents.memory.index import Hit, IndexEntry, VectorIndex
from atlas.agents.memory.render import NO_MEMORIES, memory_block, render_memory
from atlas.agents.memory.retrieval import (
    MEMORY_KINDS,
    AgentMemory,
    LessonWrite,
    Memory,
    MemoryKind,
    effective_quality,
)
from atlas.agents.memory.store import GLOBAL, Episode, MemoryStore, StoredLesson

__all__ = [
    "GLOBAL",
    "MEMORY_KINDS",
    "NO_MEMORIES",
    "AgentMemory",
    "Embedder",
    "Episode",
    "HashEmbedder",
    "Hit",
    "IndexEntry",
    "LessonWrite",
    "Memory",
    "MemoryKind",
    "MemoryStore",
    "MiniLMEmbedder",
    "StoredLesson",
    "VectorIndex",
    "effective_quality",
    "make_embedder",
    "memory_block",
    "render_memory",
]
