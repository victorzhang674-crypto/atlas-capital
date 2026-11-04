"""Stage 17 Task 7 (brief task 3): quantile buckets, mean return per bucket, monotonicity,
long-short spread and the cumulative spread chart data."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import LookAheadError
from atlas.core.pit import AsOf, tag_label
from atlas.quant.research import (
    ForwardReturns,
    QuantileAnalysis,
    compound_forward,
    daily_quantile_returns,
    monotonicity,
    quantile_analysis,
    quantile_buckets,
    quantile_returns,
)

DAYS = pd.DatetimeIndex(
    pd.to_datetime(list(TradingCalendar().sessions(date(2021, 1, 4), date(2022, 6, 1)))),
    name="date",
)


def _frame(values: np.ndarray, start: int = 0) -> pd.DataFrame:
    cols = [f"N{i:03d}" for i in range(values.shape[1])]
    return pd.DataFrame(values, index=DAYS[start : start + len(values)], columns=cols)


def _labels(
    daily: pd.DataFrame, dates: pd.DatetimeIndex, horizons: tuple[int, ...]
) -> ForwardReturns:
    frames = {h: tag_label(compound_forward(daily, list(dates), h)) for h in horizons}
    return ForwardReturns(
        dates=tuple(d.date() for d in dates),
        symbols=tuple(daily.columns),
        horizons=horizons,
        daily=tag_label(daily),
        known_at=AsOf.for_session(date(2022, 6, 1)),
        blocked=(),
        labels=frames,
    )


def test_buckets_equal_sized_ties_together() -> None:
    signal = _frame(np.arange(10, dtype="float64")[None, :])
    out = quantile_buckets(signal, 5, min_names=5)
    assert list(out.iloc[0]) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    eleven = _frame(np.arange(11, dtype="float64")[None, :])
    sizes = quantile_buckets(eleven, 5, min_names=5).iloc[0].value_counts().sort_index()
    assert list(sizes) == [3, 2, 2, 2, 2]
    constant = _frame(np.ones((1, 10)))
    assert quantile_buckets(constant, 5, min_names=5).iloc[0].nunique() == 1
    ties = _frame(np.array([[1, 1, 1, 1, 2, 3, 4, 5, 6, 7]], dtype="float64"))
    row = quantile_buckets(ties, 5, min_names=5).iloc[0]
    assert row.iloc[:4].nunique() == 1  # equal signals share a bucket
    gaps = _frame(np.array([[np.nan, 1, 2, 3, 4, 5, np.nan, 6, 7, 8, 9, 10]], dtype="float64"))
    row = quantile_buckets(gaps, 5, min_names=5).iloc[0]
    assert math.isnan(row.iloc[0]) and math.isnan(row.iloc[6])
    assert list(row.dropna()) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    few = quantile_buckets(_frame(np.arange(4, dtype="float64")[None, :]), 5, min_names=5)
    assert few.isna().all().all()


def test_monotonic_signal_scores_plus_one() -> None:
    rng = np.random.default_rng(11)
    s = rng.standard_normal((40, 100))
    y = 0.01 * s + 0.001 * rng.standard_normal((40, 100))
    buckets = quantile_buckets(_frame(s), 5, min_names=20)
    by_date = quantile_returns(buckets, _frame(y))
    means = by_date.mean()
    assert list(means.index) == [1, 2, 3, 4, 5]
    assert monotonicity(means) == pytest.approx(1.0)
    assert means[5] > means[1]


def test_reversed_signal_scores_minus_one() -> None:
    rng = np.random.default_rng(12)
    s = rng.standard_normal((40, 100))
    y = -0.01 * s + 0.001 * rng.standard_normal((40, 100))
    means = quantile_returns(quantile_buckets(_frame(s), 5, min_names=20), _frame(y)).mean()
    assert monotonicity(means) == pytest.approx(-1.0)
    assert monotonicity(
        pd.Series([0.1, 0.3, 0.2, 0.4, 0.5], index=[1, 2, 3, 4, 5])
    ) == pytest.approx(0.9)
    assert math.isnan(monotonicity(pd.Series([0.1, np.nan, 0.2], index=[1, 2, 3])))
    assert math.isnan(monotonicity(pd.Series([0.1, 0.1, 0.1], index=[1, 2, 3])))


def test_spread_is_top_minus_bottom() -> None:
    signal = _frame(np.array([[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]], dtype="float64"))
    labels = _frame(np.array([[0.01, 0.03, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05, np.nan]]))
    by_date = quantile_returns(quantile_buckets(signal, 5, min_names=5), labels)
    assert by_date.iloc[0, 0] == pytest.approx(0.02)  # bucket 1: mean of 0.01, 0.03
    assert by_date.iloc[0, 4] == pytest.approx(0.05)  # bucket 5: the NaN label is left out
    assert by_date.iloc[0, 4] - by_date.iloc[0, 0] == pytest.approx(0.03)


def test_daily_quantile_returns_hold_membership() -> None:
    # 2 evaluation dates (sessions 0 and 2), 4 names, 2 buckets
    daily = _frame(
        np.array(
            [
                [np.nan, np.nan, np.nan, np.nan],
                [0.01, 0.02, 0.03, 0.04],
                [0.00, 0.00, 0.10, 0.20],
                [0.05, 0.01, 0.02, 0.00],
                [0.01, 0.01, 0.01, 0.01],
            ]
        )
    )
    signal = pd.DataFrame(
        [[1.0, 2.0, 3.0, 4.0], [np.nan, 3.0, 2.0, 1.0]],  # N000 drops out on date 2
        index=DAYS[[0, 2]],
        columns=daily.columns,
    )
    buckets = quantile_buckets(signal, 2, min_names=3)
    out = daily_quantile_returns(buckets, daily)
    assert list(out.index) == list(DAYS[1:5])  # from the session after the first date
    # sessions 1-2 use date 0's buckets (low: N000, N001; high: N002, N003)
    assert out.loc[DAYS[1], 1] == pytest.approx(0.015) and out.loc[DAYS[1], 2] == pytest.approx(
        0.035
    )
    assert out.loc[DAYS[2], 1] == pytest.approx(0.0) and out.loc[DAYS[2], 2] == pytest.approx(0.15)
    # sessions 3-4 use date 2's buckets (low: N002, N003; high: N001); N000 is in neither,
    # it is not carried over from date 0
    assert out.loc[DAYS[3], 1] == pytest.approx(0.01) and out.loc[DAYS[3], 2] == pytest.approx(0.01)
    assert out.loc[DAYS[4], 1] == pytest.approx(0.01)


def test_cumulative_spread_compounds() -> None:
    rng = np.random.default_rng(13)
    n_s, n = 120, 50
    daily = _frame(np.vstack([np.full(n, np.nan), 0.01 * rng.standard_normal((n_s - 1, n))]))
    dates = DAYS[0:100:20]
    signal = _frame(rng.standard_normal((n_s, n))).loc[dates]
    labels = _labels(daily, dates, (1, 20))
    out = quantile_analysis(signal, labels, 20, n_quantiles=5, min_names=20)
    assert isinstance(out, QuantileAnalysis)
    spread_daily = out.daily[5] - out.daily[1]
    want = (1 + spread_daily.fillna(0.0)).cumprod() - 1
    pd.testing.assert_series_equal(out.cumulative_spread, want, check_names=False)
    assert out.cumulative_spread.index[0] == DAYS[1]
    pd.testing.assert_series_equal(out.spread, out.by_date[5] - out.by_date[1], check_names=False)
    assert out.spread_mean == pytest.approx(out.spread.mean())
    assert out.horizon == 20 and out.lags == 0  # dates 20 sessions apart, horizon 20


def test_constant_signal_has_no_spread() -> None:
    rng = np.random.default_rng(14)
    daily = _frame(np.vstack([np.full(30, np.nan), 0.01 * rng.standard_normal((59, 30))]))
    dates = DAYS[0:50:5]
    signal = pd.DataFrame(1.0, index=dates, columns=daily.columns)
    out = quantile_analysis(signal, _labels(daily, dates, (5,)), 5, n_quantiles=5, min_names=20)
    assert out.spread.isna().all()
    assert (out.cumulative_spread == 0).all()
    assert math.isnan(out.monotonicity)


def test_quantile_analysis_rejects_labels() -> None:
    rng = np.random.default_rng(15)
    daily = _frame(np.vstack([np.full(30, np.nan), 0.01 * rng.standard_normal((59, 30))]))
    dates = DAYS[0:50:5]
    labels = _labels(daily, dates, (5,))
    with pytest.raises(LookAheadError, match="forward-return label"):
        quantile_analysis(labels.horizon(5), labels, 5)
    with pytest.raises(LookAheadError):
        quantile_buckets(labels.horizon(5).rank(axis=1), 5)
    with pytest.raises(ValueError, match="horizon 21"):
        quantile_analysis(pd.DataFrame(1.0, index=dates, columns=daily.columns), labels, 21)
