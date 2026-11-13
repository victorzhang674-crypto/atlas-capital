"""Profile one real-lake factor_panel (main lake, read only). argv: as-of date, out prefix."""
import cProfile, pstats, sys, time
from datetime import date
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors import factor_panel

day = date.fromisoformat(sys.argv[1])
cfg = load_config("dev")
view = DataView(Lake(cfg.data.lake_path), day, cfg)
t0 = time.perf_counter()
members = view.universe(cfg.quant.factors.universe)
view.blocked
t1 = time.perf_counter()
prof = cProfile.Profile()
prof.enable()
panel = factor_panel(view)
prof.disable()
t2 = time.perf_counter()
print(f"as-of {day}: members {len(members)}, universe+blocked {t1 - t0:.1f} s, factor_panel {t2 - t1:.1f} s, rows {len(panel.raw)}")
stats = pstats.Stats(prof)
stats.sort_stats("cumulative").print_stats(45)
stats.sort_stats("tottime").print_stats(30)
panel.raw.to_pickle(sys.argv[2] + "-raw.pkl")
panel.exposures.to_pickle(sys.argv[2] + "-exposures.pkl")
