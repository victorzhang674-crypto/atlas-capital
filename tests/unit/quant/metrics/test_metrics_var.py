"""VaR and CVaR (Stage 20 Task 5)."""

import itertools
import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from metrics_reference import Reference
from scipy import integrate, stats

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.metrics.var import (
    COVARIANCE_METHODS,
    METHODS,
    SERIES_METHODS,
    VaRResult,
    conditional_value_at_risk,
    cornish_fisher_cvar,
    cornish_fisher_quantile,
    cornish_fisher_var,
    normal_cvar,
    normal_var,
    portfolio_returns,
    portfolio_var,
    scale_to_horizon,
    simulate_portfolio_returns,
    value_at_risk,
    var_cvar,
)

LADDER = pd.Series(np.arange(-50, 50) / 1000.0, index=pd.bdate_range("2020-01-01", periods=100))
COV = pd.DataFrame(
    [[4e-4, 1e-4, 0.0], [1e-4, 2.25e-4, -5e-5], [0.0, -5e-5, 1e-4]],
    index=["A", "B", "C"],
    columns=["A", "B", "C"],
)
W = pd.Series({"A": 0.5, "B": -0.3, "C": 0.2})


def _sample(seed: int, n: int = 500) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(
        0.01 * rng.standard_t(5, size=n), index=pd.bdate_range("2019-01-01", periods=n)
    )


def test_method_lists() -> None:
    assert METHODS == ("historical", "normal", "cornish_fisher", "monte_carlo")
    assert SERIES_METHODS == ("historical", "normal", "cornish_fisher")
    assert COVARIANCE_METHODS == ("normal", "monte_carlo")


# --- historical ------------------------------------------------------------------------------


def test_historical_hand_example() -> None:
    res = var_cvar(LADDER, method="historical", var_confidence=0.95, cvar_confidence=0.95)
    assert isinstance(res, VaRResult)
    assert res.var == pytest.approx(0.04505, rel=1e-12)  # q(0.05) at position 4.95
    assert res.cvar == pytest.approx(0.048, rel=1e-12)  # mean of -0.050 .. -0.046
    assert (res.horizon, res.method, res.n_obs, res.adjustments) == (1, "historical", 100, ())
    assert value_at_risk(LADDER, confidence=0.95) == pytest.approx(
        -float(np.quantile(LADDER, 0.05)), rel=1e-12
    )


def test_defaults_are_the_limit_confidences() -> None:
    res = var_cvar(LADDER)
    assert (res.var_confidence, res.cvar_confidence, res.method) == (0.95, 0.975, "historical")
    assert value_at_risk(LADDER) == res.var
    assert conditional_value_at_risk(LADDER) == res.cvar
    cfg = AtlasConfig.model_validate(
        {"limits": {"var_confidence": 0.99, "cvar_confidence": 0.99},
         "quant": {"metrics": {"var_method": "normal"}}}
    )  # fmt: skip
    res99 = var_cvar(LADDER, config=cfg)
    assert (res99.var_confidence, res99.cvar_confidence, res99.method) == (0.99, 0.99, "normal")


def test_historical_matches_empyrical(reference: Reference) -> None:
    r, v = reference.returns, reference.values
    assert -value_at_risk(r, confidence=0.95) == pytest.approx(v["value_at_risk_05"], rel=1e-12)
    assert -value_at_risk(r, confidence=0.99) == pytest.approx(v["value_at_risk_01"], rel=1e-12)
    assert -conditional_value_at_risk(r, confidence=0.95) == pytest.approx(
        v["conditional_value_at_risk_05"], rel=1e-12
    )


def test_var_of_gains_only_is_negative() -> None:
    gains = LADDER + 0.1
    assert value_at_risk(gains) < 0 and conditional_value_at_risk(gains) < 0


# --- parametric ------------------------------------------------------------------------------


