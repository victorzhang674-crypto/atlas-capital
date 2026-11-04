"""Stage 16 mutation checks: apply one deliberate bug at a time to the stage's code, run the
tests that should catch it, and restore the file. Every mutant must make a test fail.

Run from the repository root:
    UV_CACHE_DIR=$TMPDIR/uv-cache-s16 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-16-fundamental-factors/artifacts/mutate.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
F = "src/atlas/quant/factors/"
T = "tests/unit/quant/factors/"

MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # (name, file, old, new, tests)
    ("earnings yield over EV", F + "fundamental.py", '"earnings_yield": _ratio(ni, cap)',
     '"earnings_yield": _ratio(ni, ev)', [T + "test_factor_raw.py"]),
    ("accruals not negated", F + "fundamental.py", '"accruals": -_ratio(ni - ocf, assets)',
     '"accruals": _ratio(ni - ocf, assets)', [T + "test_factor_raw.py"]),
    ("margin std ddof 0", F + "fundamental.py", "return -float(np.std(margins, ddof=1))",
     "return -float(np.std(margins, ddof=0))", [T + "test_factor_raw.py"]),
    ("growth vs 3-year-ago TTM", F + "fundamental.py",
     "r1 = _nearest(ends, values, e0 - timedelta(days=_YEAR), tol)",
     "r1 = _nearest(ends, values, e0 - timedelta(days=3 * _YEAR), tol)", [T + "test_factor_raw.py"]),
    ("issuance not negated", F + "fundamental.py", "return -(s0 / s1 - 1) if",
     "return (s0 / s1 - 1) if", [T + "test_factor_raw.py"]),
    ("growth ignores staleness", F + "fundamental.py",
     'ends, values = _ttm_points(own, "revenue")\n    if not ends.empty and ends.iloc[-1] >= floor:',
     'ends, values = _ttm_points(own, "revenue")\n    if not ends.empty:', [T + "test_factor_raw.py"]),
    ("EPS floor ignored", F + "fundamental.py", "abs(eps1) >= config.min_abs_eps",
     "abs(eps1) > 0", [T + "test_factor_raw.py"]),
    ("shares not split-adjusted", F + "fundamental.py",
     "float(v) / _factor_at(splits, s, d)", "float(v)", [T + "test_factor_market.py"]),
    ("cap ignores the price session's split basis", F + "fundamental.py",
     "cap = price * count * _factor_at(splits, symbol, price_date)", "cap = price * count",
     [T + "test_factor_market.py"]),
    ("EV without debt", F + "fundamental.py", "ev = cap + debt - cash if", "ev = cap - cash if",
     [T + "test_factor_market.py"]),
    ("zscore ddof 1", F + "cleaning.py", "std = float(ref.std(ddof=0))",
     "std = float(ref.std(ddof=1))", [T + "test_factor_cleaning.py"]),
    ("no winsorising", F + "cleaning.py",
     "z = zscore(winsorize(frame, config.winsor_lower, config.winsor_upper))",
     "z = zscore(frame)", [T + "test_factor_cleaning.py"]),
    ("fill with the overall median", F + "cleaning.py",
     "fill[known] = sector.to_numpy()", "pass", [T + "test_factor_cleaning.py"]),
    ("no sector demeaning", F + "cleaning.py",
     "result[in_sector] = x[in_sector] - demeaned.to_numpy()",
     "result[in_sector] = x[in_sector] - x[in_sector].mean()", [T + "test_factor_cleaning.py"]),
    ("size regression without sector dummies", F + "cleaning.py",
     "design = np.column_stack([dummies.to_numpy(), s[in_sector]])",
     "design = np.column_stack([np.ones(int(in_sector.sum())), s[in_sector]])",
     [T + "test_factor_cleaning.py"]),
    ("AUDIT sector-less names demeaned as one group", F + "cleaning.py",
     "result[no_sector] = x[no_sector] - x[valid].mean()",
     "result[no_sector] = x[no_sector] - x[no_sector].mean()", [T + "test_factor_cleaning.py"]),
    ("AUDIT sector-less names filled from their own group", F + "cleaning.py",
     "fill = pd.Series(float(x.median()), index=x.index)",
     "fill = pd.Series(float(x[~known].median()), index=x.index)",
     [T + "test_factor_cleaning.py"]),
    ("AUDIT final z-score over sector-less names too", F + "cleaning.py",
     "zscore(neutral, basis=known)", "zscore(neutral)", [T + "test_factor_cleaning.py"]),
    ("no final z-score", F + "cleaning.py", "return CleanedFactors(exposures=zscore(neutral, basis=known),",
     "return CleanedFactors(exposures=neutral,", [T + "test_factor_cleaning.py"]),
    ("AUDIT margin span from a fixed 92 days", F + "fundamental.py",
     "span > (n - 1) * max_quarter_days + config.period_tolerance_days",
     "span > (n - 1) * 92 + config.period_tolerance_days", [T + "test_factor_raw.py"]),
    ("AUDIT no instruments at all treated as sector-less", F + "fundamental.py",
     "if symbols and len(missing) == len(symbols):", "if False:", [T + "test_factor_panel.py"]),
    ("min_names ignored", F + "cleaning.py", "< config.min_names]", "< 0]",
     [T + "test_factor_cleaning.py"]),
    ("composite over sum of weights", F + "composite.py", "den = known.astype(\"float64\") @ np.abs(w)",
     "den = known.astype(\"float64\") @ w", [T + "test_factor_composite.py"]),
    ("store stamps the cutoff itself", F + "store.py",
     "available_at=pd.Timestamp(panel.as_of.cutoff) - _EPSILON",
     "available_at=pd.Timestamp(panel.as_of.cutoff)", [T + "test_factor_store.py"]),
    ("store read without the PIT filter", F + "store.py",
     "known = pit_filter(rows, point)\n        assert_no_lookahead(known, point)",
     "known = rows", [T + "test_factor_store.py"]),
    ("TTM prefers quarters to the annual figure", "src/atlas/data/fundamentals/pit.py",
     'annual.assign(source="annual", rank=0)', 'annual.assign(source="annual", rank=2)',
     ["tests/unit/data/fundamentals/test_fundamentals_history.py"]),
    # look-ahead mutants: the acceptance tests must catch them
    ("LOOK-AHEAD history uses every stored fact", "src/atlas/data/view.py",
     "            lambda: concept_history(\n                known_facts(facts, point),",
     "            lambda: concept_history(\n                facts,",
     [T + "test_factor_pit.py", "tests/unit/data/test_view_history.py"]),
    ("LOOK-AHEAD panel reads a later view", F + "fundamental.py",
     "    panel = view.fundamentals_panel(wanted)\n",
     "    view = view.advance(view.day + timedelta(days=95))\n"
     "    panel = view.fundamentals_panel(wanted)\n", [T + "test_factor_pit.py"]),
    ("LOOK-AHEAD split factors from unfiltered actions", "src/atlas/data/view.py",
     '            splits = actions[actions["type"] == "split"]',
     '            raw = self._shared.lake.read_table(CORPORATE_ACTIONS.name)\n'
     '            splits = raw[(raw["type"] == "split") & raw["symbol"].isin(wanted)]',
     [T + "test_factor_pit.py"]),
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
        print(f"{'SURVIVED' if alive else 'caught  '}  {name}")
        if alive:
            survived.append(name)
    caught = len(MUTANTS) - len(survived)
    print(f"\n{caught}/{len(MUTANTS)} mutants caught")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
