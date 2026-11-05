"""Gaps found by the generated mutation run (mutmut 3.8.1, Stage 20 post-DONE completion):
arguments that were accepted but never shown to be used, boundary values, type checks, and
result metadata / names that no test asserted."""

import math

import numpy as np
import pandas as pd
import pytest
from metrics_reference import Reference

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant import metrics as m
from atlas.quant.metrics._common import (
    annual_to_daily,
    as_covariance,
    as_series,
    periods_per_year,
    risk_free_daily,
    weights_on,
)
from tiny_universe import TinyUniverse

DAYS = pd.bdate_range("2024-01-01", periods=10)
CFG12 = AtlasConfig.model_validate({"quant": {"features": {"sessions_per_year": 12}}})
CFG_RF = AtlasConfig.model_validate({"quant": {"metrics": {"risk_free_annual": 0.04}}})


# --- 1. periods / config / risk-free are used, not just accepted ------------------------------

PERIOD_FUNCS = [
    m.annualised_return, m.annualised_mean, m.annualised_vol, m.sharpe_ratio, m.downside_risk,
    m.sortino_ratio, m.calmar_ratio,
]  # fmt: skip
RELATIVE_FUNCS = [
    m.alpha, m.tracking_error, m.information_ratio, m.active_return, m.up_capture,
    m.down_capture, m.up_down_capture,
]  # fmt: skip


@pytest.mark.parametrize("fn", PERIOD_FUNCS, ids=lambda f: f.__name__)
def test_periods_and_config_are_used(fn, reference: Reference) -> None:  # type: ignore[no-untyped-def]
    r = reference.returns.iloc[:200]
    via_periods, via_config = fn(r, periods=12), fn(r, config=CFG12)
    assert via_periods == pytest.approx(via_config, rel=1e-12)
    assert via_periods != pytest.approx(fn(r), rel=1e-6)


def test_omega_periods_and_config_are_used(reference: Reference) -> None:
    r = reference.returns.iloc[:200]
    a = m.omega_ratio(r, periods=12, threshold_annual=0.2)
    assert a == pytest.approx(m.omega_ratio(r, config=CFG12, threshold_annual=0.2), rel=1e-12)
    assert a != pytest.approx(m.omega_ratio(r, threshold_annual=0.2), rel=1e-6)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"omega_threshold_annual": 0.2}}})
    assert m.omega_ratio(r, config=cfg) == pytest.approx(
        m.omega_ratio(r, threshold_annual=0.2), rel=1e-12
    )


@pytest.mark.parametrize("fn", RELATIVE_FUNCS, ids=lambda f: f.__name__)
def test_relative_periods_and_config_are_used(fn, reference: Reference) -> None:  # type: ignore[no-untyped-def]
    r, b = reference.returns.iloc[:200], reference.benchmark.iloc[:200]
    via_periods, via_config = fn(r, b, periods=12), fn(r, b, config=CFG12)
    assert via_periods == pytest.approx(via_config, rel=1e-12)
    assert via_periods != pytest.approx(fn(r, b), rel=1e-6)


def test_default_risk_free_config_is_used(reference: Reference) -> None:
    r, b = reference.returns.iloc[:200], reference.benchmark.iloc[:200]
    rf = annual_to_daily(0.04, 252)
    rf12 = annual_to_daily(0.04, 12)
    assert m.sharpe_ratio(r, config=CFG_RF) == pytest.approx(m.sharpe_ratio(r, risk_free=rf))
    assert m.alpha(r, b, config=CFG_RF) == pytest.approx(m.alpha(r, b, risk_free=rf))
    assert m.alpha(r, b, config=CFG_RF) != pytest.approx(m.alpha(r, b), rel=1e-6)
    cfg = AtlasConfig.model_validate(
        {"quant": {"features": {"sessions_per_year": 12}, "metrics": {"risk_free_annual": 0.04}}}
    )
    assert m.alpha(r, b, config=cfg) == pytest.approx(
        m.alpha(r, b, risk_free=rf12, periods=12), rel=1e-12
    )
    pd.testing.assert_series_equal(
        m.rolling_sharpe(r, 20, config=CFG_RF), m.rolling_sharpe(r, 20, risk_free=rf)
    )
    pd.testing.assert_series_equal(
        m.rolling_beta(r, b, 20, risk_free=pd.Series(np.linspace(0, 0.004, 200), index=r.index)),
        m.rolling_beta(
            r, b, 20, risk_free=pd.Series(np.linspace(0, 0.004, 200), index=r.index), periods=12
        ),
    )


