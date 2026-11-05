"""Stage 21 Task 8 evidence: the daily regime history on the real lake (read only).

Usage (from the worktree root):
    UV_NO_SYNC=1 uv run python -u Planning/stages/stage-21-regime-detection/artifacts/real_lake_check.py \
        <data lake> <scratch store lake> [start] [end]

Builds `regime_history` for [start, end] (default 2017-01-03 .. 2022-12-30) from the data lake
through one DataView, writes it to a separate store lake (`RegimeStore`), reads it back, and
prints: label counts per year, the three episodes of plan interpretation 12 against their
pre-registered thresholds, spot days, point-in-time checks and timings.
"""

import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.regime import RegimeStore, regime_at, regime_history, risk_scalar

DATA = Path(sys.argv[1]).expanduser()
OUT = Path(sys.argv[2]).expanduser()
START = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2017, 1, 3)
END = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else date(2022, 12, 30)

EPISODES = {
    "2020 crash": (date(2020, 3, 9), date(2020, 4, 9)),
    "2022 bear": (date(2022, 1, 3), date(2022, 10, 31)),
    "2017 calm": (date(2017, 1, 3), date(2017, 12, 29)),
}


def share(states, pred) -> float:  # type: ignore[no-untyped-def]
    return sum(1 for s in states if pred(s)) / len(states) if states else float("nan")


