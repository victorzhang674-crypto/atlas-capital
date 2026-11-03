"""Phase 1 checkpoint: fundamentals_panel with the cross-day cache on the real lake (read-only):
time per monthly rebalance day over 2023-2024, and exact equality with the uncached
`fundamentals_pit_detail` for every member on three of those days."""
import sys, time
from datetime import date
from pathlib import Path
import numpy as np
from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.fundamentals import FUNDAMENTALS_FACTS, fundamentals_pit_detail
from atlas.data.view import DataView
cfg = load_config("dev"); lake = Lake(Path(sys.argv[1])); cal = TradingCalendar(config=cfg.calendar)
days = [cal.next_session(date(y, m, 1)) if not cal.is_session(date(y, m, 1)) else date(y, m, 1)
        for y in (2023, 2024) for m in range(1, 13)]
view = DataView(lake, days[0], cfg)
cmap = view._fundamentals.concept_map
check = {days[0], days[11], days[23]}
total = 0.0
for day in days:
    view = view.advance(day)
    members = view.universe("sp500")
    t = time.perf_counter(); panel = view.fundamentals_panel(members); dt = time.perf_counter() - t
    total += dt
    line = f"{day}: {len(panel)} symbols, {dt:6.1f} s (cached), cache {view._shared.fundamentals_cache.info()}"
    if day in check:
        facts = lake.read_table(FUNDAMENTALS_FACTS.name, filters=[("symbol", "in", list(panel.index))])
        groups = dict(tuple(facts.groupby("symbol", sort=False)))
        point = AsOf.for_session(day, config=cfg.calendar)
        diff = []
        for sym in panel.index:
            if sym not in groups:
                ok = panel.loc[sym].isna().all()
            else:
                ref = fundamentals_pit_detail(groups[sym], point, cmap, cfg.data.fundamentals)["value"].to_numpy("float64")
                ok = np.array_equal(panel.loc[sym].to_numpy("float64"), ref, equal_nan=True)
            if not ok:
                diff.append(sym)
        line += f"; vs uncached: {len(panel) - len(diff)}/{len(panel)} identical {diff[:5]}"
    print(line, flush=True)
print(f"total {total:.1f} s over {len(days)} rebalance days")