def test_rolling_and_summaries_use_periods(reference: Reference) -> None:
    r, b = reference.returns.iloc[:200], reference.benchmark.iloc[:200]
    pd.testing.assert_series_equal(
        m.rolling_vol(r, 20, periods=12), m.rolling_vol(r, 20, config=CFG12)
    )
    pd.testing.assert_series_equal(
        m.rolling_sharpe(r, 20, periods=12), m.rolling_sharpe(r, 20, config=CFG12)
    )
    assert not np.allclose(m.rolling_vol(r, 20, periods=12).dropna(), m.rolling_vol(r, 20).dropna())
    s12 = m.performance_summary(r, config=CFG12)
    assert s12["annualised_return"] == m.annualised_return(r, periods=12)
    assert s12["sortino"] == m.sortino_ratio(r, periods=12)
    assert s12["calmar"] == m.calmar_ratio(r, periods=12)
    assert s12["omega"] == m.omega_ratio(r, periods=12)
    rel = m.relative_summary(r, b, periods=12)
    rel_cfg = m.relative_summary(r, b, config=CFG12)
    for key, fn in [
        ("beta", m.beta), ("alpha", m.alpha), ("tracking_error", m.tracking_error),
        ("information_ratio", m.information_ratio), ("active_return", m.active_return),
        ("up_capture", m.up_capture), ("down_capture", m.down_capture),
        ("up_down_capture", m.up_down_capture),
    ]:  # fmt: skip
        assert rel[key] == pytest.approx(fn(r, b, periods=12), rel=1e-12), key
        assert rel_cfg[key] == pytest.approx(rel[key], rel=1e-12), key
    rel_rf = m.relative_summary(r, b, config=CFG_RF)
    assert rel_rf["alpha"] == pytest.approx(m.alpha(r, b, config=CFG_RF), rel=1e-12)


def test_var_config_reaches_every_entry_point(reference: Reference) -> None:
    r = reference.returns
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"var_method": "normal"}}})
    assert m.value_at_risk(r, config=cfg) == m.var_cvar(r, method="normal").var
    assert m.conditional_value_at_risk(r, config=cfg) == m.var_cvar(r, method="normal").cvar
    frame = pd.DataFrame({"A": r, "B": reference.benchmark})
    w = pd.Series({"A": 0.6, "B": -0.2})
    assert m.portfolio_var(w, returns=frame, config=cfg).method == "normal"
    with pytest.raises(ValueError, match="below -1"):
        m.value_at_risk(pd.Series([-2.0] + [0.0] * 99))  # default pnl=False
    with pytest.raises(ValueError, match="below -1"):
        m.conditional_value_at_risk(pd.Series([-2.0] + [0.0] * 99))


def test_liquidity_from_view_uses_the_view_config(tiny_universe: TinyUniverse) -> None:
    cfg = AtlasConfig.model_validate({"limits": {"liquidation_adv_frac": 0.05}})
    view = tiny_universe.view(cfg)
    pos = pd.Series({"AAA": 1_000_000.0})
    adv = view.adv(["AAA"])["adv_usd"]
    prof = m.liquidity_from_view(view, pos)
    assert prof.participation == 0.05
    assert prof.days["AAA"] == pytest.approx(1_000_000.0 / (0.05 * adv["AAA"]))


