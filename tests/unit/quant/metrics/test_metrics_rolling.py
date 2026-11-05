"""Rolling metrics and the performance summary (Stage 20 Task 3)."""

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from metrics_reference import Reference

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.quant.metrics.performance import (
    annualised_mean,
    annualised_return,
    annualised_vol,
    calmar_ratio,
    cumulative_return,
    excess_kurtosis,
    hit_rate,
    max_drawdown,
    max_drawdown_duration,
    omega_ratio,
    performance_summary,
    profit_factor,
    rolling_max_drawdown,
    rolling_metric,
    rolling_sharpe,
    rolling_vol,
    sharpe_ratio,
    skewness,
    sortino_ratio,
    tail_ratio,
    ulcer_index,
)

METRICS = {"max_drawdown": max_drawdown, "hit_rate": hit_rate, "cumulative": cumulative_return}


@settings(max_examples=100, deadline=None)
@given(
    st.lists(st.floats(min_value=-0.3, max_value=0.3), min_size=2, max_size=40),
    st.integers(min_value=2, max_value=12),
    st.sampled_from(sorted(METRICS)),
)
def test_rolling_metric_equals_metric_on_each_window(
    values: list[float], window: int, name: str
) -> None:
    r = pd.Series(values, index=pd.bdate_range("2021-01-01", periods=len(values)))
    fn = METRICS[name]
    out = rolling_metric(r, fn, window)
    assert list(out.index) == list(r.index)
    for i in range(len(r)):
        if i + 1 < window:
            assert math.isnan(out.iloc[i])
        else:
            assert out.iloc[i] == pytest.approx(fn(r.iloc[i + 1 - window : i + 1]), abs=1e-15)


def test_vectorised_rolling_equals_generic(reference: Reference) -> None:
    r = reference.returns
    pairs = [
        (rolling_vol(r, 63), rolling_metric(r, annualised_vol, 63)),
        (rolling_sharpe(r, 63), rolling_metric(r, sharpe_ratio, 63)),
        (rolling_max_drawdown(r, 63), rolling_metric(r, max_drawdown, 63)),
        (
            rolling_sharpe(r, 21, risk_free=0.0002),
            rolling_metric(r, lambda x: sharpe_ratio(x, risk_free=0.0002), 21),
        ),
    ]
    for fast, slow in pairs:
        assert list(fast.index) == list(slow.index)
        np.testing.assert_allclose(fast.to_numpy(), slow.to_numpy(), rtol=1e-9, atol=1e-12)


def test_rolling_matches_empyrical_last_value(reference: Reference) -> None:
    r, v = reference.returns, reference.values
    assert rolling_sharpe(r, 63).iloc[-1] == pytest.approx(v["rolling_sharpe_63_last"], rel=1e-9)
    assert rolling_vol(r, 63).iloc[-1] == pytest.approx(v["rolling_volatility_63_last"], rel=1e-9)
    assert -rolling_max_drawdown(r, 63).iloc[-1] == pytest.approx(
        v["rolling_max_drawdown_63_last"], rel=1e-9
    )


def test_rolling_defaults_and_validation(reference: Reference) -> None:
    r = reference.returns
    out = rolling_vol(r)  # default window quant.metrics.rolling_window = 63
    assert out.iloc[:62].isna().all() and out.iloc[62:].notna().all()
    cfg = AtlasConfig.model_validate({"quant": {"metrics": {"rolling_window": 10}}})
    assert rolling_vol(r, config=cfg).iloc[:9].isna().all()
    assert rolling_vol(r, config=cfg).iloc[9:].notna().all()
    for bad in (1, 0, True, 2.5):
        with pytest.raises(ValueError, match="window"):
            rolling_sharpe(r, bad)  # type: ignore[arg-type]
    short = r.iloc[:5]
    assert rolling_max_drawdown(short, 10).isna().all()
    assert rolling_metric(short, max_drawdown, 10).isna().all()


def test_rolling_sharpe_flat_window_is_nan() -> None:
    r = pd.Series([0.01] * 5 + [0.02, -0.01], index=pd.bdate_range("2021-01-01", periods=7))
    out = rolling_sharpe(r, 3)
    assert out.iloc[2:5].isna().all() and out.iloc[5:].notna().all()


def test_performance_summary_matches_each_function(reference: Reference) -> None:
    r = reference.returns
    s = performance_summary(r)
    expected = {
        "n_sessions": 504,
        "start": r.index[0],
        "end": r.index[-1],
        "cumulative_return": cumulative_return(r),
        "annualised_return": annualised_return(r),
        "annualised_mean": annualised_mean(r),
        "annualised_vol": annualised_vol(r),
        "sharpe": sharpe_ratio(r),
        "sortino": sortino_ratio(r),
        "calmar": calmar_ratio(r),
        "omega": omega_ratio(r),
        "max_drawdown": max_drawdown(r),
        "max_drawdown_duration": max_drawdown_duration(r),
        "ulcer_index": ulcer_index(r),
        "skewness": skewness(r),
        "excess_kurtosis": excess_kurtosis(r),
        "hit_rate": hit_rate(r),
        "profit_factor": profit_factor(r),
        "tail_ratio": tail_ratio(r),
    }
    assert s == expected
    s12 = performance_summary(r, periods=12, risk_free=0.001)
    assert s12["annualised_vol"] == pytest.approx(annualised_vol(r, periods=12))
    assert s12["sharpe"] == pytest.approx(sharpe_ratio(r, periods=12, risk_free=0.001))
    with pytest.raises(DataError):
        performance_summary(r.iloc[:1])
