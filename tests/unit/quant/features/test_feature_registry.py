"""Stage 15 Task 4: the feature registry."""

import pandas as pd
import pytest

from atlas.quant.features.registry import FeatureRegistry, FeatureSpec, PricePanel


def _close(panel: PricePanel) -> pd.DataFrame:
    """The adjusted close (USD)."""
    return panel.close


def test_feature_decorator_registers_spec() -> None:
    reg = FeatureRegistry()

    @reg.feature("close_level", lookback=1, unit="usd")
    def close_level(panel: PricePanel) -> pd.DataFrame:
        """The adjusted close, USD."""
        return panel.close

    spec = reg.get("close_level")
    assert isinstance(spec, FeatureSpec)
    assert (spec.name, spec.lookback, spec.kind, spec.unit) == ("close_level", 1, "ts", "usd")
    assert spec.func is close_level
    assert spec.benchmark is False
    assert "close_level" in reg
    assert len(reg) == 1
    assert list(reg) == [spec]
    assert reg.names() == ("close_level",)


def test_description_from_docstring() -> None:
    reg = FeatureRegistry()
    reg.feature("a", lookback=1, unit="usd")(_close)
    assert reg.get("a").description == "The adjusted close (USD)."
    reg.feature("b", lookback=1, unit="usd", description="Given text.")(_close)
    assert reg.get("b").description == "Given text."


def test_duplicate_name_rejected() -> None:
    reg = FeatureRegistry()
    reg.feature("a", lookback=1, unit="usd")(_close)
    with pytest.raises(ValueError, match="'a' is already registered"):
        reg.feature("a", lookback=2, unit="usd")(_close)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"name": "Bad-Name", "lookback": 1, "unit": "usd"}, "snake_case"),
        ({"name": "", "lookback": 1, "unit": "usd"}, "snake_case"),
        ({"name": "a", "lookback": 0, "unit": "usd"}, "lookback"),
        ({"name": "a", "lookback": True, "unit": "usd"}, "lookback"),
        ({"name": "a", "lookback": 1, "unit": "usd", "kind": "panel"}, "kind"),
        ({"name": "a", "lookback": 1, "unit": "percent"}, "unit"),
        ({"name": "a", "lookback": 1, "unit": "usd", "description": ""}, "description"),
    ],
)
def test_invalid_spec_rejected(kwargs: dict[str, object], match: str) -> None:
    reg = FeatureRegistry()
    name = kwargs.pop("name")
    with pytest.raises(ValueError, match=match):
        reg.feature(name, **kwargs)(lambda panel: panel.close)  # type: ignore[arg-type]
    assert len(reg) == 0


def test_unknown_name_lists_known() -> None:
    reg = FeatureRegistry()
    reg.feature("a", lookback=1, unit="usd")(_close)
    with pytest.raises(ValueError, match=r"unknown features \['zz', 'yy'\]; known: a"):
        reg.specs(["a", "zz", "yy"])
    with pytest.raises(ValueError, match="unknown feature 'zz'"):
        reg.get("zz")


def test_specs_in_request_order_without_repeats() -> None:
    reg = FeatureRegistry()
    for name in ("a", "b", "c"):
        reg.feature(name, lookback=1, unit="usd")(_close)
    assert [s.name for s in reg.specs(["c", "a", "c"])] == ["c", "a"]
    assert [s.name for s in reg.specs()] == ["a", "b", "c"]  # registration order
    with pytest.raises(ValueError, match="no features"):
        reg.specs([])
    with pytest.raises(ValueError, match="sequence of names"):
        reg.specs("a")


def test_comparable_across_symbols() -> None:
    reg = FeatureRegistry()
    reg.feature("usd_level", lookback=1, unit="usd")(_close)
    reg.feature("frac", lookback=1, unit="fraction")(_close)
    assert reg.get("usd_level").comparable is False
    assert reg.get("frac").comparable is True