# --- 2. boundaries and type checks -------------------------------------------------------------


def test_boundaries_of_validators() -> None:
    assert periods_per_year(1) == 1
    assert risk_free_daily(-0.99, DAYS[:2], 252).tolist() == [-0.99, -0.99]
    with pytest.raises(ValueError, match="risk_free"):
        risk_free_daily(-1.0, DAYS[:2], 252)
    r = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02], index=DAYS[:5])
    with pytest.raises(ValueError, match="quantile"):
        m.tail_ratio(r, quantile=0.0)
    for bad in (True, math.nan):
        with pytest.raises(ValueError, match="required_return"):
            m.sortino_ratio(r, required_return=bad)  # type: ignore[arg-type]
    assert m.performance_summary(r.iloc[:2])["n_sessions"] == 2
    x = pd.Series(np.linspace(-0.05, 0.05, 100))
    for bad in (True, "0.95"):
        with pytest.raises(ValueError, match="confidence"):
            m.var_cvar(x, var_confidence=bad)  # type: ignore[arg-type]
    for bad in (True, 1.5):
        with pytest.raises(ValueError, match="horizon"):
            m.var_cvar(x, horizon=bad)  # type: ignore[arg-type]
    cov = np.eye(2) * 1e-4
    for bad in (True, 1.5, 0):
        with pytest.raises(ValueError, match="n_sims"):
            m.simulate_portfolio_returns(np.ones(2), cov, n_sims=bad, seed=1)  # type: ignore[arg-type]
    assert len(m.simulate_portfolio_returns(np.ones(2), cov, n_sims=1, seed=1)) == 1


def test_liquidity_boundaries() -> None:
    assert m.days_to_liquidate(pd.Series({"A": 10.0}), pd.Series({"A": 10.0}), participation=1.0)[
        "A"
    ] == pytest.approx(1.0)
    tiny = m.liquidity_profile(pd.Series({"A": 1.0}), pd.Series({"A": 5.0}))  # capacity 0.5 USD
    assert tiny.days["A"] == pytest.approx(2.0) and tiny.no_adv == ()
    sub_unit = m.liquidity_profile(
        pd.Series({"A": 0.3, "B": -0.2}), pd.Series({"A": 3.0, "B": 0.5})
    )
    # capacity 0.3 and 0.05 per session; gross 0.5 (< 1 currency unit)
    assert sub_unit.days.tolist() == pytest.approx([1.0, 4.0])
    assert dict(sub_unit.names_liquid) == pytest.approx({1: 0.5, 3: 0.5, 5: 1.0})
    assert dict(sub_unit.gross_liquid) == pytest.approx({1: 0.35 / 0.5, 3: 0.45 / 0.5, 5: 1.0})


def test_covariance_tolerances_are_relative() -> None:
    small = np.array([[1e-4, 2e-5], [2e-5, 1e-4]])
    asym = small.copy()
    asym[0, 1] += 1e-11  # 1e-7 of the largest entry: far above rounding, must be rejected
    with pytest.raises(ValueError, match="symmetric"):
        as_covariance(asym)
    ok = small.copy()
    ok[0, 1] += 1e-14  # 1e-10 of the largest entry: rounding, accepted and symmetrised
    matrix, _ = as_covariance(ok)
    assert np.array_equal(matrix, matrix.T)
    indefinite = np.diag([1e-4, -1e-12])  # -1e-8 of the largest eigenvalue
    with pytest.raises(ValueError, match="positive semi-definite"):
        as_covariance(indefinite)
    assert as_covariance(np.zeros((2, 2)))[0].tolist() == [[0.0, 0.0], [0.0, 0.0]]
    tiny_negative = np.diag([1e-4, -1e-16])  # within tolerance: allowed, simulated without NaN
    sims = m.simulate_portfolio_returns(np.array([1.0, 1.0]), tiny_negative, n_sims=100, seed=1)
    assert np.isfinite(sims).all()


