"""Returns-based performance metrics (Stage 20 Task 2)."""

import math
import statistics

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from metrics_reference import Reference
from scipy import stats

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.metrics.performance import (
    DrawdownPeriod,
    annualised_mean,
    annualised_return,
    annualised_vol,
    calmar_ratio,
    cumulative_return,
    downside_risk,
    drawdown_periods,
    drawdown_series,
    excess_kurtosis,
    hit_rate,
    max_drawdown,
    max_drawdown_duration,
    omega_ratio,
    profit_factor,
    sharpe_ratio,
    skewness,
    sortino_ratio,
    tail_ratio,
    ulcer_index,
    wealth_index,
)

DAYS = pd.bdate_range("2024-01-01", periods=10)
R = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02], index=DAYS[:5])
FIRST_LOSS = pd.Series([-0.1, 0.05, 0.05, -0.2], index=DAYS[:4])

returns_lists = st.lists(
    st.floats(min_value=-0.5, max_value=0.5, allow_nan=False, allow_infinity=False),
    min_size=2,
    max_size=60,
)

# --- hand examples ---------------------------------------------------------------------------


def test_cumulative_and_annualised_return_hand_example() -> None:
    growth = 1.01 * 0.98 * 1.03 * 0.99 * 1.02
    assert growth == pytest.approx(1.0294850412, abs=1e-12)
    assert cumulative_return(R) == pytest.approx(growth - 1, rel=1e-12)
    assert annualised_return(R) == pytest.approx(growth ** (252 / 5) - 1, rel=1e-12)
    assert annualised_return(R, periods=12) == pytest.approx(growth ** (12 / 5) - 1, rel=1e-12)
    assert annualised_mean(R) == pytest.approx(0.006 * 252, rel=1e-12)


def test_vol_sharpe_sortino_hand_example() -> None:
    sd = statistics.stdev(R.tolist())  # independent: stdlib sample std
    assert sd == pytest.approx(math.sqrt(1.72e-3 / 4), rel=1e-12)
    assert annualised_vol(R) == pytest.approx(sd * math.sqrt(252), rel=1e-12)
    assert sharpe_ratio(R) == pytest.approx(0.006 / sd * math.sqrt(252), rel=1e-12)
    rf = 0.001
    sd_ex = statistics.stdev([x - rf for x in R])
    assert sharpe_ratio(R, risk_free=rf) == pytest.approx(0.005 / sd_ex * math.sqrt(252), rel=1e-12)
    assert downside_risk(R) == pytest.approx(0.01 * math.sqrt(252), rel=1e-12)
    assert sortino_ratio(R) == pytest.approx(0.006 * 252 / (0.01 * math.sqrt(252)), rel=1e-12)
    # required return 0.01: shortfalls 0, -0.03, 0, -0.02, 0 -> sqrt(mean) = sqrt(1.3e-3 / 5)
    assert downside_risk(R, required_return=0.01) == pytest.approx(
        math.sqrt(1.3e-3 / 5) * math.sqrt(252), rel=1e-12
    )


def test_sharpe_with_a_risk_free_series() -> None:
    rf = pd.Series([0.0, 0.001, 0.002, 0.0, 0.001, 0.5], index=DAYS[:6])
    excess = [x - y for x, y in zip(R, rf.iloc[:5], strict=True)]
    want = statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252)
    assert sharpe_ratio(R, risk_free=rf) == pytest.approx(want, rel=1e-12)
    with pytest.raises(DataError, match="risk-free"):
        sharpe_ratio(R, risk_free=rf.iloc[1:])


def test_default_risk_free_comes_from_config() -> None:
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"risk_free_annual": 0.03}}})
    rf = 1.03 ** (1 / 252) - 1
    excess = [x - rf for x in R]
    want = statistics.mean(excess) / statistics.stdev(excess) * math.sqrt(252)
    assert sharpe_ratio(R, config=cfg) == pytest.approx(want, rel=1e-12)


def test_omega_hit_rate_profit_factor_tail_ratio_hand_example() -> None:
    assert omega_ratio(R) == pytest.approx(0.06 / 0.03, rel=1e-12)
    tau = 1.10 ** (1 / 252) - 1
    gains = sum(max(x - tau, 0) for x in R)
    losses = sum(max(tau - x, 0) for x in R)
    assert omega_ratio(R, threshold_annual=0.10) == pytest.approx(gains / losses, rel=1e-12)
    assert hit_rate(R) == pytest.approx(0.6)
    assert hit_rate(pd.Series([0.0, 0.01, -0.01], index=DAYS[:3])) == pytest.approx(1 / 3)
    assert profit_factor(R) == pytest.approx(2.0, rel=1e-12)
    # sorted -0.02, -0.01, 0.01, 0.02, 0.03: q(0.95) = 0.028, q(0.05) = -0.018 (linear)
    assert tail_ratio(R) == pytest.approx(0.028 / 0.018, rel=1e-12)
    assert tail_ratio(R, quantile=0.25) == pytest.approx(0.02 / 0.01, rel=1e-12)
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"tail_quantile": 0.25}}})
    assert tail_ratio(R, config=cfg) == pytest.approx(2.0, rel=1e-12)
    with pytest.raises(ValueError, match="quantile"):
        tail_ratio(R, quantile=0.5)
    for bad in ("0.05", True, math.nan):  # audit finding 2: a non-number is a ValueError
        with pytest.raises(ValueError, match="quantile"):
            tail_ratio(R, quantile=bad)  # type: ignore[arg-type]


