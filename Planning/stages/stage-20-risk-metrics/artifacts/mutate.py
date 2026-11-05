"""Stage 20 mutation checks: apply one deliberate bug at a time to the metrics library (every
formula, convention, default, fail-safe path and the Stage 17 summary that now uses it), run
the tests that should catch it, and restore the file. Every mutant must make a test fail.

Run from the repository root:
    MPLCONFIGDIR=$TMPDIR/mpl UV_CACHE_DIR=$TMPDIR/uv-cache-s20 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-20-risk-metrics/artifacts/mutate.py [--check] [--only <fragment> ...]
(`--check` only verifies that every pattern is found exactly once.)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
M = "src/atlas/quant/metrics/"
T = "tests/unit/quant/metrics/"
COM, PERF, ROLL, REL, VAR, CON, LIQ, EXP, PKG = (
    T + "test_metrics_common.py",
    T + "test_metrics_performance.py",
    T + "test_metrics_rolling.py",
    T + "test_metrics_relative.py",
    T + "test_metrics_var.py",
    T + "test_metrics_contributions.py",
    T + "test_metrics_liquidity.py",
    T + "test_metrics_exposure.py",
    T + "test_metrics_package.py",
)
BT = "tests/unit/quant/research/test_research_backtest.py"

MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # (name, file, old, new, tests)
    # --- shared helpers ---------------------------------------------------------------------
    ("NaN not dropped", M + "_common.py", "    out = out.dropna()\n", "", [COM]),
    ("return below -1 accepted", M + "_common.py",
     "    if not pnl and (data < -1.0).any():", "    if False:", [COM]),
    ("unsorted series accepted", M + "_common.py",
     "        if not series.index.is_monotonic_increasing:", "        if False:", [COM]),
    ("min_obs ignored", M + "_common.py", "    if len(out) < min_obs:", "    if False:", [COM]),
    ("annual-to-daily arithmetic", M + "_common.py",
     "return float((1.0 + rate_annual) ** (1.0 / periods) - 1.0)", "return float(rate_annual / periods)", [COM, PERF]),
    ("risk-free series gaps filled", M + "_common.py", "        if missing.any():", "        if False:", [COM, PERF]),
    ("align is an outer join", M + "_common.py",
     "    common = r.index.intersection(b.index, sort=False)", "    common = r.index.union(b.index)", [COM, REL]),
    ("ratio by zero gives inf", M + "_common.py",
     "    if denominator == 0.0 or not math.isfinite(denominator):\n        return math.nan",
     "    if denominator == 0.0:\n        return math.inf", [COM, PERF]),
    ("covariance symmetry unchecked", M + "_common.py",
     "    if not np.allclose(matrix, matrix.T, rtol=0.0, atol=_SYMMETRY_RTOL * max(scale, 1e-300)):",
     "    if False:", [COM, CON]),
    ("covariance PSD unchecked", M + "_common.py",
     "    if eig[0] < -_PSD_RTOL * max(float(eig[-1]), 0.0):", "    if False:", [COM, CON, VAR]),
    ("unknown held name ignored", M + "_common.py", "        if unknown and strict:", "        if False:", [COM, CON, VAR]),
    # --- performance ------------------------------------------------------------------------
    ("CAGR uses n not P", M + "performance.py",
     "    return float(growth ** (p / len(r)) - 1.0)", "    return float(growth ** (len(r) / p) - 1.0)", [PERF]),
    ("vol with ddof 0", M + "performance.py",
     "    return float(r.std(ddof=1) * math.sqrt(periods_per_year(periods, config)))",
     "    return float(r.std(ddof=0) * math.sqrt(periods_per_year(periods, config)))", [PERF]),
    ("Sharpe ignores the risk-free rate", M + "performance.py",
     "    excess = r.to_numpy() - risk_free_daily(risk_free, r.index, p, config)\n    return nan_if_zero",
     "    excess = r.to_numpy()\n    return nan_if_zero", [PERF]),
    ("Sharpe annualised by P", M + "performance.py",
     "    return nan_if_zero(float(excess.mean()) * math.sqrt(p), float(excess.std(ddof=1)))",
     "    return nan_if_zero(float(excess.mean()) * p, float(excess.std(ddof=1)))", [PERF]),
    ("downside risk over losing sessions only", M + "performance.py",
     "float(np.mean(shortfall**2))", "float(np.mean(shortfall[shortfall < 0] ** 2))", [PERF]),
    ("Sortino not annualised in the numerator", M + "performance.py",
     "    return nan_if_zero(float(np.mean(r - req)) * p, down)",
     "    return nan_if_zero(float(np.mean(r - req)), down)", [PERF]),
    ("Omega threshold annual used per session", M + "performance.py",
     "    tau = annual_to_daily(annual, p)", "    tau = annual", [PERF]),
    ("drawdown ignores the initial NAV", M + "performance.py",
     "    wealth = np.concatenate(([1.0], np.cumprod(1.0 + r)))", "    wealth = np.concatenate(([1.0 + r[0]], np.cumprod(1.0 + r)))", [PERF]),
    ("max drawdown sign flipped", M + "performance.py",
     "    return float(-_drawdowns(r).min()) + 0.0", "    return float(_drawdowns(r).min()) + 0.0", [PERF]),
    ("episode duration excludes recovery", M + "performance.py",
     "                duration=(end if recovered else n) - peak,", "                duration=(end - 1 if recovered else n) - peak,", [PERF]),
    ("trough is the first underwater session", M + "performance.py",
     "        trough = peak + 1 + int(np.argmin(dd[peak + 1 : end]))", "        trough = peak + 1", [PERF]),
    ("ulcer index is mean absolute drawdown", M + "performance.py",
     "    return float(math.sqrt(float(np.mean(dd**2))))", "    return float(np.mean(np.abs(dd)))", [PERF]),
    ("kurtosis not excess", M + "performance.py", "    return ratio - 3.0", "    return ratio", [PERF]),
    ("skewness with the sample std", M + "performance.py",
     "    return nan_if_zero(float(np.mean(d**3)), m2**1.5)", "    return nan_if_zero(float(np.mean(d**3)), float(np.std(r, ddof=1)) ** 3)", [PERF]),
    ("hit rate counts zero returns", M + "performance.py", "    return float(np.mean(r > 0.0))", "    return float(np.mean(r >= 0.0))", [PERF]),
    ("tail ratio quantiles swapped", M + "performance.py",
     "    upper, lower = np.quantile(r, [1.0 - t, t])", "    lower, upper = np.quantile(r, [1.0 - t, t])", [PERF]),
    ("calmar uses arithmetic mean", M + "performance.py",
     "    return nan_if_zero(annualised_return(r, periods=periods, config=config), max_drawdown(r))",
     "    return nan_if_zero(annualised_mean(r, periods=periods, config=config), max_drawdown(r))", [PERF]),
    ("rolling metric window off by one", M + "performance.py",
     "        values[end - 1] = metric(r.iloc[end - w : end])", "        values[end - 1] = metric(r.iloc[end - w + 1 : end])", [ROLL]),
    ("rolling Sharpe ignores the risk-free rate", M + "performance.py",
     "    excess = r - risk_free_daily(risk_free, r.index, p, config)\n    mean = excess.rolling(w).mean()",
     "    excess = r\n    mean = excess.rolling(w).mean()", [ROLL]),
    ("rolling vol with ddof 0", M + "performance.py",
     "    out = r.rolling(w).std(ddof=1) * math.sqrt(p)", "    out = r.rolling(w).std(ddof=0) * math.sqrt(p)", [ROLL]),
    ("summary Sortino swapped for Sharpe", M + "performance.py",
     '        "sortino": sortino_ratio(r, periods=p),', '        "sortino": sharpe_ratio(r, periods=p),', [ROLL]),
    # --- relative ---------------------------------------------------------------------------
    ("beta not on excess returns", M + "relative.py",
     "    return r.to_numpy() - rf, b.to_numpy() - rf, p", "    return r.to_numpy() - rf, b.to_numpy(), p", [REL]),
    ("beta with mixed ddof", M + "relative.py",
     "    var_b = float(np.var(be, ddof=1))", "    var_b = float(np.var(be, ddof=0))", [REL]),
    ("alpha annualised arithmetically", M + "relative.py",
     "    return float((1.0 + daily) ** p - 1.0)", "    return float(daily * p)", [REL]),
    ("tracking error not annualised", M + "relative.py",
     "    return float(active.std(ddof=1) * math.sqrt(periods_per_year(periods, config)))",
     "    return float(active.std(ddof=1))", [REL]),
    ("up capture over benchmark >= 0", M + "relative.py",
     "    return _capture(r, b, b.to_numpy() > 0.0, periods_per_year(periods, config))",
     "    return _capture(r, b, b.to_numpy() >= 0.0, periods_per_year(periods, config))", [REL]),
    ("down capture inverted", M + "relative.py",
     "    return _capture(r, b, b.to_numpy() < 0.0, periods_per_year(periods, config))",
     "    return 1.0 / _capture(r, b, b.to_numpy() < 0.0, periods_per_year(periods, config))", [REL]),
    ("rolling beta ignores the risk-free rate", M + "relative.py",
     "    re, be = r - rf, b - rf\n", "    re, be = r, b\n", [REL]),
    # --- VaR / CVaR -------------------------------------------------------------------------
    ("historical VaR at the confidence quantile", M + "var.py",
     "    return float(-np.quantile(x, 1.0 - conf))", "    return float(-np.quantile(x, conf))", [VAR]),
    ("historical CVaR excludes the quantile observation", M + "var.py",
     'k = max(int(np.searchsorted(ordered, q, side="right")), 1)', 'k = max(int(np.searchsorted(ordered, q, side="left")), 1)', [VAR]),
    ("normal CVaR without the mean", M + "var.py",
     "    return float(-mu + sigma * _N.pdf(z) / (1.0 - conf))", "    return float(sigma * _N.pdf(z) / (1.0 - conf))", [VAR]),
    ("Cornish-Fisher S^2 term sign", M + "var.py",
     "        - (2.0 * z**3 - 5.0 * z) * skew**2 / 36.0", "        + (2.0 * z**3 - 5.0 * z) * skew**2 / 36.0", [VAR]),
    ("Cornish-Fisher CVaR kurtosis term dropped", M + "var.py",
     " + kurt * (z**2 - 1.0) / 24.0 - skew**2", " - skew**2", [VAR]),
    ("Cornish-Fisher floor removed", M + "var.py", "    if cvar < var:\n        return var, True", "    if False:\n        return var, True", [VAR]),
    ("CF moments not aggregated over the horizon", M + "var.py",
     "    skew_h, kurt_h = skew / math.sqrt(h), kurt / h", "    skew_h, kurt_h = skew, kurt", [VAR]),
    ("horizon scaled linearly", M + "var.py",
     "    return float(value * math.sqrt(_horizon(horizon)))", "    return float(value * _horizon(horizon))", [VAR]),
    ("overlapping returns summed", M + "var.py",
     "agg = x.rolling(h).sum() if pnl else (1.0 + x).rolling(h).apply(np.prod, raw=True) - 1.0",
     "agg = x.rolling(h).sum()", [VAR]),
    ("zero-mean switch ignored", M + "var.py",
     "    mu = 0.0 if mc.zero_mean else float(data.mean())", "    mu = 0.0", [VAR]),
    ("VaR minimum observations ignored", M + "var.py", "    if len(x) < mc.var_min_obs:", "    if False:", [VAR]),
    ("VaR confidence from the CVaR limit", M + "var.py",
     "    vc = cfg.limits.var_confidence if var_confidence is None else var_confidence",
     "    vc = cfg.limits.cvar_confidence if var_confidence is None else var_confidence", [VAR]),
    ("portfolio sessions with a missing held return kept", M + "var.py",
     "    complete = frame.notna().all(axis=1)", "    complete = frame.notna().any(axis=1)", [VAR]),
    ("Monte Carlo horizon not applied", M + "var.py",
     "    return float(mu @ w) * h + math.sqrt(h) * out", "    return float(mu @ w) * h + out", [VAR]),
    ("Monte Carlo loading without the square root", M + "var.py",
     "    loading = vecs @ (np.sqrt(np.clip(vals, 0.0, None)) * (vecs.T @ w))",
     "    loading = vecs @ (np.clip(vals, 0.0, None) * (vecs.T @ w))", [VAR]),
    ("Monte Carlo seed not required", M + "var.py",
     "    if (seed is None) == (rng is None):\n        raise ValueError(\"pass exactly one of seed or rng",
     "    if seed is not None and rng is not None:\n        raise ValueError(\"pass exactly one of seed or rng", [VAR]),
    ("parametric covariance VaR without the horizon", M + "var.py",
     "        mu_h, sigma_h = mu * h, sigma * math.sqrt(h)\n        return VaRResult(",
     "        mu_h, sigma_h = mu, sigma\n        return VaRResult(", [VAR]),
    # --- contributions ----------------------------------------------------------------------
    ("marginal not divided by vol", M + "contributions.py",
     "    marginal = sigma_w / vol if vol > 0.0 else np.zeros(len(w))", "    marginal = sigma_w if vol > 0.0 else np.zeros(len(w))", [CON]),
    ("component without the weight", M + "contributions.py", "    component = w * marginal", "    component = marginal", [CON]),
    ("percent of variance not vol", M + "contributions.py",
     "    percent = component / vol if vol > 0.0", "    percent = component / vol**2 if vol > 0.0", [CON]),
    ("zero portfolio percent zero", M + "contributions.py",
     "np.full(len(w), np.nan)", "np.zeros(len(w))", [CON]),
    # --- liquidity --------------------------------------------------------------------------
    ("participation ignored", M + "liquidity.py", "    capacity = rate * _adv_on(pos, adv)", "    capacity = _adv_on(pos, adv)", [LIQ]),
    ("shorts not by magnitude", M + "liquidity.py",
     "    size = np.abs(pos.to_numpy())\n    usable", "    size = pos.to_numpy()\n    usable", [LIQ]),
    ("missing ADV counted as liquid", M + "liquidity.py",
     "    days = np.where(usable, size / np.where(usable, capacity, 1.0), math.inf)",
     "    days = np.where(usable, size / np.where(usable, capacity, 1.0), 0.0)", [LIQ]),
    ("names liquid strict inequality", M + "liquidity.py",
     "np.mean(days.to_numpy()[held] <= h)", "np.mean(days.to_numpy()[held] < h)", [LIQ]),
    ("gross liquid counts whole positions only", M + "liquidity.py",
     "np.minimum(size, h * capacity).sum()", "np.where(size <= h * capacity, size, 0.0).sum()", [LIQ]),
    ("default participation from the order limit", M + "liquidity.py",
     "    p = cfg.limits.liquidation_adv_frac if value is None else value",
     "    p = cfg.limits.max_order_adv_frac if value is None else value", [LIQ]),
    # --- exposure ---------------------------------------------------------------------------
    ("short reported as a magnitude", M + "exposure.py",
     "    short = float(values[values < 0].sum())", "    short = float(-values[values < 0].sum())", [EXP]),
    ("gross is the net", M + "exposure.py", "        gross=float(np.abs(values).sum()),", "        gross=float(values.sum()),", [EXP]),
    ("missing beta treated as zero", M + "exposure.py", "    if bad.any():", "    if False:", [EXP]),
    ("unclassified names dropped from sector nets", M + "exposure.py",
     "    labels = labels.where(labels.notna() & (labels.astype(str).str.len() > 0), UNCLASSIFIED)",
     "    labels = labels.where(labels.notna(), UNCLASSIFIED)", [EXP]),
    # --- Stage 17 summary on the library ----------------------------------------------------
    ("backtest summary Sharpe with the default risk-free", "src/atlas/quant/research/vector_backtest.py",
     "sharpe_ratio(net, risk_free=0.0, periods=periods)", "sharpe_ratio(net, periods=periods) * 1.01", [BT]),
    ("backtest summary drawdown on positive returns only", "src/atlas/quant/research/vector_backtest.py",
     '"max_drawdown": max_drawdown(net) if n else nan,', '"max_drawdown": max_drawdown(net[1:]) if n > 1 else 0.0,', [BT]),
]


def run(tests: list[str]) -> bool:
    """True when the tests fail (the mutant is caught)."""
    cmd = ["uv", "run", "pytest", "-q", "-x", "-p", "no:logging", "-p", "no:cacheprovider", *tests]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).returncode != 0


def main() -> int:
    check_only = "--check" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1 :] if "--only" in sys.argv else []
    caught = 0
    chosen = [m for m in MUTANTS if not only or any(o in m[0] for o in only)]
    for name, rel, old, new, tests in chosen:
        path = ROOT / rel
        original = path.read_text()
        if original.count(old) != 1:
            print(f"SETUP ERROR  {name}: pattern found {original.count(old)} times in {rel}")
            return 2
        if check_only:
            continue
        path.write_text(original.replace(old, new))
        try:
            ok = run(tests)
        finally:
            path.write_text(original)
        caught += ok
        print(f"{'caught  ' if ok else 'SURVIVED'}  {name}", flush=True)
    if check_only:
        print(f"all {len(MUTANTS)} patterns found exactly once")
        return 0
    print(f"\n{caught}/{len(chosen)} mutants caught")
    return 0 if caught == len(chosen) else 1


if __name__ == "__main__":
    sys.exit(main())
