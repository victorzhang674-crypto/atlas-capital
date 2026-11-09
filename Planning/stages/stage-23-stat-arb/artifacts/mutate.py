"""Stage 23 audit: hand-written mutations of the pairs package; each must be killed by
`tests/unit/quant/statarb` (a failing test). Restores every file afterwards. The state machine
(`signals.py`, acceptance "fully tested") gets one mutant per transition rule and boundary.

    UV_NO_SYNC=1 uv run python Planning/stages/stage-23-stat-arb/artifacts/mutate.py

Never run tests, real-lake checks or `make check` while this runs: it edits `src/` in place.
"""

import signal
import subprocess
import sys
from pathlib import Path
from types import FrameType

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / "src" / "atlas" / "quant" / "statarb"

MUTANTS = [
    # --- signals.py: the state machine ---------------------------------------------------
    ("signals.py", "if -cfg.stop_z <= z < -cfg.entry_z:", "if -cfg.stop_z <= z <= -cfg.entry_z:",
     "long entry at exactly -entry_z"),
    ("signals.py", "if cfg.entry_z < z <= cfg.stop_z:", "if cfg.entry_z < z:",
     "short entry beyond the stop"),
    ("signals.py", "if -cfg.stop_z <= z < -cfg.entry_z:", "if z < -cfg.entry_z:",
     "long entry beyond the stop"),
    ("signals.py", "if (long and z > -cfg.exit_z) or (not long and z < cfg.exit_z):",
     "if (long and z >= -cfg.exit_z) or (not long and z < cfg.exit_z):", "long exit at -exit_z"),
    ("signals.py", "if (long and z > -cfg.exit_z) or (not long and z < cfg.exit_z):",
     "if (long and z > -cfg.exit_z) or (not long and abs(z) < cfg.exit_z):",
     "no overshoot exit for shorts"),
    ("signals.py", "if abs(z) > cfg.stop_z:", "if abs(z) >= cfg.stop_z:", "stop at exactly stop_z"),
    ("signals.py", "return _LOCKED, Event.STOP_LOSS", "return PairState(), Event.STOP_LOSS",
     "no lock after a stop loss"),
    ("signals.py", "return PairState(), Event.EXIT", "return _LOCKED, Event.EXIT",
     "lock after a normal exit"),
    ("signals.py", "if held >= cfg.time_stop_mult * half_life:",
     "if held > cfg.time_stop_mult * half_life:", "time stop one session late"),
    ("signals.py", "held = state.held + 1", "held = state.held", "held not counted"),
    ("signals.py", "    if broken:\n        return _LOCKED, Event.BREAKDOWN\n", "",
     "no breakdown exit in a position"),
    ("signals.py", "if state.locked or not finite:", "if not finite:", "locked pair re-enters"),
    ("signals.py", "return (_LOCKED, None) if state.locked else (_LOCKED, Event.BREAKDOWN)",
     "return state, None", "broken flat pair not locked"),
    ("signals.py", "    if finite:\n        long = state.state",
     "    if True:\n        long = state.state", "NaN z treated as a number"),
    ("signals.py", "std = roll.std(ddof=1)", "std = roll.std(ddof=0)", "zscore ddof 0"),
    ("signals.py", "sigma = float(np.std(window, ddof=1))", "sigma = float(np.std(window))",
     "latest_zscore ddof 0"),
    ("signals.py", "config.lookback_mult * half_life + 0.5)", "config.lookback_mult * half_life)",
     "lookback rounds down"),
    ("signals.py", "move = (abs(z) - exit_z) * sigma / (1.0 + abs(hedge_ratio))",
     "move = (abs(z) - exit_z) * sigma", "exp return per leg, not per gross"),
    ("signals.py", "return float(min(max(1.0 - pvalue / config.breakdown_pvalue, 0.0), 1.0))",
     "return float(min(max(1.0 - pvalue / config.max_pvalue, 0.0), 1.0))", "confidence scale"),
    ("signals.py", "out[sig.short] = out.get(sig.short, 0.0) - half",
     "out[sig.short] = out.get(sig.short, 0.0) - half * sig.hedge_ratio", "beta-weighted short"),
    ("signals.py", "long, short = (pair.y, pair.x) if state is SpreadState.LONG else (pair.x, pair.y)",
     "long, short = (pair.y, pair.x)", "short spread legs not swapped"),
    ("signals.py", "for i in range(start, len(y), cfg.breakdown_every):",
     "for i in range(start, len(y), 1):", "breakdown re-tested every session"),
    # --- selection.py ----------------------------------------------------------------------
    ("selection.py", "if check_corr and not rho > cfg.min_corr:", "if False:", "no prefilter"),
    ("selection.py", "    return max(values)\n", "    return min(values)\n",
     "order-dependent EG (min p)"),
    ("selection.py", "if not beta > 0:", "if False:", "negative hedge ratio kept"),
    ("selection.py", "if not cfg.min_half_life <= hl <= cfg.max_half_life:",
     "if not hl <= cfg.max_half_life:", "no half-life lower bound"),
    ("selection.py", "if not h < cfg.max_hurst:", "if False:", "no Hurst filter"),
    ("selection.py", "if uses[pair.y] >= cfg.max_pairs_per_name or",
     "if uses[pair.y] > cfg.max_pairs_per_name or", "per-name cap off by one"),
    ("selection.py", "passed.sort(key=lambda p: (p.pvalue, p.half_life, p.pair_id))",
     "passed.sort(key=lambda p: p.pair_id)", "ranking by id"),
    ("selection.py", "return -math.log(2.0) / math.log(phi)", "return -math.log(2.0) / (phi - 1.0)",
     "half-life linearised"),
    ("selection.py", "lags = np.arange(2, max_lag + 1)", "lags = np.arange(1, max_lag + 1)",
     "Hurst from lag 1"),
    ("selection.py", "window = log_prices.iloc[-cfg.formation :]", "window = log_prices",
     "whole history as the window"),
    ("selection.py", "out.setdefault(str(sector), []).append(sym)",
     "out.setdefault(\"all\", []).append(sym)", "pairs across sectors"),
    # --- backtest.py ----------------------------------------------------------------------
    ("backtest.py", "lp[lo:k, col[p.y]] - p.hedge_ratio * lp[lo:k, col[p.x]]",
     "lp[lo + 1 : k + 1, col[p.y]] - p.hedge_ratio * lp[lo + 1 : k + 1, col[p.x]]",
     "z uses today's close (look-ahead)"),
    ("backtest.py", "window = lp_frame.iloc[k - cfg.formation : k][cands]",
     "window = lp_frame.iloc[k - cfg.formation + 1 : k + 1][cands]",
     "selection uses today's close (look-ahead)"),
    ("backtest.py", "                    close(pid, k, Event.DESELECTED, math.nan)\n"
     "                    del book[pid]\n", "                    pass\n", "deselected pairs kept"),
    ("backtest.py", "                    held.params_next = pair\n",
     "                    held.params = pair\n", "open pair takes new parameters at once"),
    ("backtest.py", "cost_bps=cfg.cost_bps, sessions_per_year=periods",
     "cost_bps=0.0, sessions_per_year=periods", "no costs"),
    ("backtest.py", "return 0.5 * (r_long - r_short)", "return 0.5 * (r_long + r_short)",
     "trade return sign"),
    ("backtest.py", "start=k - 1,", "start=k,", "breakdown window includes today"),
]


