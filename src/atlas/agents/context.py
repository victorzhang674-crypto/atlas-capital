"""`AgentContext`: everything one agent run may use, bound to one point in time."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from atlas.core.pit import AsOf
from atlas.core.run_context import current_run_context
from atlas.core.types.base import FrozenDict
from atlas.data.view import DataView


@dataclass(frozen=True)
class AgentContext:
    """The inputs of one agent run.

    `view` is the agent's only door to data (point in time: it knows nothing after
    `view.as_of`); `as_of` and `day` are the view's, so they cannot disagree with it.
    `run_id` defaults to the current run context's (`None` outside a run). `memory` is the
    agent's memory handle (Stage 27; optional, opaque here). `inputs` are extra values for the
    agent's prompt (a ticker, a debate transcript, ...), kept as a read-only copy.
    """

    view: DataView
    run_id: str | None = None
    memory: Any | None = None
    inputs: Mapping[str, Any] = field(default_factory=lambda: FrozenDict({}))

    def __post_init__(self) -> None:
        if self.run_id is None:
            run = current_run_context()
            object.__setattr__(self, "run_id", run.run_id if run is not None else None)
        elif not self.run_id:
            raise ValueError("run_id must be a non-empty string or None")
        object.__setattr__(self, "inputs", FrozenDict(dict(self.inputs)))

    @property
    def as_of(self) -> AsOf:
        """The decision moment: the view's `as_of`."""
        return self.view.as_of

    @property
    def day(self) -> date:
        """The trading day of `as_of`."""
        return self.view.day
