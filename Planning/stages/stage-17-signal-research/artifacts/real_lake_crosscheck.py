"""Stage 17: independent cross-check of the real-lake momentum_12_1 tear sheet on 3 dates.

The momentum (C(t-21)/C(t-252) - 1) is rebuilt from `DataView.prices` at t (adjusted closes,
pivoted by hand), the 21-session forward return from `DataView.prices` at a view after t+21,
and the Spearman correlation as pandas' Pearson of pandas' average ranks over the names with
both (pandas' own `method="spearman"` needs scipy, not installed). The result must equal the tear sheet's `ic_series.csv` (h21_spearman). Read only.
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.storage import Lake
from atlas.data.view import DataView

lake = Lake(Path.home() / "Projects/atlas-capital/data")
cal = TradingCalendar()
sheet = pd.read_csv(Path(sys.argv[1]) / "ic_series.csv", index_col="date", parse_dates=True)
worst = 0.0
for t in (date(2017, 3, 1), date(2019, 6, 3), date(2021, 2, 1)):
    view = DataView(lake, t)
    members = view.universe("sp500")
    px = view.prices(members, 253).pivot(index="date", columns="symbol", values="adj_close")
    mom = px.iloc[-22] / px.iloc[-253] - 1  # C(t-21)/C(t-252), the last row is session t-1
    end = cal.offset(t, 21)
    later = DataView(lake, cal.next_session(end))
    fwd_px = later.prices(members, 22).pivot(index="date", columns="symbol", values="adj_close")
    fwd = fwd_px.iloc[-1] / fwd_px.iloc[0] - 1  # close of t -> close of t+21
    assert pd.Timestamp(fwd_px.index[0]) == pd.Timestamp(t), fwd_px.index[0]
    both = pd.concat([mom.rename("m"), fwd.rename("f")], axis=1).dropna()
    ranks = both.rank()  # Spearman = Pearson of average ranks (pandas, not the toolkit)
    mine = ranks["m"].corr(ranks["f"])
    theirs = sheet.loc[pd.Timestamp(t), "h21_spearman"]
    worst = max(worst, abs(mine - theirs))
    print(f"{t}: names {len(both)}  independent {mine:+.6f}  tear sheet {theirs:+.6f}")
print(f"max abs difference {worst:.2e}")
