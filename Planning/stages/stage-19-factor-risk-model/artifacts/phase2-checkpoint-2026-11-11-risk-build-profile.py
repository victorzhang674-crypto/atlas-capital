"""Phase 2 checkpoint, Tier 1 item 4: where does `atlas risk-model build --from 2017 --to 2026`
spend its time and memory after the monthly anchors are loaded? (The first main-lake run was
killed by the system, exit 137, about 17 minutes after the last anchor.)

Read only. Run from the repo root with `ATLAS__DATA__LAKE_PATH` pointing at the main lake:
    python -u <this file> [first] [last]      (TRACE=1: only the cross-sections, with a tracemalloc report)

The slow part before the kill (the Stage 16 `factor_panel` at each of the 119 anchors, about
38 s each) is replaced by a stand-in that reads the stored factor cache: the same names and
fundamental composites, real sectors, made-up caps (seeded). Everything else is the real code:
the daily cross-sections, the fit, and the monthly snapshots with ETFs, written to a scratch
store under $TMPDIR. Prints seconds and peak resident memory per phase; stops itself above `LIMIT_GB`.
"""

import os
import resource
import sys
import threading
import time
import tracemalloc
from datetime import date

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors import FactorStore
from atlas.quant.factors import fundamental as fundamental_mod
from atlas.quant.factors.composite import style_composites
from atlas.quant.features import registry as registry_mod
from atlas.quant.risk_model import loader
from atlas.quant.risk_model.store import RiskModelStore

LIMIT_GB = 6.0
t0 = time.time()


def rss() -> float:
    # peak resident memory so far (bytes on macOS)
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30


def note(what: str) -> None:
    print(f"[{time.time() - t0:7.0f} s] peak rss {rss():5.2f} GB  {what}", flush=True)


def watchdog() -> None:
    while True:
        time.sleep(20)
        if rss() > LIMIT_GB:
            note(f"ABORT: resident memory above {LIMIT_GB} GB")
            os._exit(3)


threading.Thread(target=watchdog, daemon=True).start()

cfg = load_config()
lake = Lake(cfg.data.lake_path)
first = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2017, 1, 3)
last = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 10, 30)
factors = FactorStore(lake, cfg)
rng = np.random.default_rng(19)


def fast_fundamental(view: DataView, symbols: object) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    stored = factors.read(view.as_of)
    assert stored is not None, view.as_of
    comps = style_composites(stored.exposures)[list(loader.FUNDAMENTAL_STYLES)]
    names = list(comps.index)
    caps = pd.Series(np.exp(rng.normal(24.0, 1.2, size=len(names))), index=names)
    return comps, fundamental_mod._sectors(view, names), caps


def timed(module: object, name: str) -> None:
    inner = getattr(module, name)

    def wrapper(*args: object, **kwargs: object) -> object:
        note(f"-> {name}")
        out = inner(*args, **kwargs)
        note(f"<- {name}")
        return out

    setattr(module, name, wrapper)


loader._fundamental = fast_fundamental  # type: ignore[assignment]
timed(loader, "compute_panel")
real_prices = DataView.prices


def prices(self: DataView, symbols: object, lookback: int) -> pd.DataFrame:
    if lookback < 1000:  # the per-snapshot reads: not reported
        return real_prices(self, symbols, lookback)  # type: ignore[arg-type]
    note(f"-> DataView.prices ({len(list(symbols))} symbols x {lookback} sessions)")  # type: ignore[call-overload]
    out = real_prices(self, symbols, lookback)  # type: ignore[arg-type]
    note(f"<- DataView.prices ({len(out)} rows, {out.memory_usage(deep=True).sum() / 2**30:.2f} GB)")
    return out


DataView.prices = prices  # type: ignore[method-assign]
real_from_bars = registry_mod.PricePanel.from_bars.__func__  # type: ignore[attr-defined]


def from_bars(cls: type, *args: object, **kwargs: object) -> object:
    note("-> PricePanel.from_bars")
    out = real_from_bars(cls, *args, **kwargs)
    note("<- PricePanel.from_bars")
    return out


registry_mod.PricePanel.from_bars = classmethod(from_bars)  # type: ignore[method-assign,assignment]

if os.environ.get("TRACE"):
    note(f"start: load_cross_sections {first} .. {last}")
    tracemalloc.start(12)
    sections = loader.load_cross_sections(lake, first, last, cfg)
    note(f"load_cross_sections done: {len(sections)} cross-sections")
    snapshot = tracemalloc.take_snapshot()
    current, peak = tracemalloc.get_traced_memory()
    print(f"traced: {current / 2**20:.0f} MB live, {peak / 2**20:.0f} MB peak; largest live allocations:")
    for stat in snapshot.statistics("traceback")[:6]:
        print(f"  {stat.size / 2**20:8.1f} MB in {stat.count} blocks")
        for line in stat.traceback.format()[-8:]:
            print("      " + line.strip()[:150])
    tracemalloc.stop()
    raise SystemExit(0)

# the whole build into a scratch store: cross-sections, fit, monthly snapshots with ETFs
timed(loader, "fit_factor_history")
scratch = Lake(os.path.join(os.environ["TMPDIR"], f"risk-build-profile-{os.getpid()}"))
store = RiskModelStore(scratch, cfg)
cal = TradingCalendar(config=cfg.calendar)
etfs = DataView(lake, cal.next_session(last), cfg).universe(
    cfg.quant.risk_model.factor_model.etf.universe
)
note(f"start: build_and_store {first} .. {last} with {len(etfs)} ETFs into {scratch.root}")
history, written = loader.build_and_store(lake, store, first, last, cfg, etfs=etfs)
note(
    f"build_and_store done: {len(history.factor_returns)} sessions, {len(history.skipped)} "
    f"skipped, mean R2 {float(history.r2.mean()):.3f} (made-up caps), {len(written)} snapshots "
    f"{written[0]} .. {written[-1]}"
)
print(f"peak resident memory {rss():.2f} GB; PROFILE OK")