def test_normal_closed_form_matches_scipy() -> None:
    z95 = stats.norm.ppf(0.05)
    assert normal_var(0.0, 0.01, 0.95) == pytest.approx(-z95 * 0.01, rel=1e-12)
    assert normal_var(0.0, 1.0, 0.95) == pytest.approx(1.6448536269514722, rel=1e-12)
    assert normal_cvar(0.0, 1.0, 0.95) == pytest.approx(stats.norm.pdf(z95) / 0.05, rel=1e-12)
    assert normal_cvar(0.0, 1.0, 0.95) == pytest.approx(2.0627128075074, rel=1e-10)
    assert normal_var(0.001, 0.01, 0.99) == pytest.approx(-(0.001 + stats.norm.ppf(0.01) * 0.01))
    assert normal_cvar(0.001, 0.01, 0.975) == pytest.approx(
        -0.001 + 0.01 * stats.norm.pdf(stats.norm.ppf(0.025)) / 0.025
    )


def test_normal_on_a_series_zero_mean_and_sample_mean() -> None:
    x = _sample(1)
    sd = float(x.std(ddof=1))
    res = var_cvar(x, method="normal")
    assert res.var == pytest.approx(normal_var(0.0, sd, 0.95), rel=1e-12)
    assert res.cvar == pytest.approx(normal_cvar(0.0, sd, 0.975), rel=1e-12)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"zero_mean": False}}})
    res_mu = var_cvar(x, method="normal", config=cfg)
    assert res_mu.var == pytest.approx(normal_var(float(x.mean()), sd, 0.95), rel=1e-12)


def test_cornish_fisher_reduces_to_normal_without_higher_moments() -> None:
    for conf in (0.9, 0.95, 0.99):
        assert cornish_fisher_var(0.001, 0.02, 0.0, 0.0, conf) == pytest.approx(
            normal_var(0.001, 0.02, conf), rel=1e-12
        )
        cvar, floored = cornish_fisher_cvar(0.001, 0.02, 0.0, 0.0, conf)
        assert cvar == pytest.approx(normal_cvar(0.001, 0.02, conf), rel=1e-12) and not floored


def test_cornish_fisher_quantile_formula() -> None:
    z, s, k = -1.6448536269514722, -0.6, 3.0
    want = z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36
    assert cornish_fisher_quantile(z, s, k) == pytest.approx(want, rel=1e-12)
    assert cornish_fisher_var(0.0, 1.0, s, k, 0.95) == pytest.approx(-want, rel=1e-12)


@pytest.mark.parametrize(("skew", "kurt"), [(-0.5, 2.0), (0.3, 1.0), (-1.0, 6.0), (0.0, 4.0)])
@pytest.mark.parametrize("conf", [0.95, 0.975, 0.99])
def test_cornish_fisher_cvar_equals_numerical_integral(
    skew: float, kurt: float, conf: float
) -> None:
    p = 1.0 - conf
    c = stats.norm.ppf(p)
    value, _ = integrate.quad(
        lambda z: cornish_fisher_quantile(z, skew, kurt) * stats.norm.pdf(z), -np.inf, c
    )
    want = -(0.0005 + 0.01 * value / p)
    got, floored = cornish_fisher_cvar(0.0005, 0.01, skew, kurt, conf)
    assert not floored
    assert got == pytest.approx(want, rel=1e-8)


def test_cornish_fisher_floor_is_applied_and_recorded() -> None:
    var = cornish_fisher_var(0.0, 1.0, -3.0, 0.0, 0.975)
    cvar, floored = cornish_fisher_cvar(0.0, 1.0, -3.0, 0.0, 0.975)
    assert floored and cvar == var
    x = pd.Series([-0.01] * 95 + [0.2] * 5)  # skew 4.1, excess kurtosis 15: non-monotone CF
    assert float(stats.skew(x)) == pytest.approx(4.13, abs=0.01)
    res = var_cvar(x, method="cornish_fisher", var_confidence=0.975, cvar_confidence=0.975)
    assert res.cvar == res.var
    assert res.adjustments == ("cornish-fisher CVaR floored at the VaR (non-monotone expansion)",)


def test_cornish_fisher_on_a_series_uses_sample_moments() -> None:
    x = _sample(2)
    sd = float(x.std(ddof=1))
    s, k = float(stats.skew(x)), float(stats.kurtosis(x))
    res = var_cvar(x, method="cornish_fisher")
    assert res.var == pytest.approx(cornish_fisher_var(0.0, sd, s, k, 0.95), rel=1e-12)
    assert res.cvar == pytest.approx(cornish_fisher_cvar(0.0, sd, s, k, 0.975)[0], rel=1e-12)


