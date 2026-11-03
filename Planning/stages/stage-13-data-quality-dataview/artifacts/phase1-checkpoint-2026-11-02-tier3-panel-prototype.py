"""Prototype: panel values from all facts vs facts with period_end in the staleness window +
margin; exact equality (NaN == NaN) over every member at several as-of dates."""
import sys, time
from datetime import date, timedelta
from pathlib import Path
import numpy as np
from atlas.core.config import load_config
from atlas.core.pit import AsOf
from atlas.core.storage import Lake
from atlas.data.fundamentals import FUNDAMENTALS_FACTS
from atlas.data.fundamentals.mapping import load_concept_map
from atlas.data.fundamentals.pit import fundamentals_pit_detail
from atlas.data.view import DataView
cfg = load_config("dev"); fc = cfg.data.fundamentals
lake = Lake(Path(sys.argv[1])); cmap = load_concept_map(fc.xbrl_map_file)
margin = int(sys.argv[2])
for day in (date(2018, 6, 1), date(2021, 3, 1), date(2024, 6, 3)):
    members = DataView(lake, day, cfg).universe("sp500")
    facts = lake.read_table(FUNDAMENTALS_FACTS.name, filters=[("symbol", "in", list(members))])
    as_of = AsOf.for_session(day, config=cfg.calendar)
    floor = day - timedelta(days=fc.max_staleness_days + margin)
    groups = dict(tuple(facts.groupby("symbol", sort=False)))
    t0 = time.perf_counter(); full = {}
    for s, g in groups.items():
        full[s] = fundamentals_pit_detail(g, as_of, cmap, fc)["value"].to_numpy("float64")
    t1 = time.perf_counter(); cut = {}
    for s, g in groups.items():
        cut[s] = fundamentals_pit_detail(g[g["period_end"] >= floor], as_of, cmap, fc)["value"].to_numpy("float64")
    t2 = time.perf_counter()
    diff = [s for s in groups if not np.array_equal(full[s], cut[s], equal_nan=True)]
    print(f"{day}: {len(groups)} symbols, full {t1-t0:.1f} s, windowed {t2-t1:.1f} s, differing {len(diff)} {diff[:5]}", flush=True)