def test_error_messages_name_the_culprit() -> None:
    with pytest.raises(DataError, match="ZZZ"):
        weights_on(["A"], pd.Series({"ZZZ": 0.1}))
    with pytest.raises(DataError, match="QQQ"):
        m.portfolio_returns(pd.Series({"QQQ": 0.1}), pd.DataFrame({"A": [0.0]}))
    with pytest.raises(DataError, match="2024-01-01"):
        risk_free_daily(pd.Series([0.0], index=DAYS[1:2]), DAYS[:2], 252)


# --- 3. result metadata, names and indexes -----------------------------------------------------


def test_var_result_metadata_on_every_path(reference: Reference) -> None:
    r = reference.returns
    for method in ("normal", "cornish_fisher"):
        res = m.var_cvar(r, method=method, var_confidence=0.9, cvar_confidence=0.99)
        assert (res.var_confidence, res.cvar_confidence, res.method, res.n_obs) == (
            0.9, 0.99, method, 504
        )  # fmt: skip
    w = pd.Series({"A": 0.5, "B": 0.5})
    cov = pd.DataFrame(np.eye(2) * 1e-4, index=["A", "B"], columns=["A", "B"])
    n = m.portfolio_var(w, cov=cov, method="normal", var_confidence=0.9, cvar_confidence=0.99)
    assert (n.var_confidence, n.cvar_confidence, n.method, n.n_obs, n.adjustments) == (
        0.9, 0.99, "normal", 0, ()
    )  # fmt: skip
    mc = m.portfolio_var(w, cov=cov, n_sims=1234, seed=1, var_confidence=0.9, cvar_confidence=0.99)
    assert (mc.var_confidence, mc.cvar_confidence, mc.method, mc.n_obs, mc.adjustments) == (
        0.9, 0.99, "monte_carlo", 1234, ()
    )  # fmt: skip
    mean = pd.Series({"A": 0.001, "B": 0.0, "EXTRA": 5.0})  # unknown names ignored in a mean
    assert m.portfolio_var(w, cov=cov, method="normal", mean=mean).var == pytest.approx(
        m.normal_var(0.0005, math.sqrt(0.5e-4), 0.95)
    )
    sims = m.simulate_portfolio_returns(w, cov, n_sims=500, mean=mean, seed=2)
    assert np.allclose(
        sims, m.simulate_portfolio_returns(w, cov, n_sims=500, mean=mean, seed=2, horizon=1)
    )


def test_horizon_boundaries() -> None:
    x = pd.Series(np.random.default_rng(1).normal(0, 0.01, 200))
    two = m.var_cvar(x, horizon=2)
    assert two.var == pytest.approx(math.sqrt(2) * m.var_cvar(x).var)
    assert two.adjustments == ("sqrt-of-time scaling to 2 sessions",)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"horizon_method": "overlapping"}}})
    assert m.var_cvar(x, config=cfg).adjustments == ()  # h = 1 needs no aggregation
    exact = AtlasConfig.model_validate(
        {"quant": {"metrics": {"horizon_method": "overlapping", "var_min_obs": 63}}}
    )
    assert m.var_cvar(x.iloc[:67], horizon=5, config=exact).n_obs == 63  # overlapping sums used
    with pytest.raises(DataError, match="got 62"):
        m.var_cvar(x.iloc[:66], horizon=5, config=exact)


