"""Findings of the independent review (Stage 20, 2026-11-05), each reproduced first."""

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant import metrics as m
from atlas.quant.metrics.var import _moments
from atlas.quant.research.vector_backtest import _summary

# --- 1 (MEDIUM): Cornish-Fisher outside its domain of validity ---------------------------------


def _cf_map(z: np.ndarray, s: float, k: float) -> np.ndarray:
    return z + (z**2 - 1) * s / 6 + (z**3 - 3 * z) * k / 24 - (2 * z**3 - 5 * z) * s**2 / 36


@pytest.mark.parametrize(
    ("skew", "kurt", "monotone"),
    [(0.0, 0.0, True), (-0.5, 2.0, True), (0.21, 3.22, True), (0.0, 6.0, True),
     (0.15, -0.15, True), (1.08, 12.6, False), (-0.54, 11.48, False), (-3.0, 0.0, False),
     (2.0, 0.0, False), (0.0, -1.2, False), (0.0, 9.0, False)],
)  # fmt: skip
def test_cornish_fisher_monotone_matches_a_dense_grid(
    skew: float, kurt: float, monotone: bool
) -> None:
    z = np.linspace(-4, 4, 80_001)  # quant.metrics.cornish_fisher_z_max
    assert bool((np.diff(_cf_map(z, skew, kurt)) > 0).all()) is monotone
    assert m.cornish_fisher_monotone(skew, kurt) is monotone


def test_thin_tails_turn_only_far_out() -> None:
    # Maillard's whole-line domain rejects (0.15, -0.15), but its map turns only at z = -5.72
    # and z = +7.94 (roots of a z^2 + b z + c, a = -0.0225, b = 0.05, c = 1.0219)
    z = np.linspace(-8, 8, 160_001)
    d = np.diff(_cf_map(z, 0.15, -0.15))
    assert (d[np.abs(z[1:]) < 5.7] > 0).all() and not (d > 0).all()
    assert (d[z[1:] < -5.75] < 0).all() and (d[z[1:] > 7.95] < 0).all()
    assert not m.cornish_fisher_monotone(0.15, -0.15, z_max=8.0)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"cornish_fisher_z_max": 3.0}}})
    x = np.random.default_rng(1).standard_t(3, 500) * 0.01 / math.sqrt(3)
    with pytest.raises(DataError, match="Cornish-Fisher"):  # dip near the centre: any z_max
        m.var_cvar(x, method="cornish_fisher", config=cfg)


def test_cornish_fisher_var_refuses_a_non_monotone_expansion() -> None:
    x = np.random.default_rng(1).standard_t(3, 500) * 0.01 / math.sqrt(3)  # the reviewer's case
    s, k = _moments(x)
    assert s == pytest.approx(1.08, abs=0.01) and k == pytest.approx(12.6, abs=0.1)
    with pytest.raises(DataError, match="Cornish-Fisher"):
        m.var_cvar(x, method="cornish_fisher")
    # was: VaR95 0.0111 < historical 0.0136, adjustments () — understated with no flag
    assert m.var_cvar(x).var == pytest.approx(0.01362, abs=1e-5)  # historical still answers


def test_cornish_fisher_still_answers_inside_its_domain() -> None:
    x = np.random.default_rng(0).standard_t(3, 500) * 0.01 / math.sqrt(3)  # S 0.21, K 3.2
    res = m.var_cvar(x, method="cornish_fisher")
    assert res.var > 0 and res.cvar >= res.var and res.adjustments == ()


# --- 2 (MEDIUM): ADV from a partial window -----------------------------------------------------


class _StubView:
    def __init__(self, sessions: dict[str, int], config: AtlasConfig | None = None) -> None:
        self._sessions = sessions
        self.config = config if config is not None else AtlasConfig()
        self.lookbacks: list[int | None] = []

    def adv(self, symbols: list[str], lookback: int | None = None) -> pd.DataFrame:
        self.lookbacks.append(lookback)
        return pd.DataFrame(
            {"adv_usd": [5e6] * len(symbols), "adv_shares": [1e5] * len(symbols),
             "sessions": [self._sessions[s] for s in symbols]},
            index=pd.Index(symbols, name="symbol"),
        )  # fmt: skip