# Fast files first, so `-x` stops early on a kill (the backtest module's fixture is slow).
TEST_FILES = [
    f"tests/unit/quant/statarb/test_statarb_{name}.py"
    for name in ("signals", "stats", "config", "package", "selection", "backtest")
]


def _terminate(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt  # runs the `finally` that restores the mutated file


# Equivalent mutants (no test can kill them): every comparison with NaN is False, so the
# `finite` guard before the z-based exit / stop is redundant; it stays for readability.
EQUIVALENT = {"NaN z treated as a number"}


def main() -> int:
    signal.signal(signal.SIGTERM, _terminate)
    survived = 0
    equivalent_survived = 0
    for fname, old, new, label in MUTANTS:
        path = PKG / fname
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"SKIP (pattern not unique/found): {fname}: {label}")
            survived += 1
            continue
        path.write_text(original.replace(old, new), encoding="utf-8")
        try:
            run = subprocess.run(
                [sys.executable, "-m", "pytest", *TEST_FILES, "-x", "-q", "-p",
                 "no:cacheprovider"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        killed = run.returncode != 0
        if not killed and label in EQUIVALENT:
            equivalent_survived += 1
        else:
            survived += not killed
        lines = [ln for ln in run.stdout.strip().splitlines() if "passed" in ln or "failed" in ln]
        tag = "KILLED " if killed else ("EQUIV.  " if label in EQUIVALENT else "SURVIVED")
        print(f"{tag} {fname}: {label} -> {lines[-1] if lines else ''}", flush=True)
    real = len(MUTANTS) - len(EQUIVALENT)
    print(f"{real - survived}/{real} non-equivalent mutants killed; "
          f"{equivalent_survived}/{len(EQUIVALENT)} equivalent mutant(s) survived as expected")
    return 0 if survived == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