def test_names_and_indexes_of_series_results() -> None:
    r = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02], index=DAYS[:5])
    assert as_series(r).name == "returns"
    assert m.wealth_index(r).name == "wealth" and list(m.wealth_index(r).index) == list(r.index)
    assert m.drawdown_series(r).name == "drawdown"
    assert m.rolling_metric(r, m.hit_rate, 2).name == "rolling"
    frame = pd.DataFrame({"A": r.to_numpy()}, index=DAYS[:5])
    pr = m.portfolio_returns(pd.Series({"A": 1.0}), frame)
    assert pr.name == "portfolio_return" and list(pr.index) == list(DAYS[:5])
    cov = pd.DataFrame(np.eye(2) * 1e-4, index=["X", "Y"], columns=["X", "Y"])
    rc = m.risk_contributions(pd.Series({"X": 0.5, "Y": 0.5}), cov)
    assert (rc.marginal.name, rc.component.name, rc.percent.name) == (
        "marginal", "component", "percent"
    )  # fmt: skip
    zero = m.risk_contributions(pd.Series({"X": 0.0}), cov)
    assert zero.percent.dtype == np.float64 and zero.percent.isna().all()


def test_no_negative_zero_in_reported_numbers() -> None:
    longs = m.exposure_summary(pd.Series({"A": 0.1}))
    assert math.copysign(1.0, longs.short) == 1.0  # 0.0, not -0.0
    assert math.copysign(1.0, m.max_drawdown(pd.Series([0.01, 0.02]))) == 1.0
    assert math.copysign(1.0, m.rolling_max_drawdown(pd.Series([0.01, 0.02]), 2).iloc[-1]) == 1.0
    one_letter = m.group_net(pd.Series({"A": 0.1, "B": 0.2}), {"A": "X", "B": "Y"})
    assert one_letter.to_dict() == pytest.approx({"X": 0.1, "Y": 0.2})


def test_rolling_beta_on_a_flat_benchmark_window_is_nan() -> None:
    days = pd.bdate_range("2021-01-01", periods=30)
    b = pd.Series([0.001] * 15 + list(np.random.default_rng(3).normal(0, 0.01, 15)), index=days)
    r = pd.Series(np.random.default_rng(4).normal(0, 0.01, 30), index=days)
    out = m.rolling_beta(r, b, 10)
    assert out.iloc[9:15].isna().all() and out.iloc[-1] == out.iloc[-1]
    corr = m.rolling_correlation(r, b, 10)
    assert corr.iloc[9:15].isna().all() and (corr.dropna().abs() <= 1.0).all()


# --- second mutmut pass: compare against independently computed values ------------------------


def _cagr(x: pd.Series, p: int) -> float:
    return float(np.prod(1 + x.to_numpy()) ** (p / len(x)) - 1)


def test_inner_periods_reach_every_helper(reference: Reference) -> None:
    r, b = reference.returns.iloc[:200], reference.benchmark.iloc[:200]
    up, down = b > 0, b < 0
    assert m.up_capture(r, b, periods=12) == pytest.approx(
        _cagr(r[up], 12) / _cagr(b[up], 12), rel=1e-12
    )
    assert m.down_capture(r, b, periods=12) == pytest.approx(
        _cagr(r[down], 12) / _cagr(b[down], 12), rel=1e-12
    )
    assert m.active_return(r, b, periods=12) == pytest.approx(
        _cagr(r, 12) - _cagr(b, 12), rel=1e-12
    )
    dr = math.sqrt(float(np.mean(np.minimum(r.to_numpy(), 0) ** 2))) * math.sqrt(12)
    assert m.sortino_ratio(r, periods=12) == pytest.approx(r.mean() * 12 / dr, rel=1e-12)
    rc = m.risk_contributions(np.array([1.0]), np.array([[1e-4]]))
    assert rc.annual_vol(12) == pytest.approx(0.01 * math.sqrt(12))