def test_skewness_and_kurtosis_match_scipy() -> None:
    x = np.array([0.01, -0.03, 0.02, 0.05, -0.01, 0.0, 0.015, -0.04])
    assert skewness(x) == pytest.approx(float(stats.skew(x)), rel=1e-12)
    assert excess_kurtosis(x) == pytest.approx(float(stats.kurtosis(x)), rel=1e-12)
    assert math.isnan(skewness([0.01, 0.01, 0.01]))
    assert math.isnan(excess_kurtosis([0.01, 0.01, 0.01]))


# --- drawdowns -------------------------------------------------------------------------------


def test_wealth_and_drawdown_series_hand_example() -> None:
    w = wealth_index(R)
    assert w.tolist() == pytest.approx([1.01, 0.9898, 1.019494, 1.00929906, 1.0294850412])
    dd = drawdown_series(R)
    assert list(dd.index) == list(R.index)
    assert dd.tolist() == pytest.approx([0.0, -0.02, 0.0, 1.00929906 / 1.019494 - 1, 0.0])
    assert max_drawdown(R) == pytest.approx(0.02, rel=1e-12)


def test_drawdown_starts_from_initial_capital() -> None:
    dd = drawdown_series(FIRST_LOSS)
    assert dd.tolist() == pytest.approx([-0.1, -0.055, -0.00775, -0.2062])
    assert max_drawdown(FIRST_LOSS) == pytest.approx(0.2062, rel=1e-12)
    assert max_drawdown(pd.Series([-0.05], index=DAYS[:1])) == pytest.approx(0.05)


def test_drawdown_periods_hand_example() -> None:
    periods = drawdown_periods(R)
    assert periods == [
        DrawdownPeriod(peak=DAYS[0], trough=DAYS[1], recovery=DAYS[2], depth=pytest.approx(0.02),
                       to_trough=1, duration=2),
        DrawdownPeriod(peak=DAYS[2], trough=DAYS[3], recovery=DAYS[4],
                       depth=pytest.approx(1 - 1.00929906 / 1.019494), to_trough=1, duration=2),
    ]  # fmt: skip
    assert all(p.recovered for p in periods)
    [open_] = drawdown_periods(FIRST_LOSS)
    assert open_.peak is None and open_.trough == DAYS[3] and open_.recovery is None
    assert not open_.recovered
    assert open_.depth == pytest.approx(0.2062) and open_.to_trough == 4 and open_.duration == 4
    assert max_drawdown_duration(R) == 2
    assert max_drawdown_duration(FIRST_LOSS) == 4


def test_drawdown_periods_mixed_episodes() -> None:
    # up, down 2 sessions, recover above, down and never recover
    r = pd.Series([0.1, -0.1, -0.1, 0.3, 0.01, -0.05, 0.01], index=DAYS[:7])
    periods = drawdown_periods(r)
    assert [(p.peak, p.trough, p.recovery) for p in periods] == [
        (DAYS[0], DAYS[2], DAYS[3]), (DAYS[4], DAYS[5], None)
    ]  # fmt: skip
    assert [p.duration for p in periods] == [3, 2]
    assert [p.to_trough for p in periods] == [2, 1]
    assert periods[0].depth == pytest.approx(1 - 0.81)
    assert max_drawdown_duration(r) == 3
    assert drawdown_periods(pd.Series([0.01, 0.02], index=DAYS[:2])) == []
    assert max_drawdown_duration(pd.Series([0.01, 0.0], index=DAYS[:2])) == 0


def test_ulcer_index_hand_example() -> None:
    dd = [-0.1, -0.055, -0.00775, -0.2062]
    assert ulcer_index(FIRST_LOSS) == pytest.approx(math.sqrt(sum(x * x for x in dd) / 4))
    assert ulcer_index(pd.Series([0.01, 0.02], index=DAYS[:2])) == 0.0


def test_total_loss_drawdown() -> None:
    r = pd.Series([0.1, -1.0, 0.0], index=DAYS[:3])
    assert max_drawdown(r) == pytest.approx(1.0)
    assert drawdown_series(r).tolist() == pytest.approx([0.0, -1.0, -1.0])
    assert cumulative_return(r) == pytest.approx(-1.0)


