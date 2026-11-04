"""Stage 18 mutation checks: apply one deliberate bug at a time to the stage's code (EWMA
weights and order, ddof, Ledoit-Wolf and OAS terms, Higham projections, condition floor, the
history/overlap policy, GARCH validity checks and fallback, the blend, annualisation), run the
tests that should catch it, and restore the file. Every mutant must make a test fail.

Run from the repository root:
    MPLCONFIGDIR=$TMPDIR/mpl UV_CACHE_DIR=$TMPDIR/uv-cache-s18 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-18-covariance-vol/artifacts/mutate.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
R = "src/atlas/quant/risk_model/"
T = "tests/unit/quant/risk_model/"
COV, SHR, PSD, VOL, GAR = (
    T + "test_risk_covariance.py",
    T + "test_risk_shrinkage.py",
    T + "test_risk_psd.py",
    T + "test_risk_volatility.py",
    T + "test_risk_garch.py",
)

MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # (name, file, old, new, tests)
    # --- sample / EWMA ----------------------------------------------------------------------
    ("sample ddof 0", R + "covariance.py",
     "cov = (cross - sums * sums.T / common) / (common - 1.0)",
     "cov = (cross - sums * sums.T / common) / common", [COV]),
    ("sample not demeaned", R + "covariance.py",
     "cov = (cross - sums * sums.T / common) / (common - 1.0)",
     "cov = cross / (common - 1.0)", [COV]),
    ("LEAK EWMA weights reversed (oldest weighted most)", R + "covariance.py",
     'weights = self.lam ** np.arange(len(x) - 1, -1, -1, dtype="float64")',
     'weights = self.lam ** np.arange(len(x), dtype="float64")', [COV]),
    ("EWMA weights not normalised per pair", R + "covariance.py",
     "total = (mask * weights[:, None]).T @ mask", "total = np.full_like(cross, weights.sum())",
     [COV]),
    ("half-life off by one", R + "volatility.py", "lam = 0.5 ** (1.0 / half_life)",
     "lam = 0.5 ** (1.0 / (half_life + 1))", [COV, VOL]),
    ("EWMA path: missing return does not decay", R + "volatility.py",
     "        num *= decay\n        den *= decay\n        if not np.isnan(x):\n            num += x * x\n            den += 1.0\n",
     "        if not np.isnan(x):\n            num = num * decay + x * x\n            den = den * decay + 1.0\n",
     [VOL]),
    # --- Ledoit-Wolf / OAS --------------------------------------------------------------------
    ("LW: pi without the S^2 term", R + "covariance.py", "pi = (sq.T @ sq) / common - moment**2",
     "pi = (sq.T @ sq) / common", [SHR]),
    ("shrinkage repairs after shrinking (S not repaired first)", R + "covariance.py",
     "    if is_psd(s):\n        return x0, mask, common, s,", "    if True:\n        return x0, mask, common, s,",
     [COV]),
    ("repair not recorded by the shrinkage estimators", R + "covariance.py",
     'adjustments: list[str] = ["nearest_psd"] if repaired else []', "adjustments: list[str] = []",
     [COV]),
    ("LW: theta diagonal kept", R + "covariance.py", "        np.fill_diagonal(theta, 0.0)\n",
     "", [SHR]),
    ("LW: rho off-diagonal sqrt ratio inverted", R + "covariance.py",
     "np.outer(1.0 / sd, sd) * theta", "np.outer(sd, 1.0 / sd) * theta", [SHR]),
    ("LW: rbar includes the diagonal", R + "covariance.py",
     "rbar = float(((s / np.outer(sd, sd)).sum() - n) / (n * (n - 1)))",
     "rbar = float((s / np.outer(sd, sd)).sum() / (n * n))", [SHR]),
    ("LW: intensity not divided by T", R + "covariance.py",
     "delta = min(1.0, max(0.0, (float(pi.sum()) - rho) / gamma / t))",
     "delta = min(1.0, max(0.0, (float(pi.sum()) - rho) / gamma))", [SHR]),
    ("LW: intensity not clamped below", R + "covariance.py",
     "delta = min(1.0, max(0.0, (float(pi.sum()) - rho) / gamma / t))",
     "delta = min(1.0, (float(pi.sum()) - rho) / gamma / t)", [SHR]),
    ("LW: S divided by n-1", R + "covariance.py",
     "    s = (x0.T @ x0) / common\n", "    s = (x0.T @ x0) / (common - 1.0)\n", [SHR]),
    ("OAS: 2/p terms dropped", R + "covariance.py",
     "num = (1.0 - 2.0 / p) * tr2 + tr**2", "num = tr2 + tr**2", [SHR]),
    ("OAS: target not scaled by tr/p", R + "covariance.py",
     "return (1.0 - rho) * s + rho * (tr / p) * np.eye(p), rho",
     "return (1.0 - rho) * s + rho * np.eye(p), rho", [SHR]),
    ("OAS: shrinkage not capped at 1", R + "covariance.py",
     "rho = 1.0 if den <= 0.0 else min(1.0, num / den)", "rho = 1.0 if den <= 0.0 else num / den",
     [SHR]),
    # --- PSD / conditioning ---------------------------------------------------------------------
    ("Higham 2002 without Dykstra correction", R + "psd.py", "        correction = x - r\n",
     "", [PSD]),
    ("nearest_psd clips at a positive floor", R + "psd.py",
     "        return _like(_clip(a, 0.0), matrix)", "        return _like(_clip(a, 1e-3), matrix)",
     [PSD]),
    ("keep_diagonal ignored in nearest_psd", R + "psd.py",
     "    if not keep_diagonal:\n", "    if True:\n", [PSD, COV]),
    ("condition floor at lambda_max / (2 max_condition)", R + "psd.py",
     "    floor = top / max_condition\n", "    floor = top / (2 * max_condition)\n", [PSD, COV]),
    ("estimator skips the PSD repair", R + "covariance.py",
     "        if not is_psd(cov):\n", "        if False:\n", [COV]),
    ("estimator skips the condition floor", R + "covariance.py",
     "        if cond > self.max_condition:\n", "        if False:\n", [COV]),
    ("is_psd tolerance too loose", R + "psd.py",
     "    return bool(eig.min() >= -tol * scale)", "    return bool(eig.min() >= -0.5 * scale)",
     [PSD]),
    # --- history / missing data -----------------------------------------------------------------
    ("min_history off by one", R + "covariance.py", "            if len(col) < self.min_history:",
     "            if len(col) <= self.min_history:", [COV]),
    ("overlap rule disabled", R + "covariance.py",
     "        if not counts.any():\n            break\n", "        break\n", [COV]),
    ("overlap tie-break picks the earlier name", R + "covariance.py",
     "key=lambda i: (counts[i], -own[i], names[keep[i]])",
     "key=lambda i: (counts[i], -own[i], [-ord(c) for c in names[keep[i]]])", [COV]),
    ("constant names kept", R + "covariance.py",
     '            elif col.max() == col.min():\n                dropped[name] = "constant"\n',
     "", [COV]),
    ("unsorted index accepted", R + "covariance.py",
     "returns.index.is_unique and returns.index.is_monotonic_increasing", "returns.index.is_unique",
     [COV]),
    # --- post-DONE audit fixes -------------------------------------------------------------------
    ("AUDIT CovResult compares field by field", R + "covariance.py",
     "@dataclass(frozen=True, eq=False)\nclass CovResult:", "@dataclass(frozen=True)\nclass CovResult:",
     [COV]),
    ("AUDIT complete policy drops rows (EWMA loses decay slots)", R + "covariance.py",
     "            x[~rows] = np.nan", "            x = x[rows]", [COV]),
    ("AUDIT zero estimated variance floored instead of refused", R + "covariance.py",
     "        if zero:", "        if False:", [COV]),
    ("AUDIT invalid GARCH forecast not a fallback", R + "volatility.py",
     "    if not (math.isfinite(variance) and variance > 0.0):\n        return fall_back(",
     "    if False:\n        return fall_back(", [GAR]),
    # --- vol forecasts ----------------------------------------------------------------------------
    ("GARCH: integrated parameters accepted", R + "volatility.py",
     "        and alpha + beta < 1.0\n", "        and alpha + beta <= 1.0\n", [GAR]),
    ("GARCH: convergence flag ignored", R + "volatility.py",
     "    if res.convergence_flag != 0:\n", "    if False:\n", [GAR]),
    ("GARCH: short-history guard off", R + "volatility.py", "    if len(clean) < need:\n",
     "    if False:\n", [GAR]),
    ("GARCH: fit on fractions, not percent", R + "volatility.py",
     "    pct = clean.to_numpy() * 100.0\n", "    pct = clean.to_numpy()\n", [GAR]),
    ("GARCH: omega not converted to fractions", R + "volatility.py",
     '        "omega": omega / 1e4,', '        "omega": omega,', [GAR]),
    ("blend: realised weight used for GARCH", R + "volatility.py",
     'weight = {"ewma": w.ewma, "garch": w.garch, "realised": w.realised}',
     'weight = {"ewma": w.ewma, "garch": w.realised, "realised": w.garch}', [VOL]),
    ("realised vol: window includes one extra session", R + "volatility.py",
     "last = r.to_numpy()[-size:]", "last = r.to_numpy()[-size - 1 :]", [VOL]),
    ("realised vol demeaned", R + "volatility.py", "float(np.mean(last**2))",
     "float(np.var(last))", [VOL]),
    ("annualise vol by P, not sqrt(P)", R + "volatility.py",
     "    return values * math.sqrt(_periods(periods, config))",
     "    return values * _periods(periods, config)", [VOL]),
    ("zero variance accepted", R + "volatility.py",
     "    if not (math.isfinite(variance) and variance > 0.0):\n        raise DataError(",
     "    if not math.isfinite(variance):\n        raise DataError(", [VOL]),
]


def run(tests: list[str]) -> bool:
    """True when the tests fail (the mutant is caught)."""
    cmd = ["uv", "run", "pytest", "-q", "-x", "-p", "no:logging", "-p", "no:cacheprovider", *tests]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).returncode != 0


def main() -> int:
    caught = 0
    for name, rel, old, new, tests in MUTANTS:
        path = ROOT / rel
        original = path.read_text()
        if original.count(old) != 1:
            print(f"SETUP ERROR  {name}: pattern found {original.count(old)} times in {rel}")
            return 2
        path.write_text(original.replace(old, new))
        try:
            ok = run(tests)
        finally:
            path.write_text(original)
        caught += ok
        print(f"{'caught  ' if ok else 'SURVIVED'}  {name}")
    print(f"\n{caught}/{len(MUTANTS)} mutants caught")
    return 0 if caught == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
