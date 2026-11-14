"""The Stage 13 test world for the Stage 26 agent tests: a clean lake known at the as-of and a
dirty one with later rows in every table, and agent contexts on it."""

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

_DATA = str(Path(__file__).resolve().parents[1] / "data")
if _DATA not in sys.path:
    sys.path.append(_DATA)

from view_helpers import (  # noqa: E402
    WORLD_AS_OF,
    build_world,
    repo_concept_map,
    world_catalog,
    world_lexicon,
)

from atlas.core.storage import DEFAULT_REGISTRY, Lake  # noqa: E402
from atlas.data.view import DataView  # noqa: E402

if TYPE_CHECKING:
    from atlas.agents.context import AgentContext

AGENT_PROMPTS = Path(__file__).resolve().parent / "prompts"
"""Prompt templates of the test agents (the toy echo analyst)."""


@dataclass
class World:
    clean: Lake
    dirty: Lake
    lexicon: Any
    concept_map: Any
    catalog: Any
    views: dict[tuple[str, date], DataView] = field(default_factory=dict)

    def view(self, which: str = "clean", day: date = WORLD_AS_OF) -> DataView:
        """A shared view of the clean or dirty lake at `day` (views are immutable)."""
        key = (which, day)
        if key not in self.views:
            lake = self.clean if which == "clean" else self.dirty
            self.views[key] = DataView(
                lake, day, concept_map=self.concept_map, catalog=self.catalog, lexicon=self.lexicon
            )
        return self.views[key]

    def ctx(self, which: str = "clean", day: date = WORLD_AS_OF, **kwargs: Any) -> "AgentContext":
        """An agent context on a view of the clean or dirty lake."""
        from atlas.agents.context import AgentContext

        return AgentContext(self.view(which, day), **kwargs)


def build_agent_world(root: Path) -> World:
    """Both lakes of the test world under `root`."""
    lakes = []
    for name, future in (("clean", False), ("dirty", True)):
        lake = Lake(root / name, registry=DEFAULT_REGISTRY.copy())
        build_world(lake, future=future)
        lakes.append(lake)
    return World(lakes[0], lakes[1], world_lexicon(root), repo_concept_map(), world_catalog())
