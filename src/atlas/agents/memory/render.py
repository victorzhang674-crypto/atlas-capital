"""Memories as compact prompt text: 2-4 lines each, inside one `memory` data block.

An episode: a header (id, kind, day, agent, symbol, similarity), the situation, the decision
and the outcome (or `outcome: not known yet`). A lesson: a header (id, kind, day, scope,
effective quality, similarity), the lesson and its tags (if any). Whitespace inside a text is
collapsed to single spaces and each line is cut at `max_chars` (`agents.memory.render`).
Memories hold past model text, so the block goes through `data_block` like any untrusted
data (Stage 24 hygiene).
"""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from atlas.core.audit import canonical_json
from atlas.core.config import MemoryRenderConfig
from atlas.llm.hygiene import data_block

if TYPE_CHECKING:
    from atlas.agents.memory.retrieval import Memory

NO_MEMORIES = "(no memories)"
"""The block's text when nothing was recalled."""

SOURCE = "memory"
"""The data block source label."""

_ELLIPSIS = "…"


def _clip(text: str, max_chars: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= max_chars else line[: max_chars - 1] + _ELLIPSIS


def _json(value: Mapping[str, Any]) -> str:
    return canonical_json(dict(value))


def render_memory(memory: "Memory", *, max_chars: int | None = None) -> str:
    """`memory` as 2-4 lines (see the module docstring); `max_chars` defaults to
    `MemoryRenderConfig().max_chars`."""
    width = MemoryRenderConfig().max_chars if max_chars is None else max_chars
    day = memory.created_at.date().isoformat()
    if memory.kind == "episode":
        parts = [f"[{memory.id}] episode {day}", memory.agent]
        if memory.symbol is not None:
            parts.append(memory.symbol)
        parts.append(f"similarity {memory.similarity:.2f}")
        lines = [
            " · ".join(parts),
            f"situation: {memory.text}",
            f"decision: {_json(memory.decision or {})}",
        ]
        if memory.outcome is not None and memory.settled_at is not None:
            known = memory.settled_at.date().isoformat()
            lines.append(f"outcome (known {known}): {_json(memory.outcome)}")
        else:
            lines.append("outcome: not known yet")
    else:
        quality = "n/a" if memory.quality is None else f"{memory.quality:.2f}"
        lines = [
            f"[{memory.id}] lesson {day} · {memory.agent} · quality {quality} "
            f"· similarity {memory.similarity:.2f}",
            f"lesson: {memory.text}",
        ]
        if memory.tags:
            lines.append(f"tags: {', '.join(memory.tags)}")
    return "\n".join(_clip(line, width) for line in lines)


def memory_block(memories: Iterable["Memory"], *, max_chars: int | None = None) -> str:
    """`memories` rendered one after another (a blank line between them) inside
    `data_block(..., source="memory")`; `(no memories)` when there are none."""
    texts = [render_memory(m, max_chars=max_chars) for m in memories]
    return data_block("\n\n".join(texts) if texts else NO_MEMORIES, source=SOURCE)
