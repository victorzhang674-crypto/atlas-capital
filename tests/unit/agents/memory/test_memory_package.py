"""Stage 27 Task 9: the public API of `atlas.agents.memory`, the context door and the docs."""

import ast
from datetime import date, timedelta
from pathlib import Path

import atlas.agents.memory as memory_pkg
from atlas.agents.context import AgentContext
from atlas.agents.memory import AgentMemory, HashEmbedder
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.view import DataView

STAGE_27 = {
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
}

MEMORY_DIR = Path(memory_pkg.__file__).resolve().parent


def test_public_api_is_exported() -> None:
    assert set(memory_pkg.__all__) == STAGE_27
    assert len(memory_pkg.__all__) == len(set(memory_pkg.__all__))
    for name in STAGE_27:
        assert getattr(memory_pkg, name) is not None


def test_every_module_has_a_docstring() -> None:
    for path in sorted(MEMORY_DIR.rglob("*.py")):
        assert ast.get_docstring(ast.parse(path.read_text())), path


def test_docs_explain_memory(repo_root: Path) -> None:
    text = (repo_root / "docs" / "agents.md").read_text()
    for needle in (
        "## Memory",
        "AgentMemory",
        "recall",
        "ctx.recall",
        "AsOf.knows",
        "settled_at",
        "dedupe_cosine",
        "max_per_agent",
        "half_life_days",
        "min_similarity",
        "memory_block",
        "_partials/_memory.j2",
        "HashEmbedder",
        "all-MiniLM-L6-v2",
        "rebuild=True",
    ):
        assert needle in text, needle


def test_context_recall_is_bound_to_the_views_as_of(tmp_lake: Lake) -> None:
    view = DataView(tmp_lake, date(2024, 3, 11))
    assert AgentContext(view).recall("analyst", "earnings beat") == []  # no memory: nothing
    memory = AgentMemory(HashEmbedder(), config=AtlasConfig())
    try:
        cutoff = view.as_of.cutoff
        seen = memory.record_episode(
            "analyst", "earnings beat", {"a": 1}, created_at=cutoff - timedelta(days=1)
        )
        memory.record_episode(
            "analyst", "earnings beat later", {"a": 2}, created_at=cutoff + timedelta(hours=1)
        )
        ctx = AgentContext(view, memory=memory)
        got = ctx.recall("analyst", "earnings beat", k=5)
        assert [m.id for m in got] == [seen.id]
        assert got == memory.recall("analyst", "earnings beat", view.as_of, k=5)
        assert ctx.recall("analyst", "earnings beat", kinds=["lesson"]) == []
        assert ctx.recall("analyst", "earnings beat", symbol="ZZZ") == []
    finally:
        memory.close()
