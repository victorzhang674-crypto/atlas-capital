"""Stage 21 Task 7: the public API of `atlas.quant.regime`, docstrings with units / labels,
table registration and the data-access rules."""

import inspect
import re
from pathlib import Path

import atlas.quant.regime as regime
from atlas.core.storage import DEFAULT_REGISTRY

PACKAGE = Path(regime.__file__).parent

EXPECTED = {
    "VOL_LABELS", "TREND_LABELS", "QUADRANTS", "VolRegime", "TrendRegime", "Quadrant",
    "trailing_percentile", "vol_score", "classify_vol", "breadth", "trend_votes", "classify_trend",
    "HMMFit", "fit_hmm", "filter_probabilities", "expanding_probabilities", "state_labels",
    "acceleration", "claims_trend", "growth_votes", "growth_score", "inflation_gap",
    "classify_quadrant", "credit_proxy", "trailing_zscore", "credit_zscore", "stress_flag",
    "RegimeState", "risk_scalar", "MIN_RISK_SCALAR", "MAX_RISK_SCALAR",
    "regime_at", "regime_history",
    "REGIME_HISTORY",
    "REGIME_KINDS", "RegimeStore", "build_and_store", "regime_config_hash",
}  # fmt: skip


def test_public_api() -> None:
    assert set(regime.__all__) == EXPECTED
    for name in EXPECTED:
        assert hasattr(regime, name), name


def test_every_public_callable_is_documented() -> None:
    for name in regime.__all__:
        obj = getattr(regime, name)
        if callable(obj) and not isinstance(obj, type(regime.VolRegime)):
            doc = inspect.getdoc(obj)
            assert doc and len(doc) > 40, name


def test_modules_state_units_and_labels() -> None:
    for module in ("vol_trend", "hmm", "macro_quadrant", "state", "loader", "store"):
        doc = (
            inspect.getdoc(getattr(__import__(f"atlas.quant.regime.{module}").quant.regime, module))
            or ""
        ).lower()
        assert doc, module
    vt = inspect.getdoc(regime.vol_trend) or ""
    assert "annualised fraction" in vt and "index points" in vt and "LOW" in vt and "UP" in vt
    st = inspect.getdoc(regime.state) or ""
    assert "[0.25, 1]" in st and "CRISIS 0.4" in st


def test_importing_registers_the_history_table() -> None:
    assert "regime_history" in DEFAULT_REGISTRY.names()


def test_no_clock_and_no_direct_lake_reads_in_the_regime_code() -> None:
    """`datetime.now` is banned; data is read through `DataView` (the loader takes a `Lake` only
    to build views; the store reads its own table)."""
    for path in PACKAGE.glob("*.py"):
        text = path.read_text()
        assert "datetime.now" not in text and "date.today" not in text, path.name
        if path.name not in ("loader.py", "store.py"):
            assert "Lake" not in text and "read_table" not in text, path.name
    loader = (PACKAGE / "loader.py").read_text()
    assert "read_table" not in loader and "read_parquet" not in loader
    assert re.search(r"DataView\(lake", loader)


def test_regime_at_signature_takes_a_view() -> None:
    sig = inspect.signature(regime.regime_at)
    assert list(sig.parameters) == ["view"]
