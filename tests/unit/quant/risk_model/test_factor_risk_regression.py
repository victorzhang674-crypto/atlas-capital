"""Constrained cross-sectional WLS (Stage 19 Task 2)."""

import numpy as np
import pandas as pd
import pytest
from factor_risk_world import SECTORS, make_world

from atlas.core.errors import DataError
from atlas.quant.risk_model.regression import RegressionResult, constrained_wls, sector_constraint


def _cross_section(world, i: int):
    r = world.returns.iloc[i]
    return r, world.exposures, np.sqrt(world.caps)


def test_sector_constraint_holds() -> None:
    w = make_world(n=300, t=30, seed=1)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    for i in range(10):
        r, x, weights = _cross_section(w, i)
        res = constrained_wls(r, x, weights, constraint)
        assert abs(float(res.factor_returns @ constraint)) < 1e-12
    shares = w.caps.groupby(w.sectors).sum() / w.caps.sum()
    assert constraint["market"] == 0.0
    np.testing.assert_allclose(
        constraint[list(SECTORS)].to_numpy(), shares.reindex(list(SECTORS)).to_numpy()
    )


def test_noise_free_returns_recovered_exactly() -> None:
    w = make_world(n=300, t=20, seed=2, factor_only=True)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    for i in range(20):
        r, x, weights = _cross_section(w, i)
        res = constrained_wls(r, x, weights, constraint)
        np.testing.assert_allclose(
            res.factor_returns.to_numpy(), w.factor_returns.iloc[i].to_numpy(), atol=1e-12
        )
        assert float(res.residuals.abs().max()) < 1e-12
        assert res.r2 == pytest.approx(1.0, abs=1e-12)
        assert res.n_names == 300


def test_matches_dense_kkt_solution() -> None:
    w = make_world(n=150, t=5, seed=3)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    r, x, weights = _cross_section(w, 0)
    res = constrained_wls(r, x, weights, constraint)
    xm, c, wd = x.to_numpy(), constraint.to_numpy(), np.diag(weights.to_numpy())
    k = xm.shape[1]
    kkt = np.block([[xm.T @ wd @ xm, c[:, None]], [c[None, :], np.zeros((1, 1))]])
    rhs = np.concatenate([xm.T @ wd @ r.to_numpy(), [0.0]])
    sol = np.linalg.solve(kkt, rhs)[:k]
    np.testing.assert_allclose(res.factor_returns.to_numpy(), sol, atol=1e-10)
    np.testing.assert_allclose(res.residuals.to_numpy(), r.to_numpy() - xm @ sol, atol=1e-12)


def test_wls_weights_matter() -> None:
    errors_wls, errors_ols = [], []
    for seed in range(12):
        w = make_world(n=300, t=1, seed=seed, factor_only=True)
        rng = np.random.default_rng(seed)
        noise = rng.normal(size=300) / np.sqrt(np.sqrt(w.caps.to_numpy()))  # var ~ 1/sqrt(cap)
        noise *= 0.02 / noise.std()
        r = w.returns.iloc[0] + noise
        constraint = sector_constraint(w.exposures, w.caps, SECTORS)
        truth = w.factor_returns.iloc[0]
        for weights, out in ((np.sqrt(w.caps), errors_wls), (w.caps * 0 + 1.0, errors_ols)):
            est = constrained_wls(r, w.exposures, weights, constraint).factor_returns
            out.append(float(((est - truth) ** 2).sum()))
    assert np.mean(errors_wls) < np.mean(errors_ols)
    assert sum(a < b for a, b in zip(errors_wls, errors_ols, strict=True)) >= 8


def test_empty_sector_gets_nan() -> None:
    w = make_world(n=300, t=3, seed=4)
    keep = w.sectors != SECTORS[3]
    x = w.exposures.loc[keep]
    constraint = sector_constraint(x, w.caps[keep], SECTORS)
    res = constrained_wls(w.returns.iloc[0][keep], x, np.sqrt(w.caps[keep]), constraint)
    assert np.isnan(res.factor_returns[SECTORS[3]])
    assert res.factor_returns.drop(SECTORS[3]).notna().all()
    assert abs(float(res.factor_returns.fillna(0.0) @ constraint)) < 1e-12
    assert res.rank == len(x.columns) - 2  # one dummy dropped, one constraint


def test_result_is_frozen_and_aligned() -> None:
    w = make_world(n=200, t=2, seed=5)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    res = constrained_wls(w.returns.iloc[0], w.exposures, np.sqrt(w.caps), constraint)
    assert isinstance(res, RegressionResult)
    assert list(res.factor_returns.index) == list(w.exposures.columns)
    assert list(res.residuals.index) == list(w.exposures.index)
    with pytest.raises(AttributeError):
        res.r2 = 0.0  # type: ignore[misc]


def test_validation() -> None:
    w = make_world(n=100, t=2, seed=6)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    r, x, weights = _cross_section(w, 0)
    with pytest.raises(ValueError, match="finite"):
        constrained_wls(r.where(r.index != r.index[0], np.nan), x, weights, constraint)
    with pytest.raises(ValueError, match="positive"):
        constrained_wls(r, x, weights * 0.0, constraint)
    with pytest.raises(ValueError, match="aligned"):
        constrained_wls(r.iloc[:-1], x, weights, constraint)
    with pytest.raises(ValueError, match="constraint"):
        constrained_wls(r, x, weights, constraint.iloc[:-1])
    with pytest.raises(DataError, match="names"):
        constrained_wls(r.iloc[:12], x.iloc[:12], weights.iloc[:12], constraint)
    dup = x.copy()
    dup["value"] = dup["quality"]
    with pytest.raises(DataError, match="rank"):
        constrained_wls(r, dup, weights, constraint)


def test_synthetic_recovery_correlation() -> None:
    w = make_world(n=600, t=500, seed=11)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    sw = np.sqrt(w.caps)
    est = pd.DataFrame(
        [
            constrained_wls(w.returns.iloc[i], w.exposures, sw, constraint).factor_returns
            for i in range(500)
        ],
        index=w.dates[:500],
    )
    corr = pd.Series({c: est[c].corr(w.factor_returns[c]) for c in est.columns})
    assert corr.min() > 0.95, corr.sort_values().head(3).to_dict()


def test_market_factor_tracks_the_cap_weighted_return() -> None:
    """With sqrt(cap) weights the market factor is close to, not equal to, the cap-weighted
    market return (styles have cap-weighted mean 0, sectors are constrained)."""
    w = make_world(n=600, t=250, seed=12)
    constraint = sector_constraint(w.exposures, w.caps, SECTORS)
    sw = np.sqrt(w.caps)
    market = np.array(
        [
            constrained_wls(w.returns.iloc[i], w.exposures, sw, constraint).factor_returns["market"]
            for i in range(250)
        ]
    )
    cap_weighted = (w.returns @ (w.caps / w.caps.sum())).to_numpy()
    assert np.corrcoef(market, cap_weighted)[0, 1] > 0.98
    capw = np.array(
        [
            constrained_wls(w.returns.iloc[i], w.exposures, w.caps, constraint).factor_returns[
                "market"
            ]
            for i in range(5)
        ]
    )
    np.testing.assert_allclose(capw, cap_weighted[:5], atol=1e-14)  # exact with cap weights
