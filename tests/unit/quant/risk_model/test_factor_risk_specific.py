"""Specific (residual) variance: EWMA, Bayesian shrinkage to the sector median, floor
(Stage 19 Task 4)."""

import math

import numpy as np
import pandas as pd
import pytest
from factor_risk_world import SECTORS, make_world

from atlas.core.errors import DataError
from atlas.quant.risk_model.regression import constrained_wls, sector_constraint
from atlas.quant.risk_model.specific import (
    ewma_specific_variance,
    shrink_to_sector_median,
    specific_variance,
)


def _frame(columns: dict[str, list[float]]) -> pd.DataFrame:
    n = len(next(iter(columns.values())))
    return pd.DataFrame(columns, index=pd.bdate_range("2020-01-01", periods=n, name="date"))


def test_ewma_specific_variance_known() -> None:
    var, n_obs = ewma_specific_variance(_frame({"A": [0.01, 0.02, 0.03]}), half_life=1)
    # weights 1/4, 1/2, 1 (newest last): (0.25 * 1e-4 + 0.5 * 4e-4 + 1 * 9e-4) / 1.75
    assert var["A"] == pytest.approx((0.25e-4 + 2e-4 + 9e-4) / 1.75, rel=1e-12)
    assert n_obs["A"] == 3


def test_ewma_skips_missing_residuals() -> None:
    var, n_obs = ewma_specific_variance(_frame({"A": [0.01, np.nan, 0.03]}), half_life=1)
    assert var["A"] == pytest.approx((0.25e-4 + 9e-4) / 1.25, rel=1e-12)
    assert n_obs["A"] == 2
    var, n_obs = ewma_specific_variance(_frame({"A": [np.nan, np.nan]}), half_life=1)
    assert np.isnan(var["A"]) and n_obs["A"] == 0


def test_ewma_validation() -> None:
    with pytest.raises(ValueError, match="half_life"):
        ewma_specific_variance(_frame({"A": [0.01, 0.02]}), half_life=0)
    with pytest.raises(ValueError, match="finite"):
        ewma_specific_variance(_frame({"A": [0.01, np.inf]}), half_life=1)


def test_shrinkage_formula_known() -> None:
    vol = pd.Series({"a": 0.01, "b": 0.02, "c": 0.06, "z": 0.05})
    sectors = pd.Series({"a": "X", "b": "X", "c": "X", "z": "Y"})
    out, v = shrink_to_sector_median(vol, sectors, q=1.0)
    delta = float(np.std([0.01, 0.02, 0.06]))  # ddof 0
    for name, x in (("a", 0.01), ("b", 0.02), ("c", 0.06)):
        g = abs(x - 0.02)
        want_v = g / (delta + g)
        assert v[name] == pytest.approx(want_v, rel=1e-12)
        assert out[name] == pytest.approx(want_v * 0.02 + (1 - want_v) * x, rel=1e-12)
    assert v["b"] == 0.0 and out["b"] == 0.02  # the median is unchanged
    assert v["z"] == 0.0 and out["z"] == 0.05  # a single-name sector has nothing to shrink to
    assert out["c"] == pytest.approx(0.034027, abs=1e-6)  # v = 0.04 / 0.0616025 = 0.649326


def test_shrinkage_q_zero_is_identity_and_large_q_goes_to_the_median() -> None:
    vol = pd.Series({"a": 0.01, "b": 0.02, "c": 0.06})
    sectors = pd.Series("X", index=vol.index)
    out0, _ = shrink_to_sector_median(vol, sectors, q=0.0)
    pd.testing.assert_series_equal(out0, vol, check_names=False)
    big, _ = shrink_to_sector_median(vol, sectors, q=1e9)
    assert big.sub(0.02).abs().max() < 1e-6


def test_shrinkage_pulls_outliers_towards_the_median() -> None:
    rng = np.random.default_rng(0)
    vol = pd.Series(np.exp(rng.normal(np.log(0.02), 0.4, 200)), index=[f"n{i}" for i in range(200)])
    sectors = pd.Series("X", index=vol.index)
    out, v = shrink_to_sector_median(vol, sectors, q=1.0)
    med = float(vol.median())
    assert (np.abs(out - med) <= np.abs(vol - med) + 1e-15).all()
    assert out.std() < vol.std()
    assert (v >= 0).all() and (v < 1).all()
    assert v[(vol - med).abs().idxmax()] > v[(vol - med).abs().idxmin()]


def test_shrinkage_validation() -> None:
    vol = pd.Series({"a": 0.01})
    with pytest.raises(ValueError, match="q"):
        shrink_to_sector_median(vol, pd.Series({"a": "X"}), q=-1.0)
    with pytest.raises(ValueError, match="positive"):
        shrink_to_sector_median(pd.Series({"a": -0.01}), pd.Series({"a": "X"}), q=1.0)
    with pytest.raises(ValueError, match="sector"):
        shrink_to_sector_median(vol, pd.Series({"b": "X"}), q=1.0)


def test_short_history_and_unknown_sector_dropped() -> None:
    res = _frame(
        {
            "A": [0.01] * 80,
            "B": [0.02] * 80,
            "NEW": [np.nan] * 40 + [0.02] * 40,
            "NOSEC": [0.01] * 80,
        }
    )
    sectors = pd.Series({"A": "X", "B": "X", "NEW": "X"})
    out = specific_variance(res, sectors, half_life=30, q=1.0, floor_vol=0.002, min_history=63)
    assert out.dropped == {"NEW": "history", "NOSEC": "sector"}
    assert list(out.variance.index) == ["A", "B"]
    assert (out.n_obs == 80).all()


def test_floor_applied() -> None:
    res = _frame({"A": [1e-6] * 80, "B": [0.02] * 80})
    sectors = pd.Series({"A": "X", "B": "Y"})
    out = specific_variance(res, sectors, half_life=30, q=1.0, floor_vol=0.002, min_history=63)
    assert out.variance["A"] == pytest.approx(0.002**2)
    assert out.variance["B"] == pytest.approx(0.02**2, rel=1e-12)
    assert out.floored == ("A",)


def test_nothing_left_raises() -> None:
    res = _frame({"A": [0.01] * 10})
    with pytest.raises(DataError, match="history"):
        specific_variance(
            res, pd.Series({"A": "X"}), half_life=30, q=1.0, floor_vol=0.002, min_history=63
        )


def test_specific_recovers_true_vol_on_synthetic() -> None:
    w = make_world(n=400, t=400, seed=21)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    sw = np.sqrt(w.caps)
    resid = pd.DataFrame(
        [
            constrained_wls(w.returns.iloc[i], w.exposures, sw, constraint).residuals
            for i in range(400)
        ],
        index=w.dates,
    )
    out = specific_variance(resid, w.sectors, half_life=90, q=1.0, floor_vol=0.002, min_history=63)
    rel = (np.sqrt(out.variance) / w.specific_vol.reindex(out.variance.index) - 1).abs()
    assert float(rel.median()) < 0.12 and math.isfinite(float(rel.max()))
    raw_rel = (np.sqrt(out.raw_variance) / w.specific_vol.reindex(out.variance.index) - 1).abs()
    assert float(raw_rel.median()) < 0.15
