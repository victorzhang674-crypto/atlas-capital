"""Research analysts (Phase 4): one agent per discipline, each writing a cited `AnalystReport`
(Stage 29 on). Their tools live in `atlas.agents.tools.<name>_tools`."""

from atlas.agents.research.fundamental import FundamentalAnalyst, fundamental_registry

__all__ = ["FundamentalAnalyst", "fundamental_registry"]
