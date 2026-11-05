"""Stage 20 post-DONE audit: the metrics library on the real data lake (read only).

Needs a populated lake: set `ATLAS__DATA__LAKE_PATH` (skipped when the lake or its price /
membership tables are missing, as in CI). Decision day 2025-01-02: SPY over 1,260 sessions and
a seeded 30 long / 20 short book of point-in-time S&P 500 members over 504 sessions. Every
number is re-derived by plain pandas / numpy (a different code path), and the invariants of the
library are checked on real, fat-tailed data. The fuller printed evidence run is
`Planning/stages/stage-20-risk-metrics/artifacts/real_lake_check.py`.
"""

import math
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant import metrics as m
from atlas.quant.risk_model import covariance_estimator

pytestmark = pytest.mark.integration

DAY = date(2025, 1, 2)


@pytest.fixture(scope="module")
def view() -> DataView:
    raw = os.environ.get("ATLAS__DATA__LAKE_PATH")
    if not raw or not Path(raw).expanduser().exists():
        pytest.skip("ATLAS__DATA__LAKE_PATH does not point at a populated lake")
    lake = Lake(Path(raw).expanduser())
    for table in ("prices_daily", "instruments", "index_membership"):
        if not lake.has_table(table):
            pytest.skip(f"the lake has no {table} table")
    return DataView(lake, DAY)


@pytest.fixture(scope="module")
def spy(view: DataView) -> pd.Series:
    out = view.returns(["SPY"], 1260)["SPY"].dropna()
    assert len(out) >= 1250 and out.index[-1].date() < DAY  # no look-ahead
    return out


@pytest.fixture(scope="module")
def book(view: DataView) -> tuple[pd.Series, pd.DataFrame]:
    members = view.universe("sp500")
    rets = view.returns(members, 504)
    covered = [s for s in members if rets[s].notna().sum() >= 480]
    picks = list(np.random.default_rng(20).choice(covered, size=50, replace=False))
    weights = pd.Series([0.02] * 30 + [-0.02] * 20, index=picks)
    return weights, rets[picks]


def test_spy_performance_equals_plain_pandas(spy: pd.Series) -> None:
    s = m.performance_summary(spy)
    wealth = np.concatenate(([1.0], (1 + spy).cumprod().to_numpy()))
    assert s["max_drawdown"] == pytest.approx(
        -(wealth / np.maximum.accumulate(wealth) - 1).min(), rel=1e-12
    )
    assert s["sharpe"] == pytest.approx(spy.mean() / spy.std(ddof=1) * math.sqrt(252), rel=1e-12)
    assert s["annualised_return"] == pytest.approx(wealth[-1] ** (252 / len(spy)) - 1, rel=1e-12)
    assert s["skewness"] == pytest.approx(float(stats.skew(spy)), rel=1e-10)
    assert s["excess_kurtosis"] == pytest.approx(float(stats.kurtosis(spy)), rel=1e-10)
    worst = max(m.drawdown_periods(spy), key=lambda p: p.depth)
    assert worst.depth == pytest.approx(s["max_drawdown"])
    assert worst.trough.year == 2020 and worst.recovered  # the March 2020 crash


def test_spy_var_cvar_invariants(spy: pd.Series) -> None:
    for method in ("historical", "normal"):
        for h in (1, 10):
            res = m.var_cvar(spy, method=method, horizon=h)
            assert 0 < res.var <= res.cvar < 0.5, (method, h)
    # SPY 2020-2024 (skew -0.54, excess kurtosis 11.5) is outside the Cornish-Fisher domain:
    # refused instead of an unreliable number (independent review, 2026-11-05)
    with pytest.raises(DataError, match="Cornish-Fisher"):
        m.var_cvar(spy, method="cornish_fisher")
    hist = m.var_cvar(spy)
    assert hist.var == pytest.approx(-float(np.quantile(spy, 0.05)), rel=1e-12)
    q = np.quantile(spy, 0.025)
    assert hist.cvar == pytest.approx(-float(spy[spy <= q].mean()), rel=1e-12)


def test_book_var_contributions_and_relative(
    book: tuple[pd.Series, pd.DataFrame], spy: pd.Series
) -> None:
    weights, rets = book
    hist = m.portfolio_returns(weights, rets)
    plain = (rets.dropna() * weights).sum(axis=1)
    np.testing.assert_allclose(hist.to_numpy(), plain.to_numpy(), rtol=1e-12)
    cov = covariance_estimator("ledoit_wolf").estimate(rets)
    held = weights[weights.index.isin(cov.symbols)]
    normal = m.portfolio_var(held, cov=cov.cov, method="normal")
    mc = m.portfolio_var(held, cov=cov.cov, method="monte_carlo", seed=20)
    assert mc.var == pytest.approx(normal.var, rel=0.03)
    assert mc.cvar >= mc.var and normal.cvar >= normal.var
    rc = m.risk_contributions(held, cov.cov)
    assert rc.component.sum() == pytest.approx(rc.vol, rel=1e-9)
    sigma_w = cov.cov.to_numpy() @ held.reindex(cov.symbols).fillna(0).to_numpy()
    assert rc.vol == pytest.approx(math.sqrt(held.reindex(cov.symbols).fillna(0) @ sigma_w))
    spy_504 = spy.iloc[-504:]
    beta = m.beta(hist, spy_504)
    joined = pd.concat([hist, spy_504], axis=1).dropna()
    plain_beta = joined.cov().iloc[0, 1] / joined.iloc[:, 1].var()
    assert beta == pytest.approx(plain_beta, rel=1e-10)
    betas = pd.Series({s: m.beta(rets[s], spy_504) for s in weights.index})
    exp = m.exposure_summary(weights, betas=betas)
    assert exp.beta_adjusted_net == pytest.approx(beta, rel=1e-6)  # beta is linear in weights
    assert exp.gross == pytest.approx(1.0) and exp.net == pytest.approx(0.2)


def test_book_liquidity_equals_plain_pandas(
    view: DataView, book: tuple[pd.Series, pd.DataFrame]
) -> None:
    weights, _ = book
    nav = 100_000_000.0
    prof = m.liquidity_from_view(view, weights * nav)
    adv = view.adv(list(weights.index))["adv_usd"]
    days = (weights * nav).abs() / (0.10 * adv)
    np.testing.assert_allclose(prof.days.to_numpy(), days.to_numpy(), rtol=1e-12)
    for h in (1, 3, 5):
        assert prof.names_liquid[h] == pytest.approx(float((days <= h).mean()))
        sold = np.minimum((weights * nav).abs(), h * 0.10 * adv).sum()
        assert prof.gross_liquid[h] == pytest.approx(sold / nav, rel=1e-12)
