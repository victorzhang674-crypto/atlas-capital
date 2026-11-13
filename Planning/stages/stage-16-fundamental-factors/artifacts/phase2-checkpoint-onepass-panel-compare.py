"""Phase 2 checkpoint (Tier 2 item 7, Tier 3 items 1-2): what the three changes do to the
factor panels, read only.

    uv run python <this file> <old lake> <new lake> YYYY-MM-DD [YYYY-MM-DD ...]

`old lake` holds the panels stored before the change (`factor_exposures`, read as stored);
on `new lake` (re-parsed fundamentals, sectors filled) the panel of each date is computed
with the current code. Per date it prints: market caps before and after and whether any
earlier cap moved; each raw factor's count and whether earlier values moved; net debt /
EBITDA against net debt / EBIT; how close a weighted-average share count is to a cover-page
count where a company has both; the names whose cap comes from an average; sectors.
"""

import sys
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors import (
    FACTOR_NAMES,
    HISTORY_CONCEPTS,
    adjusted_shares,
    average_shares,
    factor_panel,
)
from atlas.quant.factors.fundamental import _split_lookback

config = load_config("dev")
old_lake, new_lake = Lake(sys.argv[1]), Lake(sys.argv[2])
days = [date.fromisoformat(d) for d in sys.argv[3:]]
staleness = config.data.fundamentals.max_staleness_days
view: DataView | None = None
for day in days:
    began = time.perf_counter()
    view = DataView(new_lake, day, config) if view is None else view.advance(day)
    panel = factor_panel(view)
    took = time.perf_counter() - began
    stored = old_lake.read_table("factor_exposures", filters=[("date", "==", day)])
    old_raw = stored.pivot(index="symbol", columns="factor", values="raw").reindex(
        columns=list(FACTOR_NAMES)
    )
    names = list(panel.raw.index)
    print(f"\n===== {day}: {len(names)} names (stored before: {len(old_raw)}), panel in {took:.1f} s")
    if sorted(names) != sorted(old_raw.index):
        print(f"  names differ: only new {sorted(set(names) - set(old_raw.index))[:10]}, "
              f"only old {sorted(set(old_raw.index) - set(names))[:10]}")
    old = old_raw.reindex(names)
    new = panel.raw
    print("  factor              before  after  earlier values moved (max abs diff)")
    for name in FACTOR_NAMES:
        both = old[name].notna() & new[name].notna()
        moved = float((old.loc[both, name] - new.loc[both, name]).abs().max()) if both.any() else 0.0
        lost = int((old[name].notna() & new[name].isna()).sum())
        note = f"{moved:.3g}" + (f", {lost} lost" if lost else "")
        print(f"  {name:19s} {int(old[name].notna().sum()):6d} {int(new[name].notna().sum()):6d}  {note}")
    market = panel.market
    sources = market["shares_source"].fillna("none").value_counts().to_dict()
    print(f"  share count from: {sources}; market caps: {int(market['market_cap'].notna().sum())}")
    both = old["net_debt_ebitda"].notna() & new["net_debt_ebitda"].notna()
    print(f"  net debt / EBIT (before) median {old.loc[both, 'net_debt_ebitda'].median():.2f}, "
          f"net debt / EBITDA (after) median {new.loc[both, 'net_debt_ebitda'].median():.2f} "
          f"on the {int(both.sum())} names with both; "
          f"{int((old['net_debt_ebitda'].isna() & new['net_debt_ebitda'].notna()).sum())} gained, "
          f"{int((old['net_debt_ebitda'].notna() & new['net_debt_ebitda'].isna()).sum())} lost")

    # how good is an average as a count? names that have both, each fresh
    history = view.fundamentals_history(names, HISTORY_CONCEPTS)
    splits = view.split_factors(names, _split_lookback(view, config.quant.factors, staleness))
    floor = day - timedelta(days=staleness)

    def latest(frame: pd.DataFrame) -> pd.Series:
        fresh = frame[(frame["period_end"] >= floor) & (frame["period_end"] <= day)]
        return fresh.groupby("symbol")["shares"].last()

    counts = latest(adjusted_shares(history, splits))
    averages = latest(average_shares(history, splits))
    pair = pd.concat([counts.rename("count"), averages.rename("average")], axis=1).dropna()
    ratio = pair["average"] / pair["count"]
    q = ratio.quantile([0.01, 0.05, 0.5, 0.95, 0.99]).round(4).tolist()
    off = ratio[(ratio - 1).abs() > 0.10].sort_values()
    print(f"  average / cover-page count on {len(pair)} names with both: "
          f"1% {q[0]}, 5% {q[1]}, median {q[2]}, 95% {q[3]}, 99% {q[4]}; "
          f"{int(((ratio - 1).abs() <= 0.05).sum())} within 5%, {len(off)} more than 10% off")
    if len(off):
        print("    more than 10% off: " + ", ".join(f"{s} {r:.2f}" for s, r in off.items()))
    used = market[market["shares_source"] == "average"].sort_values("market_cap", ascending=False)
    print(f"  cap from an average ({len(used)}): "
          + ", ".join(f"{s} {c / 1e9:.0f}bn" for s, c in used["market_cap"].items()))
    none = market[market["market_cap"].isna()]
    print(f"  still no cap ({len(none)}): no price {sorted(none[none['price'].isna()].index)}; "
          f"no count {sorted(none[none['price'].notna()].index)}")
    old_view = DataView(old_lake, day, config)
    had = sum(old_view.instrument(s).sector is not None for s in names)
    print(f"  names with a sector: {had} before, {int(panel.sectors.notna().sum())} after")
