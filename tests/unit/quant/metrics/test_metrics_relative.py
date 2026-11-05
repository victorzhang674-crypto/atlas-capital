"""Benchmark-relative metrics (Stage 20 Task 4)."""

import math
import statistics

import numpy as np
import pandas as pd
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from metrics_reference import Reference

from atlas.core.errors import DataError
from atlas.quant.metrics.performance import annualised_return, rolling_metric
from atlas.quant.metrics.relative import (
    active_return,
    alpha,
    beta,
    correlation,
    down_capture,
    information_ratio,
    relative_summary,
    rolling_beta,
    rolling_correlation,
    tracking_error,
    up_capture,
    up_down_capture,
)

DAYS = pd.bdate_range("2024-01-01", periods=8)
R = pd.Series([0.02, -0.01, 0.03, -0.02, 0.01], index=DAYS[:5])
B = pd.Series([0.01, -0.02, 0.02, -0.01, 0.0], index=DAYS[:5])


def test_beta_alpha_hand_example() -> None:
    r, b = R.tolist(), B.tolist()
    want_beta = statistics.covariance(r, b) / statistics.variance(b)
    assert beta(R, B) == pytest.approx(want_beta, rel=1e-12)
    daily = statistics.mean([x - want_beta * y for x, y in zip(r, b, strict=True)])
    assert alpha(R, B) == pytest.approx((1 + daily) ** 252 - 1, rel=1e-12)
    rf = 0.001
    re, be = [x - rf for x in r], [y - rf for y in b]
    beta_rf = statistics.covariance(re, be) / statistics.variance(be)
    assert beta(R, B, risk_free=rf) == pytest.approx(beta_rf, rel=1e-12)
    daily_rf = statistics.mean([x - beta_rf * y for x, y in zip(re, be, strict=True)])
    assert alpha(R, B, risk_free=rf) == pytest.approx((1 + daily_rf) ** 252 - 1, rel=1e-12)


def test_tracking_error_information_ratio_active_return_hand_example() -> None:
    active = [x - y for x, y in zip(R, B, strict=True)]
    te = statistics.stdev(active) * math.sqrt(252)
    assert tracking_error(R, B) == pytest.approx(te, rel=1e-12)
    want_ir = statistics.mean(active) * 252 / te
    assert information_ratio(R, B) == pytest.approx(want_ir, rel=1e-12)
    want_active = annualised_return(R) - annualised_return(B)
    assert active_return(R, B) == pytest.approx(want_active, rel=1e-12)


def test_capture_hand_example() -> None:
    up = ((1.02 * 1.03) ** (252 / 2) - 1) / ((1.01 * 1.02) ** (252 / 2) - 1)
    down = ((0.99 * 0.98) ** (252 / 2) - 1) / ((0.98 * 0.99) ** (252 / 2) - 1)
    assert up_capture(R, B) == pytest.approx(up, rel=1e-12)  # sessions 0 and 2
    assert down_capture(R, B) == pytest.approx(down, rel=1e-12)  # sessions 1 and 3
    assert up_down_capture(R, B) == pytest.approx(up / down, rel=1e-12)


def test_correlation_hand_example() -> None:
    want = statistics.correlation(R.tolist(), B.tolist())
    assert correlation(R, B) == pytest.approx(want, rel=1e-12)


def test_alignment_is_an_inner_join() -> None:
    r = pd.Series([0.02, -0.01, np.nan, 0.03, -0.02, 0.01, 0.5], index=DAYS[:7])
    b = pd.Series([0.01, -0.02, 0.4, 0.02, -0.01, 0.0], index=DAYS[:6])
    keep = [0, 1, 3, 4, 5]
    rr, bb = r.iloc[keep], b.iloc[keep]
    assert beta(r, b) == pytest.approx(beta(rr, bb), rel=1e-12)
    assert tracking_error(r, b) == pytest.approx(tracking_error(rr, bb), rel=1e-12)
    with pytest.raises(DataError, match="common sessions"):
        beta(r.iloc[:1], b)


def test_undefined_relative_metrics_are_nan() -> None:
    flat = pd.Series([0.01] * 5, index=DAYS[:5])
    assert math.isnan(beta(R, flat)) and math.isnan(alpha(R, flat))
    assert math.isnan(correlation(R, flat))
    assert math.isnan(information_ratio(B, B))  # zero tracking error
    assert tracking_error(B, B) == 0.0
    ups = pd.Series([0.01, 0.02, 0.03, 0.01, 0.02], index=DAYS[:5])
    assert math.isnan(down_capture(R, ups)) and math.isnan(up_down_capture(R, ups))
    assert not math.isnan(up_capture(R, ups))


