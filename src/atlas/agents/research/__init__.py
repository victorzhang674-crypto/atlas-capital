"""Research analysts (Phase 4): one agent per discipline, each writing a cited `AnalystReport`
(Stage 29 on). Their tools live in `atlas.agents.tools.<name>_tools`; the report checks they
share live in `checks`."""

from atlas.agents.research.fundamental import FundamentalAnalyst, fundamental_registry
from atlas.agents.research.technical import TechnicalAnalyst, technical_registry

__all__ = ["FundamentalAnalyst", "TechnicalAnalyst", "fundamental_registry", "technical_registry"]
