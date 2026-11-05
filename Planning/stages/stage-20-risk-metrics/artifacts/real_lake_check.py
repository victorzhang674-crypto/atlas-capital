"""Stage 20 real-lake check (read only): the metrics library on real S&P 500 data.

Decision day 2025-01-02 (08:00 New York): the view knows bars up to the close of 2024-12-31.
1. SPY over the last 1,260 sessions (2020-2024): performance summary, the worst drawdown
   episodes (the 2020 crash and the 2022 bear market), rolling Sharpe, VaR / CVaR by every
   series method, horizon scaling.
2. A seeded long/short book of point-in-time S&P 500 members (30 long at +2% NAV, 20 short at
   -2% NAV; gross 100%, net +20%): historical-simulation VaR / CVaR on 504 sessions, normal and
   Monte Carlo VaR from a Stage 18 Ledoit-Wolf covariance, Euler risk contributions,
   benchmark-relative metrics vs SPY, liquidity at 10% of 20-day ADV for a $100M NAV
   (DataView.adv), exposures with GICS sector nets and betas to SPY.
Invariants asserted on the way (CVaR >= VaR, contributions sum to the vol, net = long + short,
no look-ahead). Cross-checks rebuild a few numbers with plain pandas / numpy.

Run from the repository root:
    ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data MPLCONFIGDIR=$TMPDIR/mpl \
    UV_CACHE_DIR=$TMPDIR/uv-cache-s20 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-20-risk-metrics/artifacts/real_lake_check.py
"""

import math
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.core.errors import DataError
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant import metrics as m
from atlas.quant.risk_model import covariance_estimator

DAY = date(2025, 1, 2)
NAV = 100_000_000.0  # USD
lake = Lake(Path.home() / "Projects/atlas-capital/data")
t0 = time.perf_counter()
view = DataView(lake, DAY)
print(f"# Stage 20 real-lake check · decision day {DAY} · lake {lake.root}")

# --- 1. SPY ----------------------------------------------------------------------------------
spy = view.returns(["SPY"], 1260)["SPY"]
assert spy.index[-1].date() < DAY, "look-ahead: a return dated on or after the decision day"
print(f"\n## SPY: {spy.notna().sum()} returns {spy.index[0].date()} .. {spy.index[-1].date()} "
      f"(last < decision day: ok)")
summary = m.performance_summary(spy)
for key, value in summary.items():
    shown = f"{value:.6f}" if isinstance(value, float) else str(value)
    print(f"  {key:<24} {shown}")
wealth = (1 + spy.dropna()).cumprod()
path = np.concatenate(([1.0], wealth.to_numpy()))
plain_mdd = float(-(path / np.maximum.accumulate(path) - 1).min())
plain_sharpe = float(spy.mean() / spy.std(ddof=1) * math.sqrt(252))
assert math.isclose(summary["max_drawdown"], plain_mdd, rel_tol=1e-12)
assert math.isclose(summary["sharpe"], plain_sharpe, rel_tol=1e-12)
print(f"  cross-check (plain pandas): max_drawdown {plain_mdd:.6f}, sharpe {plain_sharpe:.6f}: ok")
episodes = sorted(m.drawdown_periods(spy), key=lambda p: p.depth, reverse=True)[:3]
print("  worst drawdown episodes (peak -> trough -> recovery, depth, sessions peak->trough, "
      "duration):")
for p in episodes:
    rec = p.recovery.date() if p.recovery is not None else "not recovered"
    peak = p.peak.date() if p.peak is not None else "start"
    print(f"    {peak} -> {p.trough.date()} -> {rec}  {p.depth:.4f}  {p.to_trough}  {p.duration}")
roll = m.rolling_sharpe(spy, 252)
print(f"  rolling 252-session Sharpe: min {roll.min():.3f} max {roll.max():.3f} "
      f"last {roll.iloc[-1]:.3f}")
print("  VaR / CVaR of SPY daily returns (VaR 95%, CVaR 97.5%, positive losses):")
for method in m.SERIES_METHODS:
    res = m.var_cvar(spy, method=method)
    res10 = m.var_cvar(spy, method=method, horizon=10)
    assert res.cvar >= res.var and res10.cvar >= res10.var
    print(f"    {method:<15} 1d VaR {res.var:.4%} CVaR {res.cvar:.4%} · 10d VaR {res10.var:.4%} "
          f"CVaR {res10.cvar:.4%} {list(res.adjustments)}")

