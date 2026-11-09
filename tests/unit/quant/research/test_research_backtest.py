"""Stage 17 Task 8 (brief task 4): the vectorised long-short backtest and its costs."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.errors import LookAheadError
from atlas.core.pit import tag_label
from atlas.quant.research import (
    BacktestResult,
    long_short_weights,
    rebalance_dates,
    vector_backtest,
)

DAYS = pd.DatetimeIndex(
    pd.to_datetime(list(TradingCalendar().sessions(date(2021, 1, 4), date(2023, 12, 29)))),
    name="date",
)


def _random(n_sessions: int, n: int, seed: int, vol: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = vol * rng.standard_normal((n_sessions, n))
    r[0] = np.nan
    return pd.DataFrame(r, index=DAYS[:n_sessions], columns=[f"N{i:03d}" for i in range(n)])


def test_zero_signal_gives_zero_return() -> None:
    daily = _random(250, 50, seed=1)
    signal = pd.DataFrame(1.0, index=daily.index[::5], columns=daily.columns)
    out = vector_backtest(signal, daily, rebalance="signal", cost_bps=10.0)
    assert isinstance(out, BacktestResult)
    assert (out.returns == 0).all() and (out.costs == 0).all() and (out.turnover == 0).all()
    assert (out.weights == 0).all().all()
    assert out.summary["total_return"] == 0.0
    assert math.isnan(out.summary["sharpe"])


def test_uninformative_signal_returns_about_zero() -> None:
    daily = _random(500, 100, seed=2)
    rng = np.random.default_rng(3)
    dates = daily.index[::5]
    signal = pd.DataFrame(
        rng.standard_normal((len(dates), 100)), index=dates, columns=daily.columns
    )
    out = vector_backtest(signal, daily, rebalance="monthly", cost_bps=0.0)
    # daily L/S vol ~ 0.01 * sqrt(2/20) = 0.0032; standard error of the mean over 500 days
    # ~ 0.00014: a mean within 4 standard errors of 0
    assert abs(out.gross_returns.mean()) < 4 * 0.0032 / math.sqrt(len(out.returns))
    np.testing.assert_allclose(out.returns, out.gross_returns - out.costs)


def test_weights_equal_and_signal_weighted() -> None:
    row = pd.Series(np.arange(1.0, 11.0), index=[f"N{i}" for i in range(10)])
    eq = long_short_weights(row, n_quantiles=5, weighting="equal", gross=2.0, min_names=5)
    assert list(eq) == [-0.5, -0.5, 0, 0, 0, 0, 0, 0, 0.5, 0.5]
    sw = long_short_weights(row, n_quantiles=5, weighting="signal", gross=2.0, min_names=5)
    # distance of the rank from the median (5.5): 4.5, 3.5 | 3.5, 4.5
    assert sw["N0"] == pytest.approx(-4.5 / 8) and sw["N1"] == pytest.approx(-3.5 / 8)
    assert sw["N8"] == pytest.approx(3.5 / 8) and sw["N9"] == pytest.approx(4.5 / 8)
    assert sw[sw > 0].sum() == pytest.approx(1.0) and sw[sw < 0].sum() == pytest.approx(-1.0)
    half = long_short_weights(row, n_quantiles=5, weighting="equal", gross=1.0, min_names=5)
    assert half.abs().sum() == pytest.approx(1.0)
    with_nan = row.copy()
    with_nan["N3"] = np.nan
    w = long_short_weights(with_nan, n_quantiles=5, weighting="equal", gross=2.0, min_names=5)
    assert w["N3"] == 0.0 and w.sum() == pytest.approx(0.0)
    flat = long_short_weights(
        row.iloc[:3], n_quantiles=5, weighting="equal", gross=2.0, min_names=5
    )
    assert (flat == 0).all()


def _two_names(returns: list[list[float]]) -> pd.DataFrame:
    r = np.array([[np.nan, np.nan], *returns])
    return pd.DataFrame(r, index=DAYS[: len(r)], columns=["A", "B"])


def test_known_returns_with_drift() -> None:
    daily = _two_names([[0.10, 0.20], [0.0, -0.10], [0.05, 0.05]])
    signal = pd.DataFrame([[1.0, 2.0]], index=DAYS[:1], columns=["A", "B"])  # long B, short A
    out = vector_backtest(
        signal, daily, rebalance="signal", cost_bps=0.0, n_quantiles=2, min_names=2
    )
    assert list(out.returns.index) == list(DAYS[:4])
    assert out.returns.iloc[0] == 0.0  # nothing held before the first close
    assert out.returns.iloc[1] == pytest.approx(1.0 * 0.20 - 1.0 * 0.10)
    w_b, w_a = 1.2 / 1.1, -1.1 / 1.1  # drifted weights after session 1
    assert out.returns.iloc[2] == pytest.approx(w_b * -0.10 + w_a * 0.0)
    r2 = w_b * -0.10
    w_b, w_a = w_b * 0.9 / (1 + r2), w_a * 1.0 / (1 + r2)
    assert out.returns.iloc[3] == pytest.approx(w_b * 0.05 + w_a * 0.05)


def test_positions_earn_from_the_next_session() -> None:
    # the signal flips on session 2; session 2's own return is still earned by the old book
    daily = _two_names([[0.0, 0.0], [0.30, -0.30], [0.10, -0.10]])
    signal = pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=DAYS[[0, 2]], columns=["A", "B"])
    out = vector_backtest(
        signal, daily, rebalance="signal", cost_bps=0.0, n_quantiles=2, min_names=2
    )
    assert out.returns.iloc[2] == pytest.approx(-0.60)  # long B (-30%), short A (+30%), old book
    assert out.returns.iloc[3] == pytest.approx(0.20)  # new book: long A (+10%), short B (-10%)
    assert list(out.weights.index) == list(DAYS[[0, 2]])
    assert list(out.weights.loc[DAYS[2]]) == [1.0, -1.0]


def test_costs_are_turnover_times_bps() -> None:
    daily = _two_names([[0.10, 0.20], [0.0, 0.0], [0.0, 0.0]])
    signal = pd.DataFrame([[1.0, 2.0], [2.0, 1.0]], index=DAYS[[0, 2]], columns=["A", "B"])
    out = vector_backtest(
        signal, daily, rebalance="signal", cost_bps=10.0, n_quantiles=2, min_names=2
    )
    assert out.turnover.iloc[0] == pytest.approx(2.0)  # the first book is bought from cash
    assert out.costs.iloc[0] == pytest.approx(2.0 * 10 / 1e4)
    assert out.returns.iloc[0] == pytest.approx(-0.002)
    drifted = np.array([-1.1 / 1.1, 1.2 / 1.1])  # A, B after session 1 (session 2 returns 0)
    turnover = np.abs(np.array([1.0, -1.0]) - drifted).sum()
    assert out.turnover.loc[DAYS[2]] == pytest.approx(turnover)
    assert out.costs.loc[DAYS[2]] == pytest.approx(turnover * 10 / 1e4)
    np.testing.assert_allclose(out.returns, out.gross_returns - out.costs)
    assert out.summary["total_cost"] == pytest.approx(out.costs.sum())


def test_first_turnover_is_gross() -> None:
    daily = _random(60, 40, seed=4)
    signal = pd.DataFrame(
        np.random.default_rng(5).standard_normal((3, 40)),
        index=daily.index[[0, 20, 40]],
        columns=daily.columns,
    )
    for gross in (1.0, 2.0, 3.0):
        out = vector_backtest(signal, daily, rebalance="signal", gross=gross, cost_bps=5.0)
        assert out.turnover.iloc[0] == pytest.approx(gross)
        assert out.weights.iloc[0].abs().sum() == pytest.approx(gross)


def test_rebalance_frequency() -> None:
    dates = DAYS[:130]
    assert list(rebalance_dates(dates, "signal")) == list(dates)
    monthly = rebalance_dates(dates, "monthly")
    assert list(monthly) == [
        d for i, d in enumerate(dates) if i == 0 or d.month != dates[i - 1].month
    ]
    quarterly = rebalance_dates(dates, "quarterly")
    assert dates[-1].month == 7  # 130 sessions from 2021-01-04 reach July
    assert [d.month for d in quarterly] == [1, 4, 7]
    weekly = rebalance_dates(dates, "weekly")
    assert len(weekly) == len({d.isocalendar()[:2] for d in dates})
    with pytest.raises(ValueError, match="rebalance"):
        rebalance_dates(dates, "yearly")  # type: ignore[arg-type]
    daily = _random(130, 30, seed=6)
    signal = pd.DataFrame(
        np.random.default_rng(7).standard_normal((130, 30)), index=dates, columns=daily.columns
    )
    out = vector_backtest(signal, daily, rebalance="monthly")
    assert list(out.weights.index) == list(monthly)
    assert list(out.turnover.index) == list(monthly)


def test_summary_sharpe_and_drawdown() -> None:
    daily = _random(300, 60, seed=8)
    signal = pd.DataFrame(
        np.random.default_rng(9).standard_normal((15, 60)),
        index=daily.index[::20],
        columns=daily.columns,
    )
    out = vector_backtest(signal, daily, rebalance="signal", cost_bps=5.0)
    r = out.returns.to_numpy()
    s = out.summary
    assert s["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(252))
    assert s["ann_return"] == pytest.approx(r.mean() * 252)
    assert s["ann_vol"] == pytest.approx(r.std(ddof=1) * math.sqrt(252))
    wealth = np.cumprod(1 + r)
    # Stage 20: the drawdown starts from the initial NAV of 1 (the first rebalance cost is a loss)
    path = np.concatenate(([1.0], wealth))
    assert s["max_drawdown"] == pytest.approx(-(path / np.maximum.accumulate(path) - 1).min())
    assert s["max_drawdown"] >= 0
    assert s["total_return"] == pytest.approx(wealth[-1] - 1)
    assert s["n_rebalances"] == 15
    assert s["mean_turnover"] == pytest.approx(out.turnover.mean())
    years = len(r) / 252
    assert s["annual_turnover"] == pytest.approx(out.turnover.sum() / years)
    assert s["n_sessions"] == len(r)
    assert {"rebalance", "weighting", "cost_bps", "gross"} <= set(s)


def test_backtest_rejects_labels() -> None:
    daily = _random(60, 30, seed=10)
    with pytest.raises(LookAheadError, match="forward-return label"):
        vector_backtest(tag_label(daily).iloc[::5], daily)
    signal = pd.DataFrame(1.0, index=[pd.Timestamp("2021-01-09")], columns=daily.columns)
    with pytest.raises(ValueError, match="not sessions of the daily returns"):
        vector_backtest(signal, daily)


def test_summary_drawdown_counts_the_first_session() -> None:  # Stage 20 single source
    from atlas.quant.metrics import max_drawdown, sharpe_ratio
    from atlas.quant.research.vector_backtest import _summary

    net = np.array([-0.01, -0.01, 0.03])
    s = _summary(net, np.array([2.0]), np.array([0.001, 0.0, 0.0]), 252)
    assert s["max_drawdown"] == pytest.approx(1 - 0.99**2)  # was 1 - 0.99 before Stage 20
    assert s["max_drawdown"] == max_drawdown(net)
    assert s["sharpe"] == sharpe_ratio(net, risk_free=0.0, periods=252)
    short = _summary(np.array([0.01]), np.array([1.0]), np.array([0.0]), 252)
    assert math.isnan(short["sharpe"]) and math.isnan(short["ann_vol"])
    assert short["max_drawdown"] == 0.0 and short["total_return"] == pytest.approx(0.01)
    empty = _summary(np.array([]), np.array([]), np.array([]), 252)
    assert empty["n_sessions"] == 0
    assert all(math.isnan(empty[k]) for k in ("ann_return", "ann_vol", "sharpe", "max_drawdown"))


# --- Stage 23: the weights-based engine extracted from `vector_backtest` ---------------------


def test_weights_backtest_reproduces_vector_backtest() -> None:  # Stage 23 refactor
    from atlas.quant.research import weights_backtest

    daily = _random(400, 60, seed=21)
    rng = np.random.default_rng(22)
    dates = daily.index[5::7]
    signal = pd.DataFrame(rng.standard_normal((len(dates), 60)), index=dates, columns=daily.columns)
    ref = vector_backtest(signal, daily, rebalance="weekly", cost_bps=7.0, weighting="signal")
    out = weights_backtest(ref.weights, daily, cost_bps=7.0, sessions_per_year=252)
    pd.testing.assert_series_equal(out.returns, ref.returns)
    pd.testing.assert_series_equal(out.gross_returns, ref.gross_returns)
    pd.testing.assert_series_equal(out.costs, ref.costs)
    pd.testing.assert_series_equal(out.turnover, ref.turnover)
    pd.testing.assert_frame_equal(out.weights, ref.weights)
    for key, value in out.summary.items():
        assert ref.summary[key] == value or (math.isnan(value) and math.isnan(ref.summary[key]))


def test_weights_backtest_known_values_and_checks() -> None:  # Stage 23
    from atlas.quant.research import weights_backtest

    idx = DAYS[:4]
    daily = pd.DataFrame({"A": [0.0, 0.10, -0.05, 0.02], "B": [0.0, 0.0, 0.0, 0.0]}, index=idx)
    targets = pd.DataFrame({"A": [0.5, 0.5], "B": [-0.5, -0.5]}, index=idx[[0, 2]])
    out = weights_backtest(targets, daily, cost_bps=10.0, sessions_per_year=252)
    # Session 1 earns 0.5 x 0.10 = 0.05; weights drift to A 0.55/1.05, B -0.5/1.05.
    assert out.gross_returns.iloc[1] == pytest.approx(0.05)
    wa, wb = 0.55 / 1.05, -0.5 / 1.05
    assert out.gross_returns.iloc[2] == pytest.approx(wa * -0.05)
    day2 = wa * -0.05
    wa2, wb2 = wa * 0.95 / (1 + day2), wb / (1 + day2)
    assert out.turnover.iloc[1] == pytest.approx(abs(0.5 - wa2) + abs(-0.5 - wb2))
    assert out.costs.iloc[0] == pytest.approx(1.0 * 10 / 1e4)  # first book from cash
    assert out.summary["cost_bps"] == 10.0
    with pytest.raises(ValueError, match="cost_bps"):
        weights_backtest(targets, daily, cost_bps=-1.0, sessions_per_year=252)
    with pytest.raises(ValueError, match="not sessions"):
        weights_backtest(
            targets.set_axis([pd.Timestamp("2021-01-02"), idx[2]]),
            daily,
            cost_bps=0.0,
            sessions_per_year=252,
        )
    with pytest.raises(ValueError, match="increasing"):
        weights_backtest(targets.iloc[::-1], daily, cost_bps=0.0, sessions_per_year=252)
    with pytest.raises(ValueError, match="no rebalance"):
        weights_backtest(targets.iloc[:0], daily, cost_bps=0.0, sessions_per_year=252)
    with pytest.raises(LookAheadError):
        weights_backtest(tag_label(targets), daily, cost_bps=0.0, sessions_per_year=252)
