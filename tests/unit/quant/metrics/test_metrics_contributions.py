"""Risk contributions: marginal, component, percent (Stage 20 Task 6)."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.metrics.contributions import RiskContributions, risk_contributions
from atlas.quant.risk_model.analytics import portfolio_risk
from atlas.quant.risk_model.factor_model import RiskModel

COV2 = pd.DataFrame([[0.04, 0.006], [0.006, 0.09]], index=["X", "Y"], columns=["X", "Y"])


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


def test_hand_example() -> None:
    res = risk_contributions(pd.Series({"X": 0.6, "Y": 0.4}), COV2)
    assert isinstance(res, RiskContributions)
    vol = math.sqrt(0.03168)
    assert res.vol == pytest.approx(vol, rel=1e-12)
    assert res.marginal.tolist() == pytest.approx([0.0264 / vol, 0.0396 / vol], rel=1e-12)
    assert res.component.tolist() == pytest.approx([0.01584 / vol, 0.01584 / vol], rel=1e-12)
    assert res.percent.tolist() == pytest.approx([0.5, 0.5], rel=1e-12)
    assert list(res.marginal.index) == ["X", "Y"] and res.marginal.index.name == "symbol"
    assert res.annual_vol() == pytest.approx(vol * math.sqrt(252), rel=1e-12)
    cfg = AtlasConfig.model_validate({"quant": {"features": {"sessions_per_year": 250}}})
    assert res.annual_vol(config=cfg) == pytest.approx(vol * math.sqrt(250), rel=1e-12)


def test_short_hedge_has_negative_percent() -> None:
    cov = pd.DataFrame([[0.04, 0.03], [0.03, 0.04]], index=["L", "H"], columns=["L", "H"])
    res = risk_contributions(pd.Series({"L": 1.0, "H": -0.5}), cov)
    assert res.percent["H"] < 0 < res.percent["L"]
    assert res.percent.sum() == pytest.approx(1.0)


def test_marginal_is_the_gradient_of_vol() -> None:
    rng = np.random.default_rng(1)
    a = rng.normal(size=(6, 6)) * 0.01
    cov = a @ a.T + np.eye(6) * 1e-5
    w = rng.normal(size=6)
    res = risk_contributions(w, cov)
    eps = 1e-7
    for i in range(6):
        up, dn = w.copy(), w.copy()
        up[i] += eps
        dn[i] -= eps
        grad = (math.sqrt(up @ cov @ up) - math.sqrt(dn @ cov @ dn)) / (2 * eps)
        assert res.marginal.iloc[i] == pytest.approx(grad, rel=1e-6)


@settings(max_examples=150, deadline=None)
@given(
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.floats(min_value=0.01, max_value=10.0),
)
def test_component_contributions_sum_to_total_vol(n: int, seed: int, scale: float) -> None:
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(n, n + 2)) * 0.02
    cov = a @ a.T
    w = rng.normal(size=n) * scale
    res = risk_contributions(w, cov)
    vol = math.sqrt(max(float(w @ cov @ w), 0.0))
    assert res.vol == pytest.approx(vol, rel=1e-12, abs=1e-15)
    assert res.component.sum() == pytest.approx(res.vol, rel=1e-9, abs=1e-15)
    if res.vol > 0:
        assert res.percent.sum() == pytest.approx(1.0, rel=1e-9)
    np.testing.assert_allclose(res.component.to_numpy(), w * res.marginal.to_numpy())


def test_matches_stage19_portfolio_risk() -> None:
    model = random_model()
    rng = np.random.default_rng(4)
    w = pd.Series(rng.normal(size=len(model.symbols)) * 0.05, index=list(model.symbols))
    w = w.drop(w.index[3])  # an absent name has zero weight
    via_model = risk_contributions(w, model)
    via_cov = risk_contributions(w, model.cov())
    risk = portfolio_risk(model, w)
    assert via_model.vol == pytest.approx(via_cov.vol, rel=1e-10) == risk.total_vol
    for attr in ("marginal", "component", "percent"):
        pd.testing.assert_series_equal(
            getattr(via_model, attr), getattr(via_cov, attr), rtol=1e-9, check_names=True
        )
    pd.testing.assert_series_equal(
        via_model.component, risk.contribution.rename("component"), rtol=1e-12
    )
    with pytest.raises(ValueError, match="Series"):
        risk_contributions(w.to_numpy(), model)
    with pytest.raises(DataError, match="not in the risk model"):
        risk_contributions(pd.Series({"ZZZ": 0.1}), model)


def test_zero_portfolio() -> None:
    res = risk_contributions(pd.Series({"X": 0.0, "Y": 0.0}), COV2)
    assert res.vol == 0.0
    assert (res.marginal == 0).all() and (res.component == 0).all()
    assert res.percent.isna().all()
    model_res = risk_contributions(pd.Series(dtype="float64"), random_model())
    assert model_res.vol == 0.0 and model_res.percent.isna().all()


def test_names_alignment_and_unknown_names() -> None:
    w = pd.Series({"Y": 0.4, "X": 0.6, "Z": 0.0})
    res = risk_contributions(w, COV2)
    assert list(res.component.index) == ["X", "Y"]
    assert res.vol == pytest.approx(math.sqrt(0.03168))
    partial = risk_contributions(pd.Series({"Y": 1.0}), COV2)
    assert partial.component["X"] == 0.0 and partial.vol == pytest.approx(0.3)
    with pytest.raises(DataError, match="without an estimate"):
        risk_contributions(pd.Series({"X": 0.6, "Z": 0.1}), COV2)


def test_rejects_invalid_covariances() -> None:
    w = np.array([0.5, 0.5])
    with pytest.raises(ValueError, match="symmetric"):
        risk_contributions(w, np.array([[0.04, 0.01], [0.02, 0.09]]))
    with pytest.raises(ValueError, match="positive semi-definite"):
        risk_contributions(w, np.array([[0.04, 0.1], [0.1, 0.04]]))
    with pytest.raises(ValueError, match="one value per name"):
        risk_contributions(np.array([1.0]), np.eye(2))
    with pytest.raises(ValueError, match="finite"):
        risk_contributions(pd.Series({"X": np.inf}), COV2)


def test_array_inputs_use_positions() -> None:
    res = risk_contributions(np.array([0.6, 0.4]), COV2.to_numpy())
    assert list(res.component.index) == [0, 1]
    assert res.percent.tolist() == pytest.approx([0.5, 0.5])