def test_calmar_hand_example() -> None:
    assert calmar_ratio(R) == pytest.approx(annualised_return(R) / 0.02, rel=1e-12)


# --- undefined and insufficient --------------------------------------------------------------


def test_undefined_ratios_are_nan() -> None:
    flat = pd.Series([0.01, 0.01, 0.01], index=DAYS[:3])
    assert math.isnan(sharpe_ratio(flat))
    assert math.isnan(sortino_ratio(flat))  # no session below 0
    assert math.isnan(calmar_ratio(flat))  # no drawdown
    assert math.isnan(omega_ratio(flat))  # no loss
    assert math.isnan(profit_factor(flat))
    assert math.isnan(tail_ratio(pd.Series([0.0, 0.0, 0.01], index=DAYS[:3])))
    assert annualised_vol(flat) == 0.0 and max_drawdown(flat) == 0.0


def test_not_enough_data_raises() -> None:
    empty = pd.Series([], dtype="float64")
    one = pd.Series([0.01], index=DAYS[:1])
    for fn in (cumulative_return, annualised_return, max_drawdown, hit_rate, ulcer_index):
        with pytest.raises(DataError):
            fn(empty)
    for fn in (annualised_vol, sharpe_ratio, skewness, excess_kurtosis):
        with pytest.raises(DataError, match="at least 2"):
            fn(one)


def test_input_validation_reaches_every_metric() -> None:
    bad = pd.Series([0.01, -2.0], index=DAYS[:2])
    for fn in (cumulative_return, sharpe_ratio, max_drawdown, sortino_ratio, omega_ratio):
        with pytest.raises(ValueError, match="below -1"):
            fn(bad)
    with pytest.raises(ValueError, match="required_return"):
        sortino_ratio(R, required_return=math.nan)
    with pytest.raises(ValueError, match="annual rate"):
        omega_ratio(R, threshold_annual=-1.0)


# --- empyrical reference ---------------------------------------------------------------------


def test_matches_empyrical_reference(reference: Reference) -> None:
    r, v, rf = reference.returns, reference.values, reference.risk_free_daily
    pairs = {
        "annual_return": annualised_return(r),
        "annual_volatility": annualised_vol(r),
        "cum_returns_final": cumulative_return(r),
        "sharpe_ratio": sharpe_ratio(r),
        "sharpe_ratio_rf": sharpe_ratio(r, risk_free=rf),
        "sortino_ratio": sortino_ratio(r),
        "sortino_ratio_req": sortino_ratio(r, required_return=rf),
        "downside_risk": downside_risk(r),
        "calmar_ratio": calmar_ratio(r),
        "omega_ratio": omega_ratio(r),
        "omega_ratio_thr": omega_ratio(r, threshold_annual=0.05),
        "max_drawdown": -max_drawdown(r),  # empyrical reports it negative
        "tail_ratio": tail_ratio(r),
        "skew": skewness(r),
        "kurtosis": excess_kurtosis(r),
    }
    for key, ours in pairs.items():
        assert ours == pytest.approx(v[key], rel=1e-9, abs=1e-12), key


# --- properties ------------------------------------------------------------------------------


@settings(max_examples=150, deadline=None)
@given(returns_lists)
def test_drawdown_properties(values: list[float]) -> None:
    r = pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)))
    dd = drawdown_series(r)
    assert (dd <= 0).all() and (dd >= -1).all()
    mdd = max_drawdown(r)
    assert 0.0 <= mdd <= 1.0
    assert mdd == pytest.approx(-float(dd.min()), abs=1e-15)
    periods = drawdown_periods(r)
    assert max((p.depth for p in periods), default=0.0) == pytest.approx(mdd, abs=1e-15)
    for p in periods:
        assert 1 <= p.to_trough <= p.duration


@settings(max_examples=100, deadline=None)
@given(returns_lists, st.floats(min_value=0.01, max_value=1.9))
def test_sharpe_is_scale_invariant(values: list[float], scale: float) -> None:
    r = pd.Series(values)
    assume(float(np.std(values)) > 1e-6)
    assert sharpe_ratio(r * scale / 2) == pytest.approx(sharpe_ratio(r), rel=1e-6, abs=1e-9)


@settings(max_examples=50, deadline=None)
@given(st.floats(min_value=-0.05, max_value=0.05), st.integers(min_value=1, max_value=500))
def test_cagr_of_a_constant_return(rate: float, n: int) -> None:
    r = pd.Series([rate] * n)
    assert annualised_return(r) == pytest.approx((1 + rate) ** 252 - 1, rel=1e-9, abs=1e-12)
    assert cumulative_return(r) == pytest.approx((1 + rate) ** n - 1, rel=1e-9, abs=1e-12)