def main() -> None:
    cfg = AtlasConfig()
    lake = Lake(DATA)
    t0 = time.time()
    states = regime_history(lake, START, END, cfg)
    t_hist = time.time() - t0
    store = RegimeStore(Lake(OUT), cfg)
    written = store.write(states)
    back = store.read(START, END)
    print(
        f"history {START}..{END}: {len(states)} decision days in {t_hist:.1f} s; written {written}; read back equal: {back == states}"
    )
    print(
        f"config_hash {store.config_hash}; stored dates {store.dates()[0]} .. {store.dates()[-1]}"
    )

    print("\n# PIT checks")
    print("every data_date < as_of:", all(s.data_date < s.as_of for s in states))
    latest = store.latest(date(2020, 3, 16))
    print("store.latest(2020-03-16) ->", latest.as_of if latest else None)
    print(
        "store.read(.., as_of=2020-03-16) last ->",
        store.read(date(2020, 3, 1), date(2020, 3, 31), as_of=date(2020, 3, 16))[-1].as_of,
    )

    print("\n# Label counts per year (vol / trend / quadrant / stress / scalar mean)")
    for year in sorted({s.as_of.year for s in states}):
        ys = [s for s in states if s.as_of.year == year]
        vol = Counter(s.vol_regime for s in ys)
        trend = Counter(s.trend_regime for s in ys)
        quad = Counter(s.macro_quadrant for s in ys)
        print(
            year, len(ys), dict(vol), dict(trend), dict(quad),
            f"stress {share(ys, lambda s: s.stress_flag):.2f}",
            f"p_turb>=0.5 {share(ys, lambda s: (s.p_turbulent or 0) >= 0.5):.2f}",
            f"scalar mean {sum(s.risk_scalar for s in ys) / len(ys):.3f} min {min(s.risk_scalar for s in ys):.2f}",
            f"credit src {dict(Counter(s.credit_source for s in ys))}",
            f"missing {dict(Counter(m for s in ys for m in s.missing))}",
        )  # fmt: skip

    print("\n# Episodes (plan interpretation 12; thresholds fixed before the run)")
    results = {}
    for name, (a, b) in EPISODES.items():
        ep = [s for s in states if a <= s.as_of <= b]
        if not ep:
            print(name, "no days in range")
            continue
        m = {
            "days": len(ep),
            "crisis": share(ep, lambda s: s.vol_regime == "CRISIS"),
            "high_or_crisis": share(ep, lambda s: s.vol_regime in ("HIGH", "CRISIS")),
            "stress": share(ep, lambda s: s.stress_flag),
            "down": share(ep, lambda s: s.trend_regime == "DOWN"),
            "p_turb>=0.5": share(ep, lambda s: (s.p_turbulent or 0) >= 0.5),
            "scalar<=0.5": share(ep, lambda s: s.risk_scalar <= 0.5),
            "scalar<1": share(ep, lambda s: s.risk_scalar < 1),
            "min_scalar": min(s.risk_scalar for s in ep),
        }
        results[name] = m
        print(name, a, b, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()})
    checks = []
    if "2020 crash" in results:
        r = results["2020 crash"]
        checks += [
            ("2020 CRISIS >= 50%", r["crisis"] >= 0.5),
            ("2020 stress >= 50%", r["stress"] >= 0.5),
            ("2020 scalar <= 0.5 on >= 50%", r["scalar<=0.5"] >= 0.5),
            ("2020 P(turbulent) >= 0.5 on >= 50%", r["p_turb>=0.5"] >= 0.5),
        ]
    if "2022 bear" in results:
        r = results["2022 bear"]
        checks += [
            ("2022 DOWN >= 50%", r["down"] >= 0.5),
            ("2022 HIGH/CRISIS >= 40%", r["high_or_crisis"] >= 0.4),
            ("2022 scalar < 1 on >= 80%", r["scalar<1"] >= 0.8),
        ]
    if "2017 calm" in results:
        r = results["2017 calm"]
        checks += [
            ("2017 scalar < 1 on <= 25%", r["scalar<1"] <= 0.25),
            ("2017 no CRISIS day", r["crisis"] == 0.0),
        ]
    for label, ok in checks:
        print(("PASS" if ok else "FAIL"), label)
    print("ALL EPISODE CHECKS PASS:", all(ok for _, ok in checks))

    print("\n# Attribution of scalar < 1 in calm 2017 (scalar recomputed without each component)")
    rc = cfg.quant.regime
    calm = [s for s in states if EPISODES["2017 calm"][0] <= s.as_of <= EPISODES["2017 calm"][1]]

    def without(s, drop: str) -> float:  # type: ignore[no-untyped-def]
        return risk_scalar(
            "NORMAL" if drop == "vol" else s.vol_regime,
            "UP" if drop == "trend" else s.trend_regime,
            stress=False if drop == "stress" else s.stress_flag,
            quadrant=None if drop == "quadrant" else s.macro_quadrant,
            p_turbulent=None if drop == "hmm" else s.p_turbulent,
            config=rc,
        )

    for drop in ("vol", "trend", "stress", "hmm", "quadrant"):
        print(f"without {drop}: scalar < 1 on {share(calm, lambda s: without(s, drop) < 1):.3f}")

    print("\n# Spot days")
    by_day = {s.as_of: s for s in states}
    for d in (
        date(2020, 2, 24),
        date(2020, 3, 16),
        date(2020, 3, 23),
        date(2020, 6, 1),
        date(2022, 6, 17),
        date(2022, 10, 13),
        date(2017, 6, 1),
    ):
        s = by_day.get(d)
        if s:
            print(d, s.vol_regime, s.trend_regime, s.macro_quadrant, "stress" if s.stress_flag else "-",
                  None if s.hmm_probs is None else tuple(round(p, 3) for p in s.hmm_probs),
                  f"scalar {s.risk_scalar:.3f}", f"rv {s.realised_vol:.3f}" if s.realised_vol else "",
                  f"vix {s.vix}", f"credit_z {s.credit_z and round(s.credit_z, 2)} ({s.credit_source})",
                  f"nfci {s.nfci}", f"breadth {s.breadth and round(s.breadth, 2)}", s.missing)  # fmt: skip

    print("\n# One-day view equals the history (spot days)")
    for d in (date(2020, 3, 16), date(2022, 6, 17)):
        if START <= d <= END:
            t1 = time.time()
            one = regime_at(DataView(lake, d, cfg))
            h = by_day[d]
            diffs = {
                k: (v, getattr(h, k)) for k, v in one.model_dump().items() if v != getattr(h, k)
            }
            same = (one.vol_regime, one.trend_regime, one.macro_quadrant, one.stress_flag, one.risk_scalar) == (
                h.vol_regime, h.trend_regime, h.macro_quadrant, h.stress_flag, h.risk_scalar)  # fmt: skip
            print(
                d,
                "exactly equal:",
                one == h,
                "| labels, stress and scalar equal:",
                same,
                f"({time.time() - t1:.1f} s)",
            )
            for k, (a, b) in diffs.items():
                print(f"   {k}: one-day {a} | history {b}")
            print("   blocked at the one-day view:", sorted(DataView(lake, d, cfg).blocked))


if __name__ == "__main__":
    main()
