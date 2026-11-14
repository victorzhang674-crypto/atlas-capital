"""Every top-level subpackage from design spec §12 exists as a real package."""

import importlib
import pkgutil
from pathlib import Path

import pytest

import atlas

SUBPACKAGES = [
    "core",
    "data",
    "quant",
    "llm",
    "agents",
    "portfolio",
    "risk",
    "compliance",
    "execution",
    "accounting",
    "performance",
    "orchestration",
    "backtest",
    "live",
    "reporting",
    "dashboard",
    "cli",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"atlas.{name}")
    doc = module.__doc__
    assert doc is not None
    assert doc.strip()
    assert "\n" not in doc.strip()


def test_no_unexpected_subpackages() -> None:
    found = {m.name for m in pkgutil.iter_modules(atlas.__path__) if m.ispkg}
    assert found == set(SUBPACKAGES)


def test_every_source_dir_has_init(repo_root: Path) -> None:
    src = repo_root / "src" / "atlas"
    # Dot-folders are not packages (tools leave e.g. `.cc-writes` behind; Tier 4 item 4).
    dirs = [
        src,
        *(
            d
            for d in src.rglob("*")
            if d.is_dir()
            and d.name != "__pycache__"
            and not any(part.startswith(".") for part in d.relative_to(src).parts)
        ),
    ]
    missing = [d for d in dirs if not (d / "__init__.py").is_file()]
    assert missing == []
