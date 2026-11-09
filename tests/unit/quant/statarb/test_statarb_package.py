"""Stage 23 Task 5: package surface and hard rules (no clock, no network, DataView only)."""

import ast
from pathlib import Path

import atlas.quant.statarb as statarb

SRC = Path(statarb.__file__).resolve().parent


def test_every_export_resolves() -> None:
    for name in statarb.__all__:
        assert getattr(statarb, name) is not None, name
    assert len(set(statarb.__all__)) == len(statarb.__all__)
    assert {"select_pairs", "pairs_backtest", "PairSignal", "step", "target_weights"} <= set(
        statarb.__all__
    )


def test_brief_files_exist() -> None:
    assert {p.name for p in SRC.glob("*.py")} == {
        "__init__.py", "selection.py", "signals.py", "backtest.py"
    }  # fmt: skip


def test_no_clock_network_or_lake_access() -> None:
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
