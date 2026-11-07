"""Stage 22 Task 6: package surface and hard rules (no clock, no network, DataView only)."""

import ast
from pathlib import Path

import atlas.quant.alpha as alpha
from atlas.quant.alpha import AlphaModel, MultiFactorModel, PEADModel, TSMOMModel

SRC = Path(alpha.__file__).resolve().parent


def test_every_export_resolves() -> None:
    for name in alpha.__all__:
        assert getattr(alpha, name) is not None, name
    assert len(set(alpha.__all__)) == len(alpha.__all__)


def test_models_satisfy_protocol() -> None:
    models = (MultiFactorModel(), TSMOMModel(), PEADModel())
    for model in models:
        assert isinstance(model, AlphaModel)
    assert [m.name for m in models] == ["multifactor", "tsmom", "pead"]


def test_no_clock_network_or_lake_access_in_models() -> None:
    banned_calls = {"now", "today", "utcnow"}
    banned_modules = {"requests", "httpx", "urllib", "socket", "duckdb", "atlas.core.storage"}
    for path in SRC.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in banned_calls, f"{path.name}: .{node.attr}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_modules, path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in banned_modules, f"{path.name}: {node.module}"
                assert node.module.split(".")[0] not in banned_modules, path.name