# --- horizon ---------------------------------------------------------------------------------


def test_horizon_scaling() -> None:
    x = _sample(3)
    one = var_cvar(x)
    four = var_cvar(x, horizon=4)
    assert four.var == pytest.approx(2 * one.var) and four.cvar == pytest.approx(2 * one.cvar)
    assert four.horizon == 4 and four.adjustments == ("sqrt-of-time scaling to 4 sessions",)
    assert scale_to_horizon(0.01, 9) == pytest.approx(0.03)
    n1, n4 = var_cvar(x, method="normal"), var_cvar(x, method="normal", horizon=4)
    assert n4.var == pytest.approx(2 * n1.var) and n4.cvar == pytest.approx(2 * n1.cvar)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"zero_mean": False}}})
    mu, sd = float(x.mean()), float(x.std(ddof=1))
    assert var_cvar(x, method="normal", horizon=4, config=cfg).var == pytest.approx(
        normal_var(4 * mu, 2 * sd, 0.95), rel=1e-12
    )
    s, k = float(stats.skew(x)), float(stats.kurtosis(x))
    assert var_cvar(x, method="cornish_fisher", horizon=4).var == pytest.approx(
        cornish_fisher_var(0.0, 2 * sd, s / 2, k / 4, 0.95), rel=1e-12
    )
    with pytest.raises(ValueError, match="horizon"):
        var_cvar(x, horizon=0)


def test_overlapping_horizon() -> None:
    x = _sample(4)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"horizon_method": "overlapping"}}})
    res = var_cvar(x, horizon=5, config=cfg)
    agg = ((1 + x).rolling(5).apply(np.prod, raw=True) - 1).dropna().to_numpy()
    assert res.var == pytest.approx(-float(np.quantile(agg, 0.05)), rel=1e-12)
    assert res.cvar == pytest.approx(-float(agg[agg <= np.quantile(agg, 0.025)].mean()), rel=1e-12)
    assert res.adjustments == ("overlapping 5-session returns",)
    pnl = x * 1e6
    res_pnl = var_cvar(pnl, horizon=5, pnl=True, config=cfg)
    sums = pnl.rolling(5).sum().dropna().to_numpy()
    assert res_pnl.var == pytest.approx(-float(np.quantile(sums, 0.05)), rel=1e-12)
    with pytest.raises(DataError, match="overlapping"):
        var_cvar(x.iloc[:70], horizon=10, config=cfg)


# --- P&L, data sufficiency, validation -------------------------------------------------------


def test_pnl_series_in_currency() -> None:
    pnl = LADDER * 1_000_000  # USD, losses far below -1
    res = var_cvar(pnl, pnl=True, var_confidence=0.95, cvar_confidence=0.95)
    assert res.var == pytest.approx(45_050.0) and res.cvar == pytest.approx(48_000.0)
    with pytest.raises(ValueError, match="below -1"):
        var_cvar(pnl)


def test_not_enough_observations() -> None:
    with pytest.raises(DataError, match="at least 63"):
        var_cvar(LADDER.iloc[:62])
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"var_min_obs": 10}}})
    assert var_cvar(LADDER.iloc[:10], config=cfg).n_obs == 10
    with_nan = LADDER.copy()
    with_nan.iloc[:40] = np.nan
    with pytest.raises(DataError, match="got 60"):
        var_cvar(with_nan)


def test_validation() -> None:
    with pytest.raises(ValueError, match="method"):
        var_cvar(LADDER, method="monte_carlo")
    for bad in (0.5, 1.0, 0.4, True):
        with pytest.raises(ValueError, match="confidence"):
            var_cvar(LADDER, var_confidence=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="confidence"):
        normal_var(0.0, 1.0, 1.2)


# --- portfolios ------------------------------------------------------------------------------


