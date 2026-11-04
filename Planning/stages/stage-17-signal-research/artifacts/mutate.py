"""Stage 17 mutation checks: apply one deliberate bug at a time to the stage's code (the
label maths, the look-ahead guard and timing, the IC / Newey-West / quantile / backtest maths),
run the tests that should catch it, and restore the file. Every mutant must make a test fail.

Run from the repository root:
    MPLCONFIGDIR=$TMPDIR/mpl UV_CACHE_DIR=$TMPDIR/uv-cache-s17 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-17-signal-research/artifacts/mutate.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
R = "src/atlas/quant/research/"
T = "tests/unit/quant/research/"

MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # (name, file, old, new, tests)
    # --- labels and look-ahead ------------------------------------------------------------------
    ("LEAK label window includes the decision day", R + "labels.py",
     "growth[p + 1 : p + 1 + horizon]", "growth[p : p + horizon]", [T + "test_research_labels.py"]),
    ("label view one session too early", R + "labels.py",
     "view = view_factory(calendar.next_session(last))", "view = view_factory(last)",
     [T + "test_research_labels.py"]),
    ("blocked names not reported", R + "labels.py",
     "blocked = tuple(s for s in wanted if s not in rets.columns)", "blocked = ()",
     [T + "test_research_labels.py"]),
    ("LEAK guard disabled", "src/atlas/core/pit.py", "    if is_label(obj):\n",
     "    if False:\n", [T + "test_research_ic.py", T + "test_research_backtest.py"]),
    ("LEAK label tag ignored", "src/atlas/core/pit.py",
     "return bool(obj.attrs.get(LABEL_ATTR, False))", "return False",
     [T + "test_research_labels.py", T + "test_research_quantiles.py"]),
    ("LEAK composite accepts labels", "src/atlas/quant/factors/composite.py",
     '    guard_not_label(factors, "factor frame")\n', "",
     ["tests/unit/quant/factors/test_factor_composite.py"]),
    ("stale signal row", R + "signals.py", "return frame.iloc[-1].astype(\"float64\")",
     "return frame.iloc[-2].astype(\"float64\")", [T + "test_research_signals.py"]),
    # --- IC, Newey-West, summary -------------------------------------------------------------
    ("spearman ranks over unmatched names", R + "ic.py",
     "x, y = _ranks(x, mask), _ranks(y, mask)",
     "x, y = _ranks(x, ~np.isnan(x)), _ranks(y, ~np.isnan(y))", [T + "test_research_ic.py"]),
    ("constant cross-section not excluded", R + "ic.py",
     "valid = (n >= min_names) & spread_x & spread_y", "valid = n >= min_names",
     [T + "test_research_ic.py"]),
    ("Newey-West without Bartlett weights", R + "ic.py",
     "weight = 1.0 - lag / (lags + 1)", "weight = 1.0", [T + "test_research_ic.py"]),
    ("Newey-West lags one too many", R + "ic.py",
     "return max(math.ceil(horizon / session_step(dates, calendar)) - 1, 0)",
     "return max(math.ceil(horizon / session_step(dates, calendar)), 0)",
     [T + "test_research_ic.py"]),
    ("IC std ddof 0", R + "ic.py", "std = float(valid.std(ddof=1)) if n >= 2",
     "std = float(valid.std(ddof=0)) if n >= 2", [T + "test_research_ic.py"]),
    ("hit rate counts zero", R + "ic.py", "hit_rate=float((valid > 0).mean())",
     "hit_rate=float((valid >= 0).mean())", [T + "test_research_ic.py"]),
    ("AUDIT names without sector pooled", R + "ic.py",
     "for group in sorted({g for g in mapped if isinstance(g, str)}):",
     "for group in sorted({str(g) for g in mapped}):", [T + "test_research_ic.py"]),
    ("rank autocorrelation compares a date with itself", R + "ic.py",
     'corr = _corr(values[lag:], values[:-lag], "spearman", k)',
     'corr = _corr(values[lag:], values[lag:], "spearman", k)', [T + "test_research_ic.py"]),
    # --- quantiles ---------------------------------------------------------------------------
    ("bucket edges ceil(r q / n)", R + "quantiles.py",
     "buckets = np.floor((ranks - 1.0) * q / n[:, None]) + 1.0",
     "buckets = np.ceil(ranks * q / n[:, None])", [T + "test_research_quantiles.py"]),
    ("ties split across buckets", R + "quantiles.py",
     'ranks = signal.astype("float64").rank(axis=1, method="average")',
     'ranks = signal.astype("float64").rank(axis=1, method="first")',
     [T + "test_research_quantiles.py"]),
    ("LEAK daily buckets earn the decision day", R + "quantiles.py",
     'dates.searchsorted(sessions, side="left")', 'dates.searchsorted(sessions, side="right")',
     [T + "test_research_quantiles.py"]),
    ("monotonicity sign flipped", R + "quantiles.py", "np.corrcoef(order, ranks)",
     "np.corrcoef(order[::-1], ranks)", [T + "test_research_quantiles.py"]),
    # --- backtest ----------------------------------------------------------------------------
    ("LEAK backtest earns the next session's return early", R + "vector_backtest.py",
     "day_ret = float(w @ r[session])", "day_ret = float(w @ r[min(session + 1, len(r) - 1)])",
     [T + "test_research_backtest.py"]),
    ("no drift between rebalances", R + "vector_backtest.py",
     "w = w * (1.0 + r[session]) / (1.0 + day_ret)", "w = w",
     [T + "test_research_backtest.py"]),
    ("turnover against cash, not the drifted book", R + "vector_backtest.py",
     "turnover[i] = float(np.abs(targets[i] - w).sum())",
     "turnover[i] = float(np.abs(targets[i]).sum())", [T + "test_research_backtest.py"]),
    ("costs not charged", R + "vector_backtest.py", "cost[j] = turnover[i] * bps / 1e4",
     "cost[j] = 0.0", [T + "test_research_backtest.py"]),
    ("signal weights from raw ranks", R + "vector_backtest.py",
     "size = np.abs(ranks - (n + 1) / 2.0)", "size = np.abs(ranks)",
     [T + "test_research_backtest.py"]),
    ("Sharpe annualised by P, not sqrt P", R + "vector_backtest.py",
     '"sharpe": mean / std * np.sqrt(periods)', '"sharpe": mean / std * periods',
     [T + "test_research_backtest.py"]),
    # --- tear sheet --------------------------------------------------------------------------
    ("headline horizon from overlapping windows", R + "tearsheet.py",
     "fitting = [h for h in horizons if h <= step]", "fitting = [h for h in horizons if h >= step]",
     [T + "test_research_tearsheet.py"]),
    ("NaN written into the JSON", R + "tearsheet.py",
     "if isinstance(value, float) and not math.isfinite(value):\n        return None",
     "if isinstance(value, float) and not math.isfinite(value):\n        return value",
     [T + "test_research_tearsheet.py"]),
]


def run(tests: list[str]) -> bool:
    """True when the tests pass (the mutant survived)."""
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    return done.returncode == 0


def main() -> int:
    survived = []
    for name, rel, old, new, tests in MUTANTS:
        path = ROOT / rel
        original = path.read_text()
        if original.count(old) != 1:
            print(f"SETUP ERROR  {name}: pattern found {original.count(old)} times in {rel}")
            return 2
        path.write_text(original.replace(old, new))
        try:
            alive = run(tests)
        finally:
            path.write_text(original)
        print(f"{'SURVIVED' if alive else 'caught  '}  {name}", flush=True)
        if alive:
            survived.append(name)
    caught = len(MUTANTS) - len(survived)
    print(f"\n{caught}/{len(MUTANTS)} mutants caught")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