# --- 2. a long/short book --------------------------------------------------------------------
members = view.universe("sp500")
rng = np.random.default_rng(20)
rets_all = view.returns(members, 504)
covered = [s for s in members if rets_all[s].notna().sum() >= 480]
picks = list(rng.choice(covered, size=50, replace=False))
weights = pd.Series([0.02] * 30 + [-0.02] * 20, index=picks, name="weight")
rets = rets_all[picks]
print(f"\n## Book: {len(members)} members, {len(covered)} with >= 480 of 504 returns; "
      f"30 long / 20 short at 2% NAV ({time.perf_counter() - t0:.1f} s so far)")

hist = m.portfolio_returns(weights, rets)
print(f"  historical simulation: {len(hist)} sessions ({504 - len(hist)} dropped for a missing "
      f"held return)")
for method in m.SERIES_METHODS:
    res = m.portfolio_var(weights, returns=rets, method=method)
    assert res.cvar >= res.var
    print(f"    {method:<15} VaR95 {res.var:.4%} CVaR97.5 {res.cvar:.4%} "
          f"(limits: VaR <= 1.50%, CVaR <= 2.50%)")
cov = covariance_estimator("ledoit_wolf").estimate(rets)
if cov.dropped:
    print(f"  Ledoit-Wolf dropped: {dict(cov.dropped)}")
held = weights[weights.index.isin(cov.symbols)]
normal = m.portfolio_var(held, cov=cov.cov, method="normal")
t1 = time.perf_counter()
mc = m.portfolio_var(held, cov=cov.cov, method="monte_carlo", seed=20)
mc_secs = time.perf_counter() - t1
assert mc.cvar >= mc.var
print(f"    normal (LW cov)  VaR95 {normal.var:.4%} CVaR97.5 {normal.cvar:.4%}")
print(f"    monte_carlo      VaR95 {mc.var:.4%} CVaR97.5 {mc.cvar:.4%} "
      f"({mc.n_obs} scenarios, {mc_secs:.2f} s; |MC/normal - 1| = {abs(mc.var / normal.var - 1):.3%})")

rc = m.risk_contributions(held, cov.cov)
assert math.isclose(rc.component.sum(), rc.vol, rel_tol=1e-9)
assert math.isclose(rc.percent.sum(), 1.0, rel_tol=1e-9)
top = rc.percent.sort_values(ascending=False)
print(f"  contributions: daily vol {rc.vol:.4%} (annual {rc.annual_vol():.2%}); "
      f"sum of components = vol: ok; top 3 % of risk: "
      + ", ".join(f"{s} {v:.1%}" for s, v in top.head(3).items())
      + f"; most diversifying: {top.index[-1]} {top.iloc[-1]:.1%}")

spy_504 = view.returns(["SPY"], 504)["SPY"]
rel = m.relative_summary(hist, spy_504)
print("  relative to SPY: " + ", ".join(
    f"{k} {v:.4f}" if isinstance(v, float) else f"{k} {v}" for k, v in rel.items()))
perf = m.performance_summary(hist)
print(f"  book: annualised return {perf['annualised_return']:.2%}, vol "
      f"{perf['annualised_vol']:.2%}, Sharpe {perf['sharpe']:.3f}, max drawdown "
      f"{perf['max_drawdown']:.2%}")

liq = m.liquidity_from_view(view, weights * NAV)
print(f"  liquidity ($100M NAV, 10% of 20-day ADV): max days {liq.max_days:.3f}; names liquid "
      f"{dict(liq.names_liquid)}; gross liquid {dict(liq.gross_liquid)}; no ADV {liq.no_adv}")
adv = view.adv(picks)["adv_usd"]
plain_days = (weights * NAV).abs() / (0.10 * adv)
assert np.allclose(liq.days.to_numpy(), plain_days.to_numpy(), rtol=1e-12)
print("  cross-check (plain pandas) days to liquidate: ok")

sectors = {}
for s in picks:
    try:
        sectors[s] = view.instrument(s).sector
    except DataError:
        sectors[s] = None
betas = pd.Series({s: m.beta(rets[s], spy_504) for s in picks})
exp = m.exposure_summary(weights, betas=betas, sectors=pd.Series(sectors, dtype=object))
assert math.isclose(exp.net, exp.long + exp.short, abs_tol=1e-12)
assert exp.sector_net is not None
print(f"  exposures: gross {exp.gross:.2%} net {exp.net:.2%} long {exp.long:.2%} short "
      f"{exp.short:.2%} ({exp.n_long} / {exp.n_short}); beta-adjusted net "
      f"{exp.beta_adjusted_net:.4f}; unclassified {exp.unclassified}")
print("  sector nets: " + ", ".join(f"{k} {v:+.0%}" for k, v in exp.sector_net.items()))
print(f"\ntotal {time.perf_counter() - t0:.1f} s · all invariants held")
