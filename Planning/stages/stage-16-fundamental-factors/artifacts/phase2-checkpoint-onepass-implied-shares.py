"""Phase 2 checkpoint, Tier 3 item 2: how close each share count is to the count implied by
TTM net income / TTM diluted EPS (read only). Evidence for `share_count_fallback_max_ratio`.

    uv run python <this file> <lake> YYYY-MM-DD [...]
"""

import sys
from datetime import date, timedelta
import numpy as np, pandas as pd
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors import HISTORY_CONCEPTS
config = load_config("dev"); lake = Lake(sys.argv[1])
st = config.data.fundamentals.max_staleness_days
view = None
for d in sys.argv[2:]:
    day = date.fromisoformat(d)
    view = DataView(lake, day, config) if view is None else view.advance(day)
    names = view.universe("sp500")
    panel = view.fundamentals_panel(names)
    hist = view.fundamentals_history(list(panel.index), HISTORY_CONCEPTS)
    floor = day - timedelta(days=st)
    def latest(concept):
        r = hist[(hist["concept"] == concept) & (hist["period_end"] >= floor) & (hist["period_end"] <= day)]
        return r.sort_values("period_end").groupby("symbol")["value"].last()
    cnt, avg = latest("shares_outstanding"), latest("shares_weighted_average")
    eps, ni = panel["eps_diluted_ttm"], panel["net_income_ttm"]
    implied = (ni / eps).where((eps.abs() >= 0.01) & (ni / eps > 0))
    df = pd.concat([cnt.rename("count"), avg.rename("avg"), implied.rename("implied")], axis=1).reindex(panel.index)
    print(f"\n== {day}: {len(df)} names; implied count known for {int(df['implied'].notna().sum())}")
    for col in ("avg", "count"):
        r = (df[col] / df["implied"]).dropna()
        q = r.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]).round(3).tolist()
        band = {f: int(((r >= 1 / f) & (r <= f)).sum()) for f in (1.25, 1.5, 2.0, 3.0)}
        out = r[(r < 1 / 2) | (r > 2)]
        print(f"  {col}/implied on {len(r)}: quantiles 1/5/25/50/75/95/99% {q}; within factor {band}")
        print(f"     outside a factor 2 ({len(out)}): " + ", ".join(f"{s} {x:.3g}" for s, x in out.sort_values().items()))
    fb = df[df["count"].isna() & df["avg"].notna()]
    r = fb["avg"] / fb["implied"]
    print(f"  fallback names (no fresh count, an average): {len(fb)}; implied missing for {int(r.isna().sum())}: {sorted(r[r.isna()].index)}")
    print("     avg/implied: " + ", ".join(f"{s} {x:.2f}" for s, x in r.dropna().sort_values().items()))
