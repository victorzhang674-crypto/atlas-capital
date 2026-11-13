import sys
import numpy as np, pandas as pd
import atlas.quant.risk_model
from atlas.core.storage import Lake
a, b = Lake(sys.argv[1]), Lake(sys.argv[2])
fa, fb = a.read_table("factor_returns"), b.read_table("factor_returns")
print("config hashes: before", fa["config_hash"].unique().tolist(), "after", fb["config_hash"].unique().tolist())
for name, f in (("before", fa), ("after", fb)):
    d = f.drop_duplicates("date")
    print(f"{name}: mean R2 {d['r2'].mean():.4f}, mean names {d['n_names'].mean():.1f}, by year names", d.groupby(pd.to_datetime(d['date']).dt.year)['n_names'].mean().round(0).astype(int).to_dict())
m = fa.merge(fb, on=["date", "factor"], suffixes=("_b", "_a"))
c = m.groupby("factor").apply(lambda g: np.corrcoef(g["value_b"], g["value_a"])[0, 1], include_groups=False)
vol = m.groupby("factor").agg(vol_b=("value_b", "std"), vol_a=("value_a", "std"))
print((pd.concat([c.rename("corr"), vol], axis=1)).round(4).to_string())
