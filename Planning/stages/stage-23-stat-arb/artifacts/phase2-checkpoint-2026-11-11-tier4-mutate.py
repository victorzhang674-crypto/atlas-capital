"""Phase 2 checkpoint, Tier 4 (backlog: "no mutation run was made for the Tier 2 changes"):
do the tests catch deliberate mistakes in the Tier 2 code and in this tier's new rules?

Each mutant changes one line of a COPY of `src/` (made under a scratch folder: the worktree's
source is never edited), then runs the given tests with `PYTHONPATH=<copy>/src` (it wins over
the editable install) and expects a failure. Run from the repo root:

    uv run python <this file> <scratch folder>
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

R = "tests/unit/quant/research/"
MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # --- Tier 2 (2026-11-12) -----------------------------------------------------------------
    ("hold-out read at the in-sample end itself", "atlas/quant/research/holdout.py",
     "        return self.last_label_session > self.in_sample_end",
     "        return self.last_label_session >= self.in_sample_end",
     [R + "test_research_holdout.py"]),
    ("hold-out uses the shortest horizon", "atlas/quant/research/holdout.py",
     "        last_label_session=calendar.offset(last, max(hs)),",
     "        last_label_session=calendar.offset(last, min(hs)),",
     [R + "test_research_holdout.py"]),
    ("in-sample end on a closed day not moved back", "atlas/quant/research/holdout.py",
     "    last_in_sample = end if calendar.is_session(end) else calendar.prev_session(end)",
     "    last_in_sample = end if calendar.is_session(end) else calendar.next_session(end)",
     [R + "test_research_holdout.py"]),
    ("keep_gaps squeezes the gaps anyway", "atlas/quant/research/ic.py",
     "    d = np.where(np.isnan(raw), 0.0, raw - mean) if keep_gaps else x - mean",
     "    d = x - mean",
     [R + "test_research_ic.py"]),
    ("Bartlett weight off by one", "atlas/quant/research/ic.py",
     "        weight = 1.0 - lag / (lags + 1)",
     "        weight = 1.0 - lag / lags",
     [R + "test_research_ic.py"]),
    ("a -100% day does not stop the backtest", "atlas/quant/research/vector_backtest.py",
     "            if not day_ret > -1.0:",
     "            if not day_ret > -2.0:",
     [R + "test_research_backtest.py"]),
    ("horizon check never fires", "atlas/quant/alpha/base.py",
     "    if model.horizon != configured:",
     "    if False:",
     ["tests/unit/quant/alpha/"]),
    # --- Tier 4 (2026-11-14) -----------------------------------------------------------------
    ("headline t-statistic squeezes the gaps", "atlas/quant/research/tearsheet.py",
     '            ic_summary[f"h{h}"][method] = summarize_ic(series, lags, keep_gaps=True).to_dict()',
     '            ic_summary[f"h{h}"][method] = summarize_ic(series, lags).to_dict()',
     [R + "test_research_tearsheet.py"]),
    ("stale rule ignores the tick", "atlas/data/quality.py",
     "    return {str(s) for s, v in typical.items() if v < threshold}",
     "    return set()",
     ["tests/unit/data/test_quality.py"]),
    ("stale rule threshold exclusive", "atlas/data/quality.py",
     "    return {str(s) for s, v in typical.items() if v < threshold}",
     "    return {str(s) for s, v in typical.items() if v <= threshold}",
     ["tests/unit/data/test_quality.py"]),
    ("a wrong cover count is kept", "atlas/quant/factors/fundamental.py",
     "                and not _within(count, averages[symbol], max_ratio)",
     "                and False",
     ["tests/unit/quant/factors/test_factor_share_fallback.py"]),
    ("old counts never confirm an average", "atlas/quant/factors/fundamental.py",
     "                keep.append(_within(today, old[str(symbol)], max_ratio))",
     "                keep.append(False)",
     ["tests/unit/quant/factors/test_factor_share_fallback.py"]),
    ("fingerprint counts rows known after the cutoff", "atlas/quant/factors/store.py",
     "            payload[schema.name] = dataset.count_rows(filter=ds.field(AVAILABLE_AT) < cutoff)",
     "            payload[schema.name] = dataset.count_rows()",
     ["tests/unit/quant/factors/test_factor_store_fingerprint.py"]),
    ("a stale panel is read", "atlas/quant/factors/store.py",
     "        if stored == {current} and not own[\"data_fingerprint\"].isna().any():",
     "        if True:",
     ["tests/unit/quant/factors/test_factor_store_fingerprint.py"]),
    ("a company's listings weigh sqrt(n) too much", "atlas/quant/risk_model/factor_model.py",
     "            weights = np.sqrt(prep.caps) / np.sqrt(n_classes.loc[prep.design.index])",
     "            weights = np.sqrt(prep.caps)",
     ["tests/unit/quant/risk_model/test_factor_risk_classes.py"]),
    ("share volume not restated", "atlas/quant/features/registry.py",
     "    return raw.div(factors)",
     "    return raw",
     ["tests/unit/quant/features/test_feature_share_volume.py"]),
    ("reused-ticker bars kept", "atlas/data/prices.py",
     "            keep = bars[symbol][\"date\"] <= until",
     "            keep = bars[symbol][\"date\"] <= date.max",
     ["tests/unit/data/test_price_ingest.py"]),
    ("pairs backtest accepts a view that sees past the window", "atlas/quant/statarb/backtest.py",
     "    if view.day != cal.next_session(last):",
     "    if False:",
     ["tests/unit/quant/statarb/test_statarb_backtest_lake.py"]),
    ("withheld ETFs not reported", "atlas/quant/risk_model/loader.py",
     "    withheld = [n for n in names if n in blocked]",
     "    withheld = []",
     ["tests/unit/quant/risk_model/test_factor_risk_loader.py"]),
    ("helper threads get no run", "atlas/core/logging.py",
     "        ctx = process_run_context()",
     "        ctx = None",
     ["tests/unit/core/test_logging.py"]),
]


def main(scratch: Path) -> int:
    copy = scratch / "src"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree("src", copy)
    env = {**os.environ, "PYTHONPATH": str(copy)}
    caught = 0
    for name, rel, old, new, tests in MUTANTS:
        path = copy / rel
        original = Path("src", rel).read_text()
        if original.count(old) != 1:
            print(f"NOT APPLIED  {name}: the line occurs {original.count(old)} times")
            continue
        path.write_text(original.replace(old, new))
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests],
            capture_output=True, text=True, env=env,
        )
        path.write_text(original)
        tail = [ln for ln in run.stdout.splitlines() if "passed" in ln or "failed" in ln]
        verdict = "CAUGHT  " if run.returncode != 0 else "SURVIVED"
        caught += run.returncode != 0
        print(f"{verdict} {name}: {tail[-1] if tail else run.returncode}", flush=True)
    print(f"{caught}/{len(MUTANTS)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
