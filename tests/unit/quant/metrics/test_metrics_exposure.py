"""Exposure metrics (Stage 20 Task 8)."""

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.core.errors import DataError
from atlas.quant.metrics.exposure import (
    UNCLASSIFIED,
    ExposureSummary,
    beta_adjusted_net,
    exposure_summary,
    group_gross,
    group_net,
)

W = pd.Series({"AAA": 0.05, "BBB": 0.03, "CCC": -0.02, "DDD": -0.04, "SPY": -0.01, "EEE": 0.0})
SECTORS = pd.Series(
    {"AAA": "Energy", "BBB": "Financials", "CCC": "Energy", "DDD": "Financials", "EEE": "Energy"}
)
BETAS = pd.Series({"AAA": 1.2, "BBB": 0.8, "CCC": 1.5, "DDD": 1.0, "SPY": 1.0})


def test_hand_example() -> None:
    s = exposure_summary(W)
    assert isinstance(s, ExposureSummary)
    assert s.gross == pytest.approx(0.15)
    assert s.net == pytest.approx(0.01)
    assert s.long == pytest.approx(0.08) and s.short == pytest.approx(-0.07)
    assert (s.n_long, s.n_short) == (2, 3)
    assert s.beta_adjusted_net is None and s.sector_net is None and s.unclassified == ()


def test_beta_adjusted_net() -> None:
    want = 0.05 * 1.2 + 0.03 * 0.8 - 0.02 * 1.5 - 0.04 * 1.0 - 0.01 * 1.0
    assert beta_adjusted_net(W, BETAS) == pytest.approx(want)  # EEE: zero weight, no beta needed
    assert exposure_summary(W, betas=BETAS).beta_adjusted_net == pytest.approx(want)


def test_missing_beta_raises() -> None:
    with pytest.raises(DataError, match="no beta for held names"):
        beta_adjusted_net(W, BETAS.drop("CCC"))
    with pytest.raises(DataError, match="SPY"):
        exposure_summary(W, betas=BETAS.replace(1.0, np.nan))
    with pytest.raises(ValueError, match="Series"):
        beta_adjusted_net(W, {"AAA": 1.0})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="duplicate"):
        beta_adjusted_net(W, pd.Series([1.0, 1.0], index=["AAA", "AAA"]))


def test_sector_nets_and_unclassified() -> None:
    s = exposure_summary(W, sectors=SECTORS)
    assert s.sector_net is not None
    assert s.sector_net.to_dict() == pytest.approx(
        {"Energy": 0.03, "Financials": -0.01, UNCLASSIFIED: -0.01}
    )
    assert list(s.sector_net.index) == sorted(s.sector_net.index)
    assert s.unclassified == ("SPY",)
    mapping = {"AAA": "Energy", "BBB": "", "CCC": "Energy"}
    nets = group_net(W, mapping)
    assert nets.to_dict() == pytest.approx({"Energy": 0.03, UNCLASSIFIED: -0.02})


def test_group_gross() -> None:
    gross = group_gross(W, SECTORS)
    assert gross.to_dict() == pytest.approx(
        {"Energy": 0.07, "Financials": 0.07, UNCLASSIFIED: 0.01}
    )
    with pytest.raises(ValueError, match="duplicate"):
        group_gross(W, pd.Series(["a", "b"], index=["AAA", "AAA"]))


def test_validation() -> None:
    with pytest.raises(ValueError, match="Series"):
        exposure_summary([0.1, 0.2])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        exposure_summary(pd.Series({"A": np.inf}))
    empty = exposure_summary(pd.Series(dtype="float64"))
    assert (empty.gross, empty.net, empty.long, empty.short) == (0.0, 0.0, 0.0, 0.0)


@settings(max_examples=150, deadline=None)
@given(st.lists(st.floats(min_value=-0.2, max_value=0.2), min_size=0, max_size=40))
def test_exposure_identities(values: list[float]) -> None:
    w = pd.Series(values, index=[f"N{i}" for i in range(len(values))], dtype="float64")
    s = exposure_summary(w)
    assert s.net == pytest.approx(s.long + s.short, abs=1e-12)
    assert s.gross == pytest.approx(s.long - s.short, abs=1e-12)
    assert abs(s.net) <= s.gross + 1e-12
    assert s.long >= 0.0 >= s.short
    sectors = pd.Series({k: ("S1" if i % 2 else "S2") for i, k in enumerate(w.index)})
    if len(w):
        assert group_net(w, sectors).sum() == pytest.approx(s.net, abs=1e-12)
        assert group_gross(w, sectors).sum() == pytest.approx(s.gross, abs=1e-12)