def test_partial_window_adv_is_not_trusted() -> None:
    view = _StubView({"AAA": 20, "IPO": 1, "GAP": 18, "GAP3": 17})
    pos = pd.Series({"AAA": 1e6, "IPO": 1e6, "GAP": 1e6, "GAP3": 1e6})
    prof = m.liquidity_from_view(view, pos)  # type: ignore[arg-type]
    assert prof.days["AAA"] == pytest.approx(2.0) and prof.days["GAP"] == pytest.approx(2.0)
    assert math.isinf(prof.days["IPO"]) and math.isinf(prof.days["GAP3"])  # < 90% of 20
    assert set(prof.no_adv) == {"IPO", "GAP3"}
    strict = _StubView(
        {"AAA": 20, "GAP": 18},
        AtlasConfig.model_validate({"quant": {"metrics": {"adv_min_coverage": 1.0}}}),
    )
    prof2 = m.liquidity_from_view(strict, pd.Series({"AAA": 1e6, "GAP": 1e6}))  # type: ignore[arg-type]
    assert prof2.no_adv == ("GAP",)
    short = _StubView({"AAA": 5})
    assert m.liquidity_from_view(short, pd.Series({"AAA": 1e6}), lookback=5).no_adv == ()  # type: ignore[arg-type]
    assert short.lookbacks == [5]


# --- 3 (LOW): Monte Carlo with too few scenarios ----------------------------------------------


def test_monte_carlo_needs_var_min_obs_scenarios() -> None:
    cov = pd.DataFrame(np.eye(2) * 1e-4, index=["A", "B"], columns=["A", "B"])
    w = pd.Series({"A": 0.5, "B": 0.5})
    with pytest.raises(DataError, match="at least 63"):
        m.portfolio_var(w, cov=cov, n_sims=3, seed=1)
    assert m.portfolio_var(w, cov=cov, n_sims=63, seed=1).n_obs == 63


# --- 4 (LOW): rounding at the high-water mark --------------------------------------------------


def test_recovery_to_the_high_water_mark_through_rounding() -> None:
    r = pd.Series([0.0, -0.13, 1 / 0.87 - 1] + [0.0] * 60)
    assert float(np.prod(1 + r.to_numpy())) < 1.0  # 0.9999999999999999: the rounding case
    [episode] = m.drawdown_periods(r)
    assert episode.recovered and episode.duration == 2
    assert m.max_drawdown_duration(r) == 2
    assert m.max_drawdown(r) == pytest.approx(0.13)
    assert m.drawdown_series(r).iloc[-1] == 0.0


# --- 5 (LOW): Stage 17 summary on a return below -100% ----------------------------------------


def test_backtest_summary_rejects_a_return_below_minus_one() -> None:
    # documented behaviour change of the Stage 20 refactor: a wiped-out book has no metrics
    with pytest.raises(ValueError, match="below -1"):
        _summary(np.array([0.01, -1.2, 0.02]), np.array([1.0]), np.zeros(3), 252)


# --- 6 (LOW): covariance-only arguments with a returns history ---------------------------------


@pytest.mark.parametrize(
    "extra",
    [{"mean": pd.Series({"A": 0.01})}, {"n_sims": 5}, {"seed": 1},
     {"rng": np.random.default_rng(1)}],
)  # fmt: skip
def test_returns_path_rejects_covariance_only_arguments(extra: dict[str, object]) -> None:
    frame = pd.DataFrame(np.random.default_rng(0).normal(0, 0.01, (100, 1)), columns=["A"])
    with pytest.raises(ValueError, match="only apply with cov"):
        m.portfolio_var(pd.Series({"A": 1.0}), returns=frame, **extra)  # type: ignore[arg-type]


# --- 7 (LOW): observations of an overlapping-horizon VaR --------------------------------------


def test_overlapping_var_reports_the_overlapping_count() -> None:
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"horizon_method": "overlapping"}}})
    x = pd.Series(np.random.default_rng(0).normal(0, 0.01, 200))
    assert m.var_cvar(x, horizon=10, config=cfg).n_obs == 191
    assert m.var_cvar(x, horizon=10).n_obs == 200  # sqrt scaling uses every observation


# --- second mutmut pass on the review fixes ----------------------------------------------------


def _numeric_min_slope(skew: float, kurt: float, z_max: float) -> float:
    z = np.linspace(-z_max, z_max, 40_001)
    return float((1 + z * skew / 3 + (z**2 - 1) * kurt / 8 - (6 * z**2 - 5) * skew**2 / 36).min())


@settings(max_examples=400, deadline=None)
@given(
    st.floats(min_value=-2.5, max_value=2.5),
    st.floats(min_value=-2.0, max_value=16.0),
    st.floats(min_value=3.0, max_value=6.0),
)
def test_cornish_fisher_monotone_matches_the_numerical_minimum(
    skew: float, kurt: float, z_max: float
) -> None:
    lowest = _numeric_min_slope(skew, kurt, z_max)
    assume(abs(lowest) > 1e-6)  # the grid cannot settle exact ties
    assert m.cornish_fisher_monotone(skew, kurt, z_max) is (lowest > 0)


