"""Phase 2 checkpoint, Tier 4: the out-of-sample bias statistic of stored risk-model snapshots
by year (equal-weighted and cap-weighted portfolios of each model's stocks, and single names;
the 21 sessions after each snapshot; as in the Tier 1 read-back script). Read only.

    uv run python <this file> <lake> <risk-model settings hash>

The hash selects the stored rows (an older build's rows carry an older hash).
"""

import sys
from datetime import date

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.risk_model.store import RiskModelStore

cfg = load_config("dev")
lake = Lake(sys.argv[1])
store = RiskModelStore(lake, cfg)
store.config_hash = sys.argv[2]
cal = TradingCalendar(config=cfg.calendar)
shots = store.snapshot_dates()
models = {d: store.read_snapshot(cal.next_session(d)) for d in shots}
names = sorted({s for m in models.values() for s in m.symbols if s not in set(m.etfs)})
view = DataView(lake, cal.next_session(date(2026, 10, 30)), cfg)
rets = view.returns(names, len(cal.sessions(shots[0], date(2026, 10, 30))) + 30)
by_year: dict[int, dict[str, list[float]]] = {}
for d, model in models.items():
    stocks = [s for s in model.symbols if s not in set(model.etfs)]
    later = rets.loc[(rets.index > pd.Timestamp(d)) & (rets.index <= pd.Timestamp(cal.offset(d, 21)))]
    if later.empty:
        continue
    block = later.reindex(columns=stocks).fillna(0.0)
    caps = pd.Series(np.exp(model.exposures.loc[stocks, "size"].to_numpy()), index=stocks)
    out = by_year.setdefault(d.year, {"equal": [], "cap": [], "single": []})
    for key, w in (("equal", pd.Series(1.0 / len(stocks), index=stocks)), ("cap", caps / caps.sum())):
        cov = model.cov(stocks).to_numpy()
        sigma = float(np.sqrt(w.to_numpy() @ cov @ w.to_numpy()))
        out[key].extend(((block @ w) / sigma).tolist())
    out["single"].extend((block.to_numpy() / model.vol().reindex(stocks).to_numpy()).ravel().tolist())
print(f"lake {sys.argv[1]}, settings {sys.argv[2]}: {len(models)} snapshots")
print("year  equal   cap    single")
for year, values in sorted(by_year.items()):
    b = {k: float(np.sqrt(np.mean(np.square(v)))) for k, v in values.items()}
    print(f"{year}  {b['equal']:.3f}  {b['cap']:.3f}  {b['single']:.3f}")