def test_matches_empyrical_reference(reference: Reference) -> None:
    r, b, v, rf = (
        reference.returns,
        reference.benchmark,
        reference.values,
        reference.risk_free_daily,
    )
    pairs = {
        "beta": beta(r, b),
        "alpha": alpha(r, b),
        "alpha_rf": alpha(r, b, risk_free=rf),
        "beta_rf": beta(r, b, risk_free=rf),
        "up_capture": up_capture(r, b),
        "down_capture": down_capture(r, b),
        "up_down_capture": up_down_capture(r, b),
        "excess_sharpe": information_ratio(r, b) / math.sqrt(252),  # empyrical: not annualised
        "rolling_beta_63_last": rolling_beta(r, b, 63).iloc[-1],
    }
    for key, ours in pairs.items():
        assert ours == pytest.approx(v[key], rel=1e-9, abs=1e-12), key


def test_rolling_equals_scalar_per_window(reference: Reference) -> None:
    r, b = reference.returns.iloc[:150], reference.benchmark.iloc[:150]
    joined = pd.concat([r, b], axis=1)
    for w in (5, 63):
        fast_beta = rolling_beta(r, b, w, risk_free=0.0001)
        fast_corr = rolling_correlation(r, b, w)
        for i in range(len(joined)):
            if i + 1 < w:
                assert math.isnan(fast_beta.iloc[i]) and math.isnan(fast_corr.iloc[i])
                continue
            window = joined.iloc[i + 1 - w : i + 1]
            x, y = window.iloc[:, 0], window.iloc[:, 1]
            assert fast_beta.iloc[i] == pytest.approx(beta(x, y, risk_free=0.0001), rel=1e-8)
            assert fast_corr.iloc[i] == pytest.approx(correlation(x, y), rel=1e-8)
    generic = rolling_metric(r, lambda x: beta(x, b.loc[x.index]), 21)
    np.testing.assert_allclose(rolling_beta(r, b, 21).to_numpy(), generic.to_numpy(), rtol=1e-8)


def test_relative_summary(reference: Reference) -> None:
    r, b = reference.returns, reference.benchmark
    s = relative_summary(r, b)
    assert s == {
        "n_common": 504,
        "beta": beta(r, b),
        "alpha": alpha(r, b),
        "tracking_error": tracking_error(r, b),
        "information_ratio": information_ratio(r, b),
        "active_return": active_return(r, b),
        "up_capture": up_capture(r, b),
        "down_capture": down_capture(r, b),
        "up_down_capture": up_down_capture(r, b),
        "correlation": correlation(r, b),
    }


returns_lists = st.lists(st.floats(min_value=-0.2, max_value=0.2), min_size=3, max_size=50)


@settings(max_examples=100, deadline=None)
@given(returns_lists, st.floats(min_value=-2.0, max_value=2.0), st.floats(-0.01, 0.01))
def test_beta_of_a_linear_combination(values: list[float], slope: float, shift: float) -> None:
    b = pd.Series(values)
    assume(float(np.std(values)) > 1e-4)
    r = (slope * b + shift).clip(lower=-1.0)
    assume(bool((slope * b + shift >= -1.0).all()))
    assert beta(r, b) == pytest.approx(slope, rel=1e-6, abs=1e-6)
    assert tracking_error(b, b) == 0.0


@settings(max_examples=100, deadline=None)
@given(returns_lists, returns_lists)
def test_correlation_is_bounded(x: list[float], y: list[float]) -> None:
    n = min(len(x), len(y))
    c = correlation(x[:n], y[:n])
    assert math.isnan(c) or -1.0 <= c <= 1.0


def test_time_varying_risk_free_changes_beta(reference: Reference) -> None:
    # A constant rate cancels in cov / var; a rate that moves with the sessions does not
    # (mutation-check survivor: rolling beta ignored the risk-free rate).
    r, b = reference.returns.iloc[:200], reference.benchmark.iloc[:200]
    rf = pd.Series(np.linspace(0.0, 0.004, 200), index=r.index)
    exact = beta(r, b, risk_free=rf)
    assert exact != pytest.approx(beta(r, b), rel=1e-6)
    want = statistics.covariance(list(r - rf), list(b - rf)) / statistics.variance(list(b - rf))
    assert exact == pytest.approx(want, rel=1e-12)
    rolled = rolling_beta(r, b, 200, risk_free=rf)
    assert rolled.iloc[-1] == pytest.approx(exact, rel=1e-9)
    assert rolling_beta(r, b, 50, risk_free=rf).iloc[-1] == pytest.approx(
        beta(r.iloc[-50:], b.iloc[-50:], risk_free=rf), rel=1e-9
    )