def test_performance_summary_passes_config_to_every_metric(reference: Reference) -> None:
    r = reference.returns.iloc[:200]
    cfg = AtlasConfig.model_validate(
        {"quant": {"features": {"sessions_per_year": 12},
                   "metrics": {"risk_free_annual": 0.04, "tail_quantile": 0.2,
                               "omega_threshold_annual": 0.3}}}
    )  # fmt: skip
    s = m.performance_summary(r, config=cfg)
    assert s["annualised_mean"] == pytest.approx(float(r.mean()) * 12)
    assert s["sharpe"] == pytest.approx(
        m.sharpe_ratio(r, risk_free=annual_to_daily(0.04, 12), periods=12), rel=1e-12
    )
    assert s["omega"] == pytest.approx(
        m.omega_ratio(r, threshold_annual=0.3, periods=12), rel=1e-12
    )
    assert s["tail_ratio"] == pytest.approx(m.tail_ratio(r, quantile=0.2), rel=1e-12)


def test_rolling_windows_default_from_config(reference: Reference) -> None:
    r, b = reference.returns.iloc[:100], reference.benchmark.iloc[:100]
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"rolling_window": 10}}})
    for out in (
        m.rolling_sharpe(r, config=cfg), m.rolling_max_drawdown(r, config=cfg),
        m.rolling_metric(r, m.hit_rate, config=cfg), m.rolling_beta(r, b, config=cfg),
        m.rolling_correlation(r, b, config=cfg), m.rolling_vol(r, config=cfg),
    ):  # fmt: skip
        assert out.iloc[:9].isna().all() and out.iloc[9:].notna().all()
    for out in (
        m.rolling_vol(r, 20), m.rolling_max_drawdown(r, 20), m.rolling_sharpe(r, 20),
        m.rolling_metric(r, m.hit_rate, 20), m.rolling_beta(r, b, 20),
        m.rolling_correlation(r, b, 20),
    ):  # fmt: skip
        assert out.iloc[:19].isna().all() and out.iloc[19:].notna().all()


def test_var_wrappers_pass_every_argument(reference: Reference) -> None:
    r = reference.returns
    full = m.var_cvar(r, method="normal", var_confidence=0.9, cvar_confidence=0.9, horizon=4)
    assert m.value_at_risk(r, method="normal", confidence=0.9, horizon=4) == full.var
    assert m.conditional_value_at_risk(r, method="normal", confidence=0.9, horizon=4) == full.cvar
    pnl = r * 1e6 - 3e6  # currency, far below -1
    assert m.value_at_risk(pnl, pnl=True) == m.var_cvar(pnl, pnl=True).var
    assert m.conditional_value_at_risk(pnl, pnl=True) == m.var_cvar(pnl, pnl=True).cvar
    for method in m.SERIES_METHODS:
        assert m.var_cvar(r, method=method, horizon=3).horizon == 3


def test_portfolio_var_passes_every_argument(reference: Reference) -> None:
    frame = pd.DataFrame({"A": reference.returns, "B": reference.benchmark})
    w = pd.Series({"A": 0.6, "B": -0.2})
    got = m.portfolio_var(w, returns=frame, var_confidence=0.9, cvar_confidence=0.99, horizon=4)
    want = m.var_cvar(
        m.portfolio_returns(w, frame), var_confidence=0.9, cvar_confidence=0.99, horizon=4
    )
    assert got == want
    cov = pd.DataFrame(np.eye(2) * 1e-4, index=["A", "B"], columns=["A", "B"])
    assert m.portfolio_var(w, cov=cov, method="normal", horizon=4).horizon == 4
    mean = pd.Series({"A": 0.01, "B": 0.0})
    base = m.portfolio_var(w, cov=cov, seed=5, horizon=4)
    shifted = m.portfolio_var(w, cov=cov, seed=5, horizon=4, mean=mean)
    assert base.horizon == 4
    assert shifted.var == pytest.approx(base.var - 4 * 0.006, rel=1e-9)  # mean shifts losses
    one = m.portfolio_var(w, cov=cov, seed=5)
    assert base.var == pytest.approx(2 * one.var, rel=1e-9)  # same draws, sqrt(4) scale


