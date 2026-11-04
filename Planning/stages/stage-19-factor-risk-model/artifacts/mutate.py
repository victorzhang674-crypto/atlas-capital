"""Stage 19 mutation checks: apply one deliberate bug at a time to the stage's code (the sector
constraint and WLS weights, the standardisation, EWMA weights and Newey-West terms, PSD repair,
the shrinkage formula, the risk decomposition, the ETF window and degrees of freedom, the
point-in-time availability and look-ahead lags), run the tests that should catch it, and
restore the file. Every mutant must make a test fail.

Run from the repository root:
    MPLCONFIGDIR=$TMPDIR/mpl UV_CACHE_DIR=$TMPDIR/uv-cache-s19 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-19-factor-risk-model/artifacts/mutate.py [--check] [--only <name fragment> ...]
(`--check` only verifies that every pattern is found exactly once.)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
R = "src/atlas/quant/risk_model/"
T = "tests/unit/quant/risk_model/"
REG, SPEC, MOD, ANA, ETF, STORE, LOAD = (
    T + "test_factor_risk_regression.py",
    T + "test_factor_risk_specific.py",
    T + "test_factor_risk_model.py",
    T + "test_factor_risk_analytics.py",
    T + "test_factor_risk_etf.py",
    T + "test_factor_risk_store.py",
    T + "test_factor_risk_loader.py",
)

MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # (name, file, old, new, tests)
    # --- regression -------------------------------------------------------------------------
    ("constraint not applied", R + "regression.py", "    if np.any(ca != 0):", "    if False:", [REG]),
    ("WLS weights ignored (OLS)", R + "regression.py", "    root = np.sqrt(w)", "    root = np.ones_like(w)", [REG]),
    ("rank check removed", R + "regression.py", "    if rank < basis.shape[1]:", "    if False:", [REG]),
    ("residuals shrunk", R + "regression.py", "    resid = y - xa @ (basis @ g)", "    resid = (y - xa @ (basis @ g)) * 0.9", [REG]),
    ("sector shares by name count, not cap", R + "regression.py",
     "out[sector] = float(caps[exposures[sector] != 0].sum()) / total",
     "out[sector] = float((exposures[sector] != 0).sum()) / len(caps)", [REG]),
    ("too-few-names check removed", R + "regression.py", "    if n <= k:", "    if False:", [REG]),
    # --- specific risk ----------------------------------------------------------------------
    ("LEAK specific EWMA weights reversed", R + "specific.py",
     "w = 0.5 ** ((t - 1 - np.arange(t)) / half_life)", "w = 0.5 ** (np.arange(t) / half_life)", [SPEC]),
    ("specific EWMA not squared", R + "specific.py",
     "squares = np.where(seen, values, 0.0) ** 2", "squares = np.abs(np.where(seen, values, 0.0))", [SPEC]),
    ("specific EWMA counts missing residuals in the weights", R + "specific.py",
     "den = w @ seen", "den = np.full(values.shape[1], w.sum())", [SPEC]),
    ("shrinkage denominator ignores q", R + "specific.py",
     "denom = delta + q * gap", "denom = delta + gap", [SPEC]),
    ("shrinkage target is the mean", R + "specific.py", 'transform("median")', 'transform("mean")', [SPEC]),
    ("sector dispersion with ddof 1", R + "specific.py",
     "float(np.std(s.to_numpy()))", "float(np.std(s.to_numpy(), ddof=1))", [SPEC]),
    ("vol floor removed", R + "specific.py", "final = shrunk.clip(lower=floor_vol)", "final = shrunk", [SPEC]),
    ("short-history drop disabled", R + "specific.py",
     "elif n_obs[name] < min_history:", "elif n_obs[name] < 0:", [SPEC]),
    # --- standardisation / design / factor model ----------------------------------------------
    ("centre is the equal-weighted mean", R + "factor_model.py",
     "centre = float(cap[ok] @ clipped / cap[ok].sum())", "centre = float(clipped.mean())", [MOD]),
    ("std with ddof 1", R + "factor_model.py", "std = float(clipped.std())", "std = float(clipped.std(ddof=1))", [MOD]),
    ("winsorisation skipped", R + "factor_model.py", "clipped = np.clip(x[ok], lo, hi)", "clipped = x[ok]", [MOD]),
    ("missing style not neutral", R + "factor_model.py",
     "out = pd.DataFrame(0.0, index=frame.index, columns=list(STYLE_NAMES))",
     "out = pd.DataFrame(0.1, index=frame.index, columns=list(STYLE_NAMES))", [MOD]),
    ("market column zero", R + "factor_model.py", "design[MARKET] = 1.0", "design[MARKET] = 0.0", [MOD]),
    ("WLS weights are cap, not sqrt(cap)", R + "factor_model.py",
     "returns.loc[prep.design.index], prep.design, np.sqrt(prep.caps), constraint",
     "returns.loc[prep.design.index], prep.design, prep.caps, constraint", [MOD]),
    ("LEAK history.up_to includes the next session", R + "factor_model.py",
     "keep = self.factor_returns.index <= pd.Timestamp(day)", "keep = self.factor_returns.index <= pd.Timestamp(day) + pd.Timedelta(days=1)", [MOD]),
    ("LEAK model ignores the as-of cut", R + "factor_model.py", "    hist = history.up_to(as_of)", "    hist = history", [MOD]),
    ("factor EWMA weights reversed", R + "factor_model.py",
     "w = 0.5 ** ((t - 1 - np.arange(t)) / half_life)", "w = 0.5 ** (np.arange(t) / half_life)", [MOD]),
    ("Newey-West Bartlett weight dropped", R + "factor_model.py",
     "cov = cov + (1.0 - lag / (lags + 1)) * (g + g.T)", "cov = cov + (g + g.T)", [MOD]),
    ("Newey-West lag not applied", R + "factor_model.py",
     "num = (z[lag:] * w[lag:, None]).T @ z[: t - lag]", "num = (z[lag:] * w[lag:, None]).T @ z[lag:]", [MOD]),
    ("factor covariance not symmetrised", R + "factor_model.py",
     "return (cov + cov.T) / 2.0", "return cov", [MOD]),
    ("PSD repair skipped", R + "factor_model.py", "    if not is_psd(cov):", "    if False:", [MOD]),
    ("condition floor skipped", R + "factor_model.py",
     "    if condition_number(cov) > max_condition:", "    if False:", [MOD]),
    ("cov() leaves out the specific variance", R + "factor_model.py",
     "out[np.diag_indices_from(out)] += self.d[pos]", "out[np.diag_indices_from(out)] += 0.0", [MOD]),
    # --- analytics ----------------------------------------------------------------------------
    ("by_factor uses x*x", R + "analytics.py", "pd.Series(x * fx,", "pd.Series(x * x,", [ANA]),
    ("mctr divided by variance", R + "analytics.py", "mctr = sigma_w / total_vol", "mctr = sigma_w / total_var", [ANA]),
    ("specific variance linear in |w|", R + "analytics.py",
     "specific_var = float(w**2 @ model.d)", "specific_var = float(np.abs(w) @ model.d)", [ANA]),
    ("nonzero weight on unknown name tolerated", R + "analytics.py", "    if unknown:", "    if False:", [ANA]),
    ("bias statistic without the root", R + "analytics.py",
     'return float(np.sqrt(np.mean((both["r"] / both["s"]) ** 2)))',
     'return float(np.mean((both["r"] / both["s"]) ** 2))', [ANA]),
    ("bias statistic minimum disabled", R + "analytics.py", "    if len(both) < 20:", "    if len(both) < 0:", [ANA]),
    # --- ETFs ---------------------------------------------------------------------------------
    ("ETF window ignored", R + "etf_mapping.py", ".tail(ec.window)", ".tail(10**6)", [ETF]),
    ("ETF returns not demeaned", R + "etf_mapping.py", "        f = f - f.mean(axis=0)\n", "", [ETF]),
    ("ETF residual variance without dof", R + "etf_mapping.py",
     "var = sse / (n - k - 1)", "var = sse / n", [ETF]),
    ("ETF minimum observations disabled", R + "etf_mapping.py",
     "        if n < max(ec.min_obs, k + 2):", "        if False:", [ETF]),
    ("ETF name clash allowed", R + "etf_mapping.py", "    if clash:", "    if False:", [ETF]),
    # --- store / loader -----------------------------------------------------------------------
    ("LEAK factor returns available at the decision of their own day", R + "store.py",
     "return pd.Timestamp(bar_available_at(day, self._calendar))",
     "return pd.Timestamp(bar_available_at(day, self._calendar)) - pd.Timedelta(days=1)", [STORE]),
    ("other settings' rows read", R + "store.py",
     'return rows[rows["config_hash"] == self.config_hash]', "return rows", [STORE]),
    ("LEAK loader uses the regressed session's own close for the styles", R + "loader.py",
     "prev = pd.Timestamp(cal.offset(day, -1))", "prev = pd.Timestamp(day)", [LOAD]),
    ("loader caps scaled by the inverse price ratio", R + "loader.py",
     "close.loc[prev, members] / close.loc[base, members]",
     "close.loc[base, members] / close.loc[prev, members]", [LOAD]),
    # --- audit follow-up (2026-11-04) -----------------------------------------------------------
    ("audit: skip does not cover the regression", R + "factor_model.py",
     '            constraint = sector_constraint(prep.design, prep.caps, factors.sectors)\n            res = constrained_wls(\n                returns.loc[prep.design.index], prep.design, np.sqrt(prep.caps), constraint\n            )\n        except DataError as exc:\n            if on_short == "skip":\n                _log.warning(\n                    "risk_model.cross_section_skipped", day=cs.day.isoformat(), error=str(exc)\n                )\n                skipped.append(cs.day)\n                continue\n            raise\n        stamp = pd.Timestamp(cs.day)',
     '        except DataError as exc:\n            if on_short == "skip":\n                _log.warning(\n                    "risk_model.cross_section_skipped", day=cs.day.isoformat(), error=str(exc)\n                )\n                skipped.append(cs.day)\n                continue\n            raise\n        constraint = sector_constraint(prep.design, prep.caps, factors.sectors)\n        res = constrained_wls(\n            returns.loc[prep.design.index], prep.design, np.sqrt(prep.caps), constraint\n        )\n        stamp = pd.Timestamp(cs.day)',
     [MOD]),
    ("audit: per-factor history check removed", R + "factor_model.py",
     "thin = [str(name) for name, n in counts.items() if n < min_history]", "thin: list[str] = []", [MOD]),
    ("audit: stale history accepted", R + "factor_model.py",
     "    if gap > fm.max_stale_sessions:", "    if False:", [MOD]),
    ("audit: off-by-one staleness", R + "factor_model.py",
     "gap = len(cal.sessions(last, as_of)) - 1 if as_of > last else 0",
     "gap = len(cal.sessions(last, as_of)) - 2 if as_of > last else 0", [MOD]),
    ("audit: non-finite model accepted", R + "factor_model.py",
     "        if not (np.isfinite(x).all() and np.isfinite(f).all() and np.isfinite(d).all()):",
     "        if False:", [MOD]),
    ("audit: asymmetric f accepted", R + "factor_model.py",
     "        if not np.allclose(f, f.T, rtol=1e-12, atol=0.0):", "        if False:", [MOD]),
    ("audit: ETF drops not reported", R + "etf_mapping.py",
     "dropped={**model.dropped, **etfs.dropped},", "dropped=model.dropped,", [ETF]),
    ("audit: LEAK ETF exposures use factor returns after the view", R + "loader.py",
     "    hist = history.up_to(data_date)\n    if hist.factor_returns.empty:\n        raise DataError(f\"no factor returns on or before {data_date}\")\n    returns",
     "    hist = history\n    if hist.factor_returns.empty:\n        raise DataError(f\"no factor returns on or before {data_date}\")\n    returns",
     [LOAD]),
    ("audit: snapshots without the minimum history", R + "loader.py",
     "if stamp not in index or int((index <= stamp).sum()) < cfg.quant.risk_model.min_history:",
     "if stamp not in index:", [LOAD]),
    ("audit: LEAK snapshot model sees the whole history", R + "loader.py",
     "        upto = history.up_to(day)\n", "        upto = history\n", [LOAD]),
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