def _frame(seed: int = 5, n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = rng.multivariate_normal(np.zeros(3), COV.to_numpy(), size=n)
    return pd.DataFrame(data, index=pd.bdate_range("2021-01-01", periods=n), columns=COV.columns)


def test_portfolio_returns_historical_simulation() -> None:
    frame = _frame()
    frame.iloc[3, 0] = np.nan  # A missing on session 3: dropped (A is held)
    frame.iloc[7, 2] = np.nan  # C missing on session 7: dropped (C is held)
    frame["D"] = 0.5  # not held: ignored
    out = portfolio_returns(W, frame)
    assert len(out) == 298 and frame.index[3] not in out.index
    want = (frame[["A", "B", "C"]].dropna() * W).sum(axis=1)
    np.testing.assert_allclose(out.to_numpy(), want.to_numpy(), rtol=1e-12)
    out2 = portfolio_returns(pd.Series({"A": 0.5, "B": -0.3, "C": 0.2, "E": 0.0}), frame)
    assert len(out2) == 298  # a zero weight on an unknown name is fine
    with pytest.raises(DataError, match="no returns for held names"):
        portfolio_returns(pd.Series({"A": 0.5, "E": 0.1}), frame)
    with pytest.raises(ValueError, match="DataFrame"):
        portfolio_returns(W, frame["A"])  # type: ignore[arg-type]


def test_portfolio_var_from_returns_equals_series_var() -> None:
    frame = _frame()
    series = portfolio_returns(W, frame)
    for method in SERIES_METHODS:
        got = portfolio_var(W, returns=frame, method=method)
        assert got == var_cvar(series, method=method)


def test_portfolio_var_from_covariance_normal() -> None:
    sigma = math.sqrt(float(W.to_numpy() @ COV.to_numpy() @ W.to_numpy()))
    res = portfolio_var(W, cov=COV, method="normal")
    assert res.var == pytest.approx(normal_var(0.0, sigma, 0.95), rel=1e-12)
    assert res.cvar == pytest.approx(normal_cvar(0.0, sigma, 0.975), rel=1e-12)
    mean = pd.Series({"A": 0.001, "B": 0.0, "C": -0.0005})
    res_mu = portfolio_var(W, cov=COV, method="normal", mean=mean, horizon=4)
    mu = float(mean @ W)
    assert res_mu.var == pytest.approx(normal_var(4 * mu, 2 * sigma, 0.95), rel=1e-12)
    with pytest.raises(ValueError, match="method"):
        portfolio_var(W, cov=COV, method="historical")
    with pytest.raises(ValueError, match="exactly one of returns"):
        portfolio_var(W)
    with pytest.raises(ValueError, match="exactly one of returns"):
        portfolio_var(W, returns=_frame(), cov=COV)


def test_monte_carlo_converges_to_normal_and_is_reproducible() -> None:
    normal = portfolio_var(W, cov=COV, method="normal")
    mc = portfolio_var(W, cov=COV, method="monte_carlo", seed=7)
    assert mc.method == "monte_carlo" and mc.n_obs == 20_000
    assert mc.var == pytest.approx(normal.var, rel=0.02)
    assert mc.cvar == pytest.approx(normal.cvar, rel=0.03)
    assert portfolio_var(W, cov=COV, seed=7) == mc  # default method with a cov
    rng_res = portfolio_var(W, cov=COV, rng=np.random.default_rng(7))
    assert rng_res == mc
    assert portfolio_var(W, cov=COV, seed=8) != mc
    with pytest.raises(ValueError, match="seed or rng"):
        portfolio_var(W, cov=COV)
    with pytest.raises(ValueError, match="seed or rng"):
        portfolio_var(W, cov=COV, seed=1, rng=np.random.default_rng(1))


def test_monte_carlo_mean_horizon_and_rank_deficient_cov() -> None:
    mean = pd.Series({"A": 0.002, "B": 0.0, "C": 0.0})
    sims = simulate_portfolio_returns(W, COV, n_sims=50_000, mean=mean, horizon=9, seed=3)
    sigma = math.sqrt(float(W.to_numpy() @ COV.to_numpy() @ W.to_numpy()))
    assert float(sims.mean()) == pytest.approx(9 * 0.001, abs=4 * 3 * sigma / math.sqrt(50_000))
    assert float(sims.std()) == pytest.approx(3 * sigma, rel=0.02)
    v = np.array([1.0, 2.0])
    singular = np.outer(v, v) * 1e-4  # rank 1
    out = simulate_portfolio_returns(np.array([1.0, -0.5]), singular, n_sims=1000, seed=1)
    assert np.allclose(out, 0.0)  # w is orthogonal to the only risky direction
    with pytest.raises(ValueError, match="positive semi-definite"):
        portfolio_var(W, cov=COV - np.diag([1e-3, 0, 0]), seed=1)
    with pytest.raises(ValueError, match="n_sims"):
        simulate_portfolio_returns(W, COV, n_sims=0, seed=1)


def test_covariance_methods_reject_names_without_estimates() -> None:
    with pytest.raises(DataError, match="without an estimate"):
        portfolio_var(pd.Series({"A": 0.5, "Z": 0.1}), cov=COV, method="normal")
    assert portfolio_var(pd.Series({"A": 0.5, "Z": 0.0}), cov=COV, method="normal").var > 0


# --- properties ------------------------------------------------------------------------------

sample_lists = st.lists(
    st.floats(min_value=-0.4, max_value=0.4, allow_nan=False), min_size=63, max_size=150
)


@settings(max_examples=60, deadline=None)
@given(sample_lists, st.floats(min_value=0.8, max_value=0.995), st.sampled_from(SERIES_METHODS))
def test_cvar_is_never_below_var(values: list[float], conf: float, method: str) -> None:
    res = var_cvar(pd.Series(values), method=method, var_confidence=conf, cvar_confidence=conf)
    assert res.cvar >= res.var - 1e-12


@settings(max_examples=60, deadline=None)
@given(sample_lists, st.sampled_from(("historical", "normal")))
def test_var_increases_with_confidence(values: list[float], method: str) -> None:
    x = pd.Series(values)
    levels = [0.9, 0.95, 0.975, 0.99]
    vars_ = [value_at_risk(x, method=method, confidence=c) for c in levels]
    assert all(b >= a - 1e-12 for a, b in itertools.pairwise(vars_))


@settings(max_examples=60, deadline=None)
@given(sample_lists, st.floats(min_value=0.1, max_value=2.0), st.sampled_from(SERIES_METHODS))
def test_var_is_positively_homogeneous(values: list[float], c: float, method: str) -> None:
    x = pd.Series(values)
    a, b = var_cvar(x, method=method), var_cvar(x * c, method=method)
    assert b.var == pytest.approx(c * a.var, rel=1e-6, abs=1e-12)
    assert b.cvar == pytest.approx(c * a.cvar, rel=1e-6, abs=1e-12)


@settings(max_examples=30, deadline=None)
@given(st.integers(min_value=0, max_value=10_000), st.floats(0.9, 0.99))
def test_monte_carlo_cvar_is_never_below_var(seed: int, conf: float) -> None:
    res = portfolio_var(
        W, cov=COV, n_sims=2_000, seed=seed, var_confidence=conf, cvar_confidence=conf
    )
    assert res.cvar >= res.var


def test_audit_portfolio_returns_rejects_ambiguous_frames() -> None:  # audit finding 1
    frame = _frame()
    dup = pd.concat([frame, frame[["A"]]], axis=1)
    with pytest.raises(ValueError, match="duplicate"):
        portfolio_returns(W, dup)
    with pytest.raises(ValueError, match="sorted"):
        portfolio_returns(W, frame.iloc[::-1])
    with pytest.raises(ValueError, match="duplicate"):
        portfolio_returns(W, pd.concat([frame.iloc[:5], frame.iloc[4:10]]))


@settings(max_examples=60, deadline=None)
@given(st.lists(st.floats(-0.3, 0.3), min_size=63, max_size=120), st.floats(0.51, 0.999))
def test_audit_historical_cvar_tail_is_never_empty(values: list[float], conf: float) -> None:
    # audit finding 3: the tail always holds the observations at or below the VaR quantile
    x = np.array(values)
    res = var_cvar(x, var_confidence=conf, cvar_confidence=conf)
    q = np.quantile(x, 1 - conf)
    assert math.isfinite(res.cvar)
    assert res.cvar == pytest.approx(-float(x[x <= q].mean()) if (x <= q).any() else -x.min())
