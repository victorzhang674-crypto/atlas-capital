"""Apply one mutation at a time to the Stage 15 code, run the named tests, restore."""
import subprocess, sys, os
from pathlib import Path

T = "src/atlas/quant/features/technical.py"
R = "src/atlas/quant/features/registry.py"
SN = "src/atlas/quant/features/snapshot.py"
F = "tests/unit/quant/features/"
MUTATIONS = [
    ("vol ddof 1 -> 0", T, "_rolling(panel.returns, n).std(ddof=1)", "_rolling(panel.returns, n).std(ddof=0)", F + "test_technical_features.py"),
    ("EMA alpha 2/(n+1) -> 2/n", T, "return smooth(x, n, 2.0 / (n + 1))", "return smooth(x, n, 2.0 / n)", F + "test_technical_features.py"),
    ("Wilder alpha 1/n -> 1/(n+1)", T, "return smooth(x, n, 1.0 / n)", "return smooth(x, n, 1.0 / (n + 1))", F + "test_technical_features.py"),
    ("smoother skips NaN state rule", T, "update = (first < t) & ~np.isnan(current)", "update = first < t", F + "test_technical_features.py"),
    ("Bollinger population -> sample std", T, "mid, sd = window.mean(), window.std(ddof=0)", "mid, sd = window.mean(), window.std(ddof=1)", F + "test_technical_features.py"),
    ("max drawdown skips first bar", T, "for j in range(1, window):", "for j in range(2, window):", F + "test_technical_features.py"),
    ("idio vol ddof 2 -> 1", T, "np.sqrt(ssr.clip(lower=0.0) / (window - 2))", "np.sqrt(ssr.clip(lower=0.0) / (window - 1))", F + "test_technical_features.py"),
    ("volume z includes today", T, "past = _rolling(log_dv.shift(1), 21)", "past = _rolling(log_dv, 21)", F + "test_technical_features.py"),
    ("momentum skips 22 not 21", T, "return panel.close.shift(21) / panel.close.shift(252) - 1", "return panel.close.shift(22) / panel.close.shift(252) - 1", F + "test_technical_features.py"),
    ("LOOK-AHEAD: gap uses next close", T, 'lambda: panel.open / panel.close.shift(1) - 1', 'lambda: panel.open / panel.close.shift(-1) - 1', F + "test_feature_lookahead.py"),
    ("LOOK-AHEAD: centred SMA", T, 'return panel.memo(("sma", n), lambda: _rolling(panel.close, n).mean())', 'return panel.memo(("sma", n), lambda: panel.close.rolling(n, min_periods=n, center=True).mean())', F + "test_feature_lookahead.py"),
    ("LOOK-AHEAD: market window shifted forward", T, "r, m = panel.returns, panel.benchmark_returns", "r, m = panel.returns.shift(-1), panel.benchmark_returns.shift(-1)", F + "test_feature_lookahead.py"),
    ("lookback buffer dropped", R, "n = max(spec.lookback for spec in specs) + settings.buffer_sessions + history - 1", "n = max(spec.lookback for spec in specs) + history - 1", F + "test_feature_compute.py"),
    ("blocked symbols kept", R, "[s for s in wanted if s not in blocked]", "wanted", F + "test_feature_compute.py"),
    ("snapshot mid-rank -> upper rank", SN, "out = (below + 0.5 * equal) / count", "out = (below + equal) / count", F + "test_feature_snapshot.py"),
    ("snapshot USD features ranked", SN, "if spec.comparable:", "if True:", F + "test_feature_snapshot.py"),
]
env = dict(os.environ)
failures = 0
for label, path, old, new, tests in MUTATIONS:
    p = Path(path)
    original = p.read_text()
    assert original.count(old) == 1, (label, old)
    p.write_text(original.replace(old, new))
    try:
        res = subprocess.run(["uv", "run", "pytest", tests, "-q", "-x", "-p", "no:cacheprovider"], capture_output=True, text=True, env=env)
    finally:
        p.write_text(original)
    last = [l for l in res.stdout.splitlines() if "passed" in l or "failed" in l or "error" in l.lower()][-1:]
    caught = res.returncode != 0
    failures += not caught
    print(f"{'CAUGHT ' if caught else 'MISSED '} {label:45s} -> {last[0] if last else res.returncode}")
print(f"mutations: {len(MUTATIONS)}, caught: {len(MUTATIONS) - failures}, missed: {failures}")
sys.exit(1 if failures else 0)
