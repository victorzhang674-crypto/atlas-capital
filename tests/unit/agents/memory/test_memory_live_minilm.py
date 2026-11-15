"""Stage 27 Task 7 (live): the real all-MiniLM-L6-v2 model recalls relevant memories from
paraphrased queries. Downloads the model (~79 MB) on the first run into
`agents.memory.model_dir` (set `ATLAS__AGENTS__MEMORY__MODEL_DIR` to move it)."""

from datetime import date
from pathlib import Path

import pytest
from memory_world import AGENT, CORPUS, as_of, build_memory

from atlas.agents.memory.embedder import MiniLMEmbedder, make_embedder
from atlas.core.config import load_config

pytestmark = pytest.mark.live

PARAPHRASES = [
    ("a company topped profit forecasts and lifted its outlook", "earnings"),
    ("the Fed tightened monetary policy again", "rates"),
    ("logistics bottlenecks held up deliveries of parts", "supply"),
    ("competition authorities sued the firm", "legal"),
    ("takeover bid at a premium to the share price", "deal"),
]


def test_minilm_recalls_relevant_memories_from_paraphrases(repo_root: Path) -> None:
    cfg = load_config("dev", config_dir=repo_root / "config")
    embedder = make_embedder(cfg.agents.memory)
    assert isinstance(embedder, MiniLMEmbedder)
    floor = cfg.agents.memory.min_similarity
    # Measure with no floor, to see the whole spread of similarities.
    open_memory = cfg.agents.memory.model_copy(update={"min_similarity": -1.0})
    measured = cfg.model_copy(
        update={"agents": cfg.agents.model_copy(update={"memory": open_memory})}
    )
    memory, seeds = build_memory(embedder=embedder, config=measured)
    try:
        end = as_of(date(2024, 3, 29))
        for query, topic in PARAPHRASES:
            everything = memory.recall(AGENT, query, end, k=len(CORPUS), kinds=["episode"])
            spread = [(round(m.similarity, 3), seeds[m.id].topic) for m in everything]
            print(f"{query!r} -> {spread}")
            assert everything and seeds[everything[0].id].topic == topic, spread
            on = [s for s, t in spread if t == topic]
            off = [s for s, t in spread if t != topic]
            print(
                f"  best on-topic {max(on):.3f}, best off-topic {max(off, default=0):.3f}, "
                f"floor {floor}"
            )
            assert max(on) >= floor
    finally:
        memory.close()
