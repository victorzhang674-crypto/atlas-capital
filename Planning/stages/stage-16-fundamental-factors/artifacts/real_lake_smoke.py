"""Stage 16 Task 10: factor_panel on the main checkout's lake (read only). Run with
ATLAS__DATA__LAKE_PATH pointing at the lake."""
import time
from datetime import date
from pathlib import Path
import pandas as pd
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors import factor_panel, FACTOR_NAMES, style_composites

cfg = load_config()
lake = Lake(Path(cfg.data.lake_path))
print("lake:", cfg.data.lake_path)
pd.set_option("display.width", 200)
view = None
for day in (date(2024, 1, 31), date(2026, 9, 30)):
    t = time.perf_counter()
    view = DataView(lake, day, cfg) if view is None else view.advance(day)
    p = factor_panel(view)
    dt = time.perf_counter() - t
    n = len(p.raw)
    print(f"\n=== as-of {day} (cutoff {p.as_of.cutoff.isoformat()}): {n} names, {dt:.1f} s, config_hash {p.config_hash}")
    cov = pd.DataFrame({
        "raw_valid": p.raw.notna().sum(),
        "raw_pct": (100 * p.raw.notna().mean()).round(1),
        "filled": p.filled.sum(),
        "median_raw": p.raw.median().round(4),
        "p01_raw": p.raw.quantile(0.01).round(4),
        "p99_raw": p.raw.quantile(0.99).round(4),
    })
    print(cov.to_string())
    known = p.sectors.notna().to_numpy()
    e = p.exposures[known]
    print("names with a known sector:", int(known.sum()), "without:", int((~known).sum()))
    print("max |mean| exposure (known-sector names):", float(e.mean().abs().max()))
    print("max |std-1| (known-sector names):", float((e.std(ddof=0) - 1).abs().max()))
    print("max |sector sum|:", float(e.groupby(p.sectors[known].to_numpy()).sum().abs().max().max()))
    if (~known).any():
        u = p.exposures[~known]
        print("sector-less names, mean / std of exposures over all factors:",
              round(float(u.stack().mean()), 3), round(float(u.stack().std(ddof=0)), 3))
    print("market cap median USD bn:", round(float(p.market["market_cap"].median()) / 1e9, 2),
          "missing caps:", int(p.market["market_cap"].isna().sum()))
    print("sectors:", p.sectors.fillna("Unknown").value_counts().to_dict())
    print("style composite corr:\n", style_composites(e).corr().round(2).to_string())
    for sym in ("AAPL", "MSFT", "JPM", "NVDA"):
        if sym in p.raw.index:
            r = p.raw.loc[sym]
            print(sym, "cap bn", round(p.market.loc[sym, "market_cap"] / 1e9, 1),
                  {k: round(float(r[k]), 4) for k in ("earnings_yield", "book_yield", "roe", "revenue_growth", "net_issuance", "size")})
