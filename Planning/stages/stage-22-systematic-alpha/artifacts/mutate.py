"""Stage 22 audit: hand-written mutations of the alpha package; each must be killed by
`tests/unit/quant/alpha` (a failing test). Restores every file afterwards.

    UV_NO_SYNC=1 uv run python Planning/stages/stage-22-systematic-alpha/artifacts/mutate.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / "src" / "atlas" / "quant" / "alpha"

MUTANTS = [
    ("calibration.py", "keep = [calendar.offset(pd.Timestamp(d).date(), horizon) < day",
     "keep = [calendar.offset(pd.Timestamp(d).date(), horizon) <= day", "IC known on its label day"),
    ("calibration.py", "return math.sqrt(periods / horizon)", "return 1.0", "no annualisation"),
    ("calibration.py", "positive = means.where(means > 0, 0.0)", "positive = means.abs()",
     "negative ICs weighted"),
    ("multifactor.py", ".where(coverage >= min_coverage - 1e-12)", "", "no coverage filter"),
    ("multifactor.py", "neutral = neutralize(frame, sectors.reindex(raw.index))",
     "neutral = frame", "component not sector-neutral"),
    ("multifactor.py", "ic = max(mean, 0.0)", "ic = mean", "negative trailing IC flips book"),
    ("multifactor.py", "return style_composites(panel.exposures.mask(panel.filled))",
     "return style_composites(panel.exposures)", "Stage 16 fills kept"),
    ("tsmom.py", "score = (blend * cfg.vol_target / vol)", "score = (blend * cfg.vol_target)",
     "no inverse-vol scaling"),
    ("tsmom.py", "return signs.mean(axis=1).where(count >= min_lookbacks)",
     "return signs.mean(axis=1)", "min_lookbacks ignored"),
    ("pead.py", "return max(0.0, 1.0 - age / decay_sessions)",
     "return max(0.0, 1.0 - age / (2 * decay_sessions))", "slower decay"),
    ("pead.py", "return max(len(calendar.sessions(window_end, day)) - 2, 0)",
     "return max(len(calendar.sessions(window_end, day)) - 1, 0)", "age off by one"),
    ("base.py", "if ((conf < 0) | (conf > 1)).any():", "if False:", "confidence unchecked"),
]


def main() -> int:
    survived = 0
    for fname, old, new, label in MUTANTS:
        path = PKG / fname
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            print(f"SKIP (pattern not unique/found): {label}")
            survived += 1
            continue
        path.write_text(original.replace(old, new), encoding="utf-8")
        try:
            run = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/unit/quant/alpha", "-x", "-q", "-p",
                 "no:cacheprovider"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
        finally:
            path.write_text(original, encoding="utf-8")
        killed = run.returncode != 0
        survived += not killed
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else ""
        print(f"{'KILLED ' if killed else 'SURVIVED'} {fname}: {label} -> {tail}")
    print(f"{len(MUTANTS) - survived}/{len(MUTANTS)} killed")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