def test_relative_summary_passes_the_risk_free_rate(reference: Reference) -> None:
    r, b = reference.returns.iloc[:200], reference.benchmark.iloc[:200]
    rf = pd.Series(np.linspace(0.0, 0.004, 200), index=r.index)
    s = m.relative_summary(r, b, risk_free=rf)
    assert s["beta"] == pytest.approx(m.beta(r, b, risk_free=rf), rel=1e-12)
    assert s["alpha"] == pytest.approx(m.alpha(r, b, risk_free=rf), rel=1e-12)
    assert s["beta"] != pytest.approx(m.beta(r, b), rel=1e-6)


def test_cvar_tail_of_a_single_observation() -> None:
    x = pd.Series(np.linspace(-0.05, 0.05, 63))  # 99% quantile sits between the two lowest
    res = m.var_cvar(x, var_confidence=0.99, cvar_confidence=0.99)
    assert res.cvar == pytest.approx(0.05)  # the tail is the single lowest observation


def test_overlapping_horizon_of_two() -> None:
    x = pd.Series(np.random.default_rng(2).normal(0, 0.01, 200))
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"horizon_method": "overlapping"}}})
    res = m.var_cvar(x, horizon=2, config=cfg)
    agg = ((1 + x).rolling(2).apply(np.prod, raw=True) - 1).dropna().to_numpy()
    assert res.adjustments == ("overlapping 2-session returns",)
    assert res.var == pytest.approx(-float(np.quantile(agg, 0.05)), rel=1e-12)


def test_correlation_stays_within_bounds_for_exact_lines() -> None:
    rng = np.random.default_rng(7)
    for _ in range(50):
        x = pd.Series(rng.normal(0, 0.01, 40))
        for y in (x * 3.0 + 0.001, -x * 0.7 + 0.002):
            c = m.correlation(x, y)
            assert -1.0 <= c <= 1.0 and abs(abs(c) - 1.0) < 1e-12
            rc = m.rolling_correlation(x, y, 10).dropna()
            assert ((rc >= -1.0) & (rc <= 1.0)).all()


def test_summary_passes_periods_to_omega_without_a_config(reference: Reference) -> None:
    # a 0% threshold is 0 per session for any P, so a non-zero one is needed to see `periods`
    r = reference.returns.iloc[:200]
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"omega_threshold_annual": 0.3}}})
    s = m.performance_summary(r, periods=12, config=cfg)
    want = m.omega_ratio(r, periods=12, threshold_annual=0.3)
    assert s["omega"] == pytest.approx(want, rel=1e-12)
    assert want != pytest.approx(m.omega_ratio(r, threshold_annual=0.3), rel=1e-6)


def test_cvar_tail_includes_an_observation_equal_to_the_quantile() -> None:
    x = pd.Series(np.arange(101) / 1000.0 - 0.05)  # q(0.05) at position 5.0: exactly x[5]
    q = float(np.quantile(x, 0.05))
    assert q == pytest.approx(float(x.iloc[5]), abs=1e-15)
    res = m.var_cvar(x, var_confidence=0.95, cvar_confidence=0.95)
    assert res.cvar == pytest.approx(-float(x.iloc[:6].mean()), rel=1e-12)  # six, not five


def test_rolling_metrics_flat_after_volatile_windows() -> None:
    # pandas' incremental rolling variance must not leave rounding residue on flat windows
    days = pd.bdate_range("2021-01-01", periods=60)
    rng = np.random.default_rng(3)
    b = pd.Series(list(rng.normal(0, 0.01, 40)) + [0.001] * 20, index=days)
    r = pd.Series(rng.normal(0, 0.01, 60), index=days)
    assert m.rolling_beta(r, b, 10).iloc[-5:].isna().all()
    assert m.rolling_correlation(r, b, 10).iloc[-5:].isna().all()
    assert m.rolling_sharpe(b, 10).iloc[-5:].isna().all()
    assert (m.rolling_vol(b, 10).iloc[-5:] == 0.0).all()
