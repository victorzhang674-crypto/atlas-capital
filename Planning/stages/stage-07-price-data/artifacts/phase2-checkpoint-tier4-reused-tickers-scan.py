import sys
from datetime import timedelta
import pandas as pd
import atlas.data.prices, atlas.data.universe, atlas.data.reference
from atlas.core.storage import Lake
lake = Lake(sys.argv[1])
memb = lake.read_table("index_membership")
inst = lake.read_table("instruments").set_index("symbol")
bars = lake.read_table("prices_daily", columns=["symbol", "date", "close", "vendor"])
acts = lake.read_table("corporate_actions")
first = bars.groupby("symbol")["date"].agg(["min", "max", "count"])
current = set(memb.loc[memb["end_date"].isna(), "symbol"])
spans = memb.groupby("symbol").agg(start=("start_date", "min"), end=("end_date", "max"))
rows = []
for sym, sp in spans.iterrows():
    if sym in current or sym not in first.index or pd.isna(sp["end"]):
        continue
    f = first.loc[sym]
    if f["min"] > sp["end"]:
        n_act = int((acts["symbol"] == sym).sum())
        rows.append((sym, sp["start"], sp["end"], f["min"], f["max"], int(f["count"]), n_act, inst["name"].get(sym)))
out = pd.DataFrame(rows, columns=["symbol", "member_from", "member_to", "first_bar", "last_bar", "bars", "actions", "name"])
pd.set_option("display.width", 220)
print(out.to_string(index=False))
print("total bars", out["bars"].sum(), "actions", out["actions"].sum())
for sym in ("CA", "POM", "AA", "AVB"):
    print(sym, "membership:", memb[memb["symbol"] == sym][["start_date", "end_date"]].values.tolist(),
          "bars:", first.loc[sym].tolist() if sym in first.index else None)