def test_z_max_default_and_config_are_used() -> None:
    assert m.cornish_fisher_monotone(0.0, -0.42)  # slope at |z| = 4: 1 + 15 K / 8 = 0.21 > 0
    assert not m.cornish_fisher_monotone(0.0, -0.42, z_max=5.0)  # at 5: 1 + 3 K = -0.26
    u = np.linspace(-1, 1, 201)
    x = pd.Series(np.sign(u) * np.abs(u) ** 1.8 * 0.02)  # symmetric, excess kurtosis -0.42
    s, k = _moments(x.to_numpy())
    assert abs(s) < 1e-9 and k == pytest.approx(-0.4198, abs=1e-4)
    assert m.var_cvar(x, method="cornish_fisher").var > 0  # inside |z| <= 4
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"cornish_fisher_z_max": 5.0}}})
    with pytest.raises(DataError, match="Cornish-Fisher"):
        m.var_cvar(x, method="cornish_fisher", config=cfg)


def test_exact_quantile_boundary_of_the_cvar_tail() -> None:
    x = pd.Series((np.arange(101) - 50) / 64.0)  # multiples of 1/64: exact in binary
    assert float(np.quantile(x, 0.05)) == float(x.iloc[5])  # exactly on an observation
    res = m.var_cvar(x, var_confidence=0.95, cvar_confidence=0.95)
    assert res.cvar == pytest.approx(-float(x.iloc[:6].mean()), rel=1e-15)  # six, not five


def test_overlapping_two_session_count() -> None:
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"horizon_method": "overlapping"}}})
    x = pd.Series(np.random.default_rng(2).normal(0, 0.01, 200))
    assert m.var_cvar(x, horizon=2, config=cfg).n_obs == 199


def _kurt_for_min_slope(skew: float, target: float) -> float:
    """The excess kurtosis at which the slope's minimum over the real line equals `target`
    (a > 0 branch), found by bisection on K."""

    def lowest(k: float) -> float:
        a = k / 8 - skew**2 / 6
        b, c = skew / 3, 1 - k / 8 + 5 * skew**2 / 36
        return c - b * b / (4 * a)

    lo, hi = skew**2 * 4 / 3 + 1e-9, 40.0  # a > 0 just above lo; lowest() falls as K grows
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if lowest(mid) > target else (lo, mid)
    return (lo + hi) / 2


@pytest.mark.parametrize("skew", [-2.4, -1.6, -0.8, 0.8, 1.6, 2.4])
@pytest.mark.parametrize("target", [-1e-3, 1e-3])
def test_shallow_dips_are_judged_exactly(skew: float, target: float) -> None:
    kurt = _kurt_for_min_slope(skew, target)
    a = kurt / 8 - skew**2 / 6
    vertex = -(skew / 3) / (2 * a)
    assert -4 < vertex < 4 and abs(vertex) > 0.1  # the minimum sits inside, off the centre
    assert _numeric_min_slope(skew, kurt, 4.0) == pytest.approx(target, abs=2e-4)
    assert m.cornish_fisher_monotone(skew, kurt) is (target > 0)


def test_a_dip_just_outside_the_range_does_not_count() -> None:
    skew, kurt = -1.18, 2.19  # slope minimum -0.008 at z = 4.72, positive on [-4, 4]
    a = kurt / 8 - skew**2 / 6
    assert -(skew / 3) / (2 * a) == pytest.approx(4.72, abs=0.01)
    assert _numeric_min_slope(skew, kurt, 4.0) > 0 > _numeric_min_slope(skew, kurt, 5.0)
    assert m.cornish_fisher_monotone(skew, kurt) is True
    assert m.cornish_fisher_monotone(skew, kurt, z_max=5.0) is False


def test_monte_carlo_mean_ignores_symbols_outside_the_covariance() -> None:
    cov = pd.DataFrame(np.eye(2) * 1e-4, index=["A", "B"], columns=["A", "B"])
    w = pd.Series({"A": 0.6, "B": -0.2})
    plain = m.portfolio_var(w, cov=cov, seed=5, mean=pd.Series({"A": 0.01, "B": 0.0}))
    extra = m.portfolio_var(w, cov=cov, seed=5, mean=pd.Series({"A": 0.01, "B": 0.0, "Z": 9.9}))
    assert extra == plain
