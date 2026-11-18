"""`atlas.agents.tools` is a package (Phase 3 checkpoint Tier 2 item 5).

Stage 26 built the tools as one module; the briefs of Stages 29, 31 and 33 add
`src/atlas/agents/tools/<name>_tools.py`. The registry and the built-in DataView tools now live in
`tools/registry.py` and `tools/dataview_tools.py`, and every name the package exported before is
still importable from `atlas.agents.tools` (the same objects).
"""

import importlib
import pkgutil

import atlas.agents
import atlas.agents.tools as tools
from atlas.agents.tools import dataview_tools, registry

PUBLIC = {
    "MAX_ERROR_CHARS",
    "FUNDAMENTALS",
    "MARKET_NEWS",
    "NEWS",
    "PRICE_HISTORY",
    "MarketNewsArgs",
    "NewsArgs",
    "PriceHistoryArgs",
    "SymbolArgs",
    "Tool",
    "ToolFn",
    "ToolOutcome",
    "ToolRegistry",
    "default_tools",
}


def test_tools_is_a_package_with_registry_and_dataview_modules() -> None:
    assert hasattr(tools, "__path__")
    modules = {m.name for m in pkgutil.iter_modules(tools.__path__)}
    assert {"registry", "dataview_tools"} <= modules


def test_every_public_name_is_still_importable_from_the_package() -> None:
    assert set(tools.__all__) == PUBLIC
    for name in PUBLIC:
        assert hasattr(tools, name), name


def test_the_package_reexports_the_same_objects() -> None:
    assert tools.Tool is registry.Tool
    assert tools.ToolRegistry is registry.ToolRegistry
    assert tools.ToolOutcome is registry.ToolOutcome
    assert tools.default_tools is dataview_tools.default_tools
    assert tools.NEWS is dataview_tools.NEWS
    assert atlas.agents.ToolRegistry is registry.ToolRegistry
    assert atlas.agents.default_tools is dataview_tools.default_tools


def test_the_registry_module_does_not_import_the_dataview_tools() -> None:
    """A new `<name>_tools.py` imports `registry` alone; the registry must not pull in the
    built-in tools (no import cycle, no hidden coupling)."""
    source = importlib.util.find_spec("atlas.agents.tools.registry")
    assert source is not None and source.origin is not None
    with open(source.origin, encoding="utf-8") as fh:
        text = fh.read()
    imports = [line for line in text.splitlines() if line.startswith(("import ", "from "))]
    assert imports
    assert not [line for line in imports if "dataview_tools" in line]


def test_default_tools_combine_with_a_stage_module_tool() -> None:
    """The pattern a stage's tools module follows: build its tools on `registry` and join them
    to the built-in ones in one registry."""
    from pydantic import BaseModel

    class Args(BaseModel):
        pass

    extra = registry.Tool(name="extra", description="An extra tool.", args=Args, fn=lambda c, a: [])
    joined = registry.ToolRegistry([*tools.default_tools().tools, extra])
    assert joined.names == ("extra", "fundamentals", "market_news", "news", "price_history")
    assert tools.default_tools() is not tools.default_tools()  # a fresh registry each time
