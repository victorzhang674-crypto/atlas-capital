"""Portfolio analytics on a RiskModel (Stage 19 Task 6)."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.core.errors import DataError
from atlas.quant.risk_model.analytics import (
    PortfolioRisk,
    bias_statistic,
    factor_exposures,
    marginal_contribution,
    portfolio_risk,
)
from atlas.quant.risk_model.factor_model import RiskModel


def random_model(n: int = 12, k: int = 4, seed: int = 0) -> RiskModel:
    rng = np.random.default_rng(seed)
    a = rng.normal(scale=0.01, size=(k, k))
    return RiskModel(
        as_of=date(2020, 1, 2),
        symbols=tuple(f"N{i:02d}" for i in range(n)),
        factors=tuple(f"F{j}" for j in range(k)),
        x=rng.normal(size=(n, k)),
        f=a @ a.T + np.eye(k) * 1e-5,
        d=rng.uniform(1e-4, 5e-4, size=n),
    )


def _w(model: RiskModel, seed: int, scale: float = 1.0) -> pd.Series:
    w = np.random.default_rng(seed).normal(size=len(model.symbols)) * scale
    return pd.Series(w, index=list(model.symbols))


@settings(max_examples=40, deadline=None)
@given(seed=st.integers(0, 10_000), scale=st.floats(0.001, 5.0))
def test_decomposition_sums_to_total(seed: int, scale: float) -> None:
    m = random_model(seed=seed % 50)
    w = _w(m, seed, scale)
    r = portfolio_risk(m, w)
    sigma = m.cov().to_numpy()
    direct = float(w.to_numpy() @ sigma @ w.to_numpy())
    assert r.total_var == pytest.approx(direct, rel=1e-10)
    assert r.factor_var + r.specific_var == pytest.approx(r.total_var, rel=1e-12)
    assert float(r.by_factor.sum()) == pytest.approx(r.factor_var, rel=1e-10)
    assert float(r.contribution.sum()) == pytest.approx(r.total_vol, rel=1e-10)
    assert r.total_vol == pytest.approx(math.sqrt(r.total_var), rel=1e-12)
    assert r.factor_vol**2 == pytest.approx(r.factor_var, rel=1e-12)
    assert r.specific_vol**2 == pytest.approx(r.specific_var, rel=1e-12)


def test_hand_example() -> None:
    m = RiskModel(
        as_of=date(2020, 1, 2),
        symbols=("A", "B"),
        factors=("f1", "f2"),
        x=np.eye(2),
        f=np.array([[4e-4, 5e-5], [5e-5, 1e-4]]),
        d=np.array([1e-4, 2e-4]),
    )
    r = portfolio_risk(m, pd.Series({"A": 0.5, "B": 0.5}))
    assert r.factor_var == pytest.approx(1.5e-4, rel=1e-12)
    assert r.specific_var == pytest.approx(7.5e-5, rel=1e-12)
    assert r.total_var == pytest.approx(2.25e-4, rel=1e-12)
    assert r.total_vol == pytest.approx(0.015, rel=1e-12)
    np.testing.assert_allclose(r.by_factor.to_numpy(), [1.125e-4, 3.75e-5], rtol=1e-12)
    assert r.exposures.to_dict() == {"f1": 0.5, "f2": 0.5}
    assert r.annual_vol() == pytest.approx(0.015 * math.sqrt(252), rel=1e-12)
    assert r.annual_vol(100) == pytest.approx(0.15, rel=1e-12)
    # marginal contribution: (Sigma w)_n / sigma_p, Sigma = X F X' + D
    sigma = m.cov().to_numpy()
    np.testing.assert_allclose(r.mctr.to_numpy(), sigma @ [0.5, 0.5] / 0.015, rtol=1e-12)


def test_factor_exposures_are_x_transpose_w() -> None:
    m = random_model(seed=3)
    w = _w(m, 4)
    got = factor_exposures(m, w)
    np.testing.assert_allclose(got.to_numpy(), m.x.T @ w.to_numpy(), rtol=1e-12)
    assert list(got.index) == list(m.factors)
    assert factor_exposures(m, w * 0.0).abs().max() == 0.0


def test_mctr_is_the_gradient_of_vol() -> None:
    m = random_model(seed=5)
    w = _w(m, 6, 0.1)
    mctr = marginal_contribution(m, w)
    h = 1e-7
    for i in (0, 4, 9):
        up, dn = w.copy(), w.copy()
        up.iloc[i] += h
        dn.iloc[i] -= h
        fd = (portfolio_risk(m, up).total_vol - portfolio_risk(m, dn).total_vol) / (2 * h)
        assert mctr.iloc[i] == pytest.approx(fd, rel=1e-5)
    assert (mctr.index == pd.Index(m.symbols)).all()


def test_zero_portfolio_has_zero_risk() -> None:
    m = random_model(seed=1)
    r = portfolio_risk(m, pd.Series(0.0, index=list(m.symbols)))
    assert r.total_var == 0.0 and r.total_vol == 0.0 and float(r.mctr.abs().sum()) == 0.0
    assert float(r.contribution.abs().sum()) == 0.0


def test_unknown_names_only_matter_with_nonzero_weight() -> None:
    m = random_model(seed=2)
    w = _w(m, 1)
    ok = pd.concat([w, pd.Series({"GHOST": 0.0})])
    assert portfolio_risk(m, ok).total_var == portfolio_risk(m, w).total_var
    with pytest.raises(DataError, match="GHOST"):
        portfolio_risk(m, pd.concat([w, pd.Series({"GHOST": 0.01})]))


def test_weights_validated() -> None:
    m = random_model(seed=2)
    w = _w(m, 1)
    with pytest.raises(ValueError, match="finite"):
        portfolio_risk(m, w.where(w.index != "N00", np.nan))
    dup = pd.concat([w, w.iloc[:1]])
    with pytest.raises(ValueError, match="duplicate"):
        portfolio_risk(m, dup)
    with pytest.raises(ValueError, match="Series"):
        portfolio_risk(m, w.to_numpy())  # type: ignore[arg-type]


def test_portfolio_risk_is_independent_of_weight_order() -> None:
    m = random_model(seed=2)
    w = _w(m, 1)
    a, b = portfolio_risk(m, w), portfolio_risk(m, w.iloc[::-1])
    assert isinstance(a, PortfolioRisk)
    assert a.total_var == pytest.approx(b.total_var, rel=1e-14)
    pd.testing.assert_series_equal(a.mctr, b.mctr)


def test_bias_statistic_known_and_validation() -> None:
    idx = pd.bdate_range("2020-01-01", periods=40)
    realised = pd.Series(np.tile([0.02, -0.02], 20), index=idx)
    assert bias_statistic(realised, pd.Series(0.01, index=idx)) == pytest.approx(2.0, rel=1e-12)
    assert bias_statistic(realised, pd.Series(0.02, index=idx)) == pytest.approx(1.0, rel=1e-12)
    short = realised.copy()
    short.iloc[:25] = np.nan
    with pytest.raises(DataError, match="20"):
        bias_statistic(short, pd.Series(0.01, index=idx))
    zero = pd.Series(0.0, index=idx)
    with pytest.raises(DataError, match="20"):
        bias_statistic(realised, zero)
