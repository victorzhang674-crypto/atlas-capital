"""Stage 21 mutation checks: apply one deliberate bug at a time to the regime code (look-ahead,
ordering, filtering, tie rules, thresholds, the scalar mapping, persistence and the DataView
additions), run the tests that should catch it, and restore the file. Every mutant must make a
test fail.

Run from the repository root (never while other checks run: it edits `src/` in place):
    UV_CACHE_DIR=$TMPDIR/uv-cache-s21 UV_NO_SYNC=1 uv run python \
        Planning/stages/stage-21-regime-detection/artifacts/mutate.py [--check] [--only <fragment> ...]
(`--check` only verifies that every pattern is found exactly once.)
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
R = "src/atlas/quant/regime/"
T = "tests/unit/quant/regime/"
VT, HMM, PIT, MQ, ST, LD, SO, AU = (
    T + "test_regime_vol_trend.py",
    T + "test_regime_hmm.py",
    T + "test_regime_hmm_pit.py",
    T + "test_regime_macro_quadrant.py",
    T + "test_regime_state.py",
    T + "test_regime_loader.py",
    T + "test_regime_store.py",
    T + "test_regime_audit.py",
)
VIEW = "tests/unit/data/test_view_pit.py"
CLI = "tests/unit/cli/test_regime_cmd.py"

MUTANTS: list[tuple[str, str, str, str, list[str]]] = [
    # (name, file, old, new, tests)
    # --- vol / trend ----------------------------------------------------------------------------
    ("percentile ties counted below only", R + "vol_trend.py",
     'rank(method="max", pct=True)', 'rank(method="min", pct=True)', [VT, AU]),
    ("percentile window ignored (expanding)", R + "vol_trend.py",
     "values.rolling(window, min_periods=min_obs)", "values.expanding(min_periods=min_obs)", [VT, AU]),
    ("vol score = min instead of max", R + "vol_trend.py",
     "both.max(axis=1, skipna=True)", "both.min(axis=1, skipna=True)", [VT, AU]),
    ("CRISIS without the floors", R + "vol_trend.py",
     "floor = (v >= config.crisis_vix_floor) | (r >= config.crisis_rv_floor)",
     "floor = np.ones_like(s, dtype=bool)", [VT, AU]),
    ("LOW threshold inclusive", R + "vol_trend.py",
     "[s < config.low_below, s < config.high_from", "[s <= config.low_below, s < config.high_from", [VT, AU]),
    ("breadth counts at-the-average as above", R + "vol_trend.py",
     "(eligible & (ratio > 1.0))", "(eligible & (ratio >= 1.0))", [VT, AU]),
    ("breadth min names ignored", R + "vol_trend.py",
     "return share.where(count >= min_names)", "return share.where(count >= 0)", [VT, AU]),
    ("trend UP needs only majority", R + "vol_trend.py",
     "[share >= config.up_share, share <= config.down_share]", "[share > 0.5, share <= config.down_share]", [VT, AU]),
    ("trend without the MA vote allowed", R + "vol_trend.py",
     'votes["ma"].notna().to_numpy()', 'np.ones(len(votes), dtype=bool)', [VT, AU]),
    # --- HMM --------------------------------------------------------------------------------------
    ("states not ordered by vol", R + "hmm.py",
     'order = np.argsort(order_std, kind="stable")', "order = np.arange(len(order_std))", [HMM, AU]),
    ("order reversed (turbulent first)", R + "hmm.py",
     'order = np.argsort(order_std, kind="stable")', 'order = np.argsort(-order_std, kind="stable")', [HMM, AU]),
    ("transition matrix not permuted", R + "hmm.py",
     "np.asarray(model.transmat_)[np.ix_(order, order)]", "np.asarray(model.transmat_)", [HMM, AU]),
    ("no standardisation stored", R + "hmm.py", "    scale = x.std(axis=0)\n", "    scale = np.ones(x.shape[1])\n", [HMM, AU]),
    ("filter skips the transition step", R + "hmm.py",
     "        log_alpha = _logsumexp(log_alpha[:, None] + log_a, axis=0) + log_b[t]",
     "        log_alpha = np.log(fit.startprob) + log_b[t]", [HMM, AU]),
    ("filter not normalised", R + "hmm.py",
     "        log_alpha = log_alpha - _logsumexp(log_alpha, axis=0)\n        out[t] = log_alpha",
     "        out[t] = log_alpha", [HMM, AU]),
    ("look-ahead: fit window includes the month", R + "hmm.py",
     "    train = features.loc[pd.DatetimeIndex(features.index) < month]",
     "    train = features.loc[pd.DatetimeIndex(features.index) < month + pd.offsets.MonthBegin(1)]", [PIT, AU]),
    ("look-ahead: probability of day t uses row t", R + "hmm.py",
     "            known = probs.loc[probs.index < pd.Timestamp(day)]",
     "            known = probs.loc[probs.index <= pd.Timestamp(day)]", [PIT, AU]),
    ("min_obs ignored", R + "hmm.py", "    if len(train) < config.min_obs:", "    if len(train) < 50:", [PIT, AU]),
    ("degenerate starts accepted", R + "hmm.py",
     "    if share.min() < min_state_share:", "    if False:", [HMM, PIT, AU]),
    ("multithreaded fits (not reproducible)", R + "hmm.py",
     "    with threadpool_limits(limits=1), warnings.catch_warnings():",
     "    with threadpool_limits(limits=None), warnings.catch_warnings():", [HMM, AU]),
    # --- macro quadrant, credit, stress ---------------------------------------------------------
    ("growth tie counts as up", R + "macro_quadrant.py", "    up = growth > 0\n", "    up = growth >= 0\n", [MQ, AU]),
    ("inflation tie counts as falling", R + "macro_quadrant.py",
     "    rising = inflation >= 0\n", "    rising = inflation > 0\n", [MQ, AU]),
    ("claims not inverted", R + "macro_quadrant.py",
     "            up_when_positive=False,\n", "            up_when_positive=True,\n", [MQ, AU]),
    ("claims short/long swapped", R + "macro_quadrant.py",
     "s.iloc[-short_weeks:].mean() - s.iloc[-long_weeks:].mean()",
     "s.iloc[-long_weeks:].mean() - s.iloc[-short_weeks:].mean()", [MQ, AU]),
    ("proxy unhedged", R + "macro_quadrant.py",
     "np.log(long_close).diff() - hedge_ratio * np.log(hedge_close).diff()",
     "np.log(long_close).diff()", [MQ, AU]),
    ("proxy sign flipped", R + "macro_quadrant.py",
     "    change = (-excess).astype(\"float64\")", "    change = excess.astype(\"float64\")", [MQ, AU]),
    ("proxy level from 0 before the data", R + "macro_quadrant.py",
     "        level.iloc[: max(change.index.get_loc(first) - 1, 0)] = np.nan\n", "        pass\n", [MQ, AU]),
    ("zscore on a partial window", R + "macro_quadrant.py",
     "s.rolling(window, min_periods=window)", "s.rolling(window, min_periods=2)", [MQ, AU]),
    ("OAS used when too short", R + "macro_quadrant.py",
     'config.source == "auto" and len(oas_clean) >= config.zscore_window',
     'config.source == "auto" and len(oas_clean) >= 1', [MQ, AU]),
    ("stress threshold inclusive", R + "macro_quadrant.py",
     "credit_z > config.credit.zscore_threshold", "credit_z >= config.credit.zscore_threshold", [MQ, AU]),
    ("NFCI ignored", R + "macro_quadrant.py",
     "math.isfinite(nfci) and nfci > config.stress.nfci_threshold", "False", [MQ, AU]),
    # --- risk scalar and the contract -------------------------------------------------------------
    ("scalar not clipped", R + "state.py",
     "    return float(min(table.cap, max(table.floor, value)))", "    return float(value)", [ST, AU]),
    ("stress multiplier dropped", R + "state.py", "        value *= table.stress\n", "        pass\n", [ST, AU]),
    ("HMM threshold strict", R + "state.py",
     "p_turbulent >= config.hmm.turbulent_prob", "p_turbulent > config.hmm.turbulent_prob", [ST, AU]),
    ("quadrant multiplier dropped", R + "state.py",
     "        value *= getattr(table.quadrant, quadrant.lower())\n", "        pass\n", [ST, AU]),
    ("state may be dated on its data date", R + "state.py",
     "        if self.data_date >= self.as_of:", "        if self.data_date > self.as_of:", [ST, AU]),
    # --- loader (point in time) -----------------------------------------------------------------
    ("look-ahead: FRED rows not filtered by the decision", R + "loader.py",
     "        latest = known_observations(self.rows, point)",
     "        latest = self.rows.sort_values(\"realtime_start\").drop_duplicates([\"series_id\", \"obs_date\"], keep=\"last\")",
     [LD, AU]),
    # (first version, "rows <= stamp + 7 days", was equivalent: the slope column is aligned on
    # sessions <= s only, so `.dropna()` removes every later row — a second guard; recorded)
    ("look-ahead: HMM filter sees the panel's later rows", R + "loader.py",
     "            rows = base.loc[base.index <= stamp].assign(slope=slope_t).dropna()",
     "            rows = base.assign(slope=_on_sessions(known_market.get(SLOPE_SERIES), sessions, rc.max_fill_sessions)).dropna()",
     [LD, AU]),
    ("look-ahead: VIX percentile uses later sessions", R + "loader.py",
     "        upto = sessions[sessions <= stamp]", "        upto = sessions", [LD, AU]),
    ("look-ahead: credit proxy z uses later sessions", R + "loader.py",
     'proxy["level"].loc[:stamp]', 'proxy["level"]', [LD, AU]),
    ("HMM fit slope known at t, not at the month start", R + "loader.py",
     "            first = AsOf.for_session(_first_session(month.date(), cal), config=cfg.calendar)",
     "            first = point", [LD, AU]),
    ("panel anchored at the decision (not fixed)", R + "loader.py",
     "        _first_session(rc.train_start, cal), -(rc.vol.percentile_window + _EXTRA_SESSIONS)",
     "        data_dates[0], -(rc.vol.percentile_window + _EXTRA_SESSIONS)", [LD, AU]),
    ("breadth on today's members for every day", R + "loader.py",
     "    members = [_members(view, p.day, rc.universe) for p in points]",
     "    members = [_members(view, view.day, rc.universe) for p in points]", [LD, AU]),
    ("FRED gaps filled without limit", R + "loader.py",
     "    return out.ffill(limit=max_fill) if max_fill > 0 else out", "    return out.ffill()", [LD, AU]),
    # --- store ------------------------------------------------------------------------------------
    ("stored state available at its decision", R + "store.py",
     "            AVAILABLE_AT: pd.Timestamp(bar_available_at(state.data_date, self._calendar)),",
     "            AVAILABLE_AT: pd.Timestamp(bar_available_at(state.as_of, self._calendar)),", [SO, AU]),
    ("other settings' rows read", R + "store.py",
     '        return rows[rows["config_hash"] == self.config_hash]', "        return rows", [SO, AU]),
    ("rewrite appends instead of replacing", R + "store.py",
     '            self._lake.write_table(REGIME_HISTORY.name, frame, mode="upsert")',
     '            self._lake.write_table(REGIME_HISTORY.name, frame, mode="append")', [SO, AU]),
    ("latest returns the oldest", R + "store.py",
     '        return self._state(known.sort_values("date").iloc[-1])',
     '        return self._state(known.sort_values("date").iloc[0])', [SO, AU]),
    # --- DataView additions -------------------------------------------------------------------
    ("macro_vintages not point in time", "src/atlas/data/macro/pit.py",
     '    known = pit_filter(observations[observations["series_id"].isin(ids)], as_of)',
     '    known = observations[observations["series_id"].isin(ids)]', [VIEW, LD, AU]),
    # --- CLI (gap fix: persist the history) -------------------------------------------------------
    ("CLI end not clipped to the data", "src/atlas/cli/regime_cmd.py",
     '    return min(day, cal.next_session(max(bars["date"])))', "    return day", [CLI]),
    ("CLI ignores --store", "src/atlas/cli/regime_cmd.py",
     "    target = Lake(store) if store is not None else lake", "    target = lake", [CLI]),
    ("members_on accepts a later day", "src/atlas/data/view.py",
     "        if day > self.day:\n            raise ValueError", "        if False:\n            raise ValueError", [VIEW, AU]),
]  # fmt: skip


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
