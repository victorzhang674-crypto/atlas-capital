"""The package `atlas.core.types` re-exports every public contract."""

import importlib
import inspect

import atlas.core.types as types
from atlas.core.types.base import AtlasModel

MODULES = [
    "base",
    "ids",
    "instruments",
    "market",
    "research",
    "portfolio",
    "trading",
    "performance",
]


def test_all_exports_resolve() -> None:
    assert len(types.__all__) == len(set(types.__all__))
    for name in types.__all__:
        assert getattr(types, name) is not None


def test_every_public_model_and_enum_is_exported() -> None:
    public: set[str] = set()
    for mod_name in MODULES:
        module = importlib.import_module(f"atlas.core.types.{mod_name}")
        for name, obj in vars(module).items():
            if name.startswith("_") or not inspect.isclass(obj):
                continue
            if obj.__module__ == module.__name__:
                public.add(name)
    assert public <= set(types.__all__)
    assert "AtlasModel" in types.__all__
    assert issubclass(types.Order, AtlasModel)


def test_helpers_exported() -> None:
    for name in ("new_id", "normalize_symbol", "vendor_symbol", "Symbol", "SymbolWeights"):
        assert name in types.__all__
