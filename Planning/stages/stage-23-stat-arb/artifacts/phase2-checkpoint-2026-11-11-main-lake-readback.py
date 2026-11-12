"""Phase 2 checkpoint, Tier 1 item 4: read back what `atlas factors build` and
`atlas risk-model build` stored on the main lake, and score the stored risk models out of
sample.

Read only. Run from the repo root with `ATLAS__DATA__LAKE_PATH` pointing at the main lake.
An optional argument names another lake holding the stored tables (a scratch `--store`).

Bias statistic (as in the Stage 19 real-lake check): at each stored month-end model, fixed
portfolios of the model's stocks are held for the next 21 sessions; `sqrt(mean((r_t /
sigma_p) ** 2))` pooled over all months, where `sigma_p` is the stored model's predicted daily
vol and `r_t` the realised daily simple return (a missing return counts as 0, reported).
About 1.00 means calibrated; above 1 the risk was under-predicted.
"""

import sys
from datetime import date

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors import FactorStore
from atlas.quant.risk_model.analytics import portfolio_risk
from atlas.quant.risk_model.factor_model import STYLE_NAMES
from atlas.quant.risk_model.psd import is_psd
from atlas.quant.risk_model.store import RiskModelStore

cfg = load_config()
lake = Lake(cfg.data.lake_path)
stored = Lake(sys.argv[1]) if len(sys.argv) > 1 else lake
cal = TradingCalendar(config=cfg.calendar)
FAR = date(2027, 1, 4)
ok = True

# --- factor cache -------------------------------------------------------------------------
factors = FactorStore(stored, cfg)
days = factors.dates()
print(f"factor_exposures: {len(days)} month-ends {days[0]} .. {days[-1]} (settings {factors.config_hash})")
by_year: dict[int, list[tuple[int, float]]] = {}
for day in days:
    panel = factors.read(day)
    assert panel is not None and panel.date == day
    values = panel.exposures.to_numpy(dtype="float64")
    ok = ok and bool(np.isfinite(values).all())
    by_year.setdefault(day.year, []).append((len(panel.exposures), float(panel.filled.to_numpy().mean())))
for year, rows in sorted(by_year.items()):
    print(
        f"  {year}: {len(rows):2d} month-ends, names {min(n for n, _ in rows)}-{max(n for n, _ in rows)}, "
        f"mean filled share {np.mean([f for _, f in rows]):.3f}"
    )
first = factors.read(cal.offset(days[0], -1))
print(f"  point in time: nothing readable the session before the first month-end -> {first is None}")
ok = ok and first is None

# --- risk model ---------------------------------------------------------------------------
store = RiskModelStore(stored, cfg)
returns = store.read_factor_returns(FAR)
assert returns is not None
shots = store.snapshot_dates()
print(
    f"factor_returns: {len(returns)} sessions {returns.index[0].date()} .. {returns.index[-1].date()}, "
    f"{returns.shape[1]} factors, {int(returns.isna().sum().sum())} missing cells"
)
print(f"risk_model_snapshots: {len(shots)} month-ends {shots[0]} .. {shots[-1]} (settings {store.config_hash})")
ann = returns.std() * np.sqrt(cfg.quant.features.sessions_per_year)
print("  annualised vol of the daily factor returns: " + ", ".join(f"{k} {v:.3f}" for k, v in ann.items()))

view = DataView(lake, cal.next_session(returns.index[-1].date()), cfg)
models = {}
names: set[str] = set()
for d in shots:
    model = store.read_snapshot(cal.next_session(d))
    assert model is not None and model.as_of == d, (d, model and model.as_of)
    models[d] = model
    names |= set(model.symbols) - set(model.etfs)
# the morning of a month-end knows only the previous month's model
earlier = store.read_snapshot(shots[-1])
pit = earlier is not None and earlier.as_of == shots[-2]
print(f"  point in time: on the morning of {shots[-1]} the latest readable model is {earlier and earlier.as_of} -> {pit}")
ok = ok and pit

n_sessions = len(cal.sessions(shots[0], returns.index[-1].date())) + 30
rets = view.returns(sorted(names), n_sessions)
rng = np.random.default_rng(19)
pooled: dict[str, list[float]] = {}
n_missing = n_cells = 0
by_year_cov: dict[int, list[tuple[int, int]]] = {}
for d, model in models.items():
    stocks = [s for s in model.symbols if s not in set(model.etfs)]
    by_year_cov.setdefault(d.year, []).append((len(stocks), len(model.etfs)))
    cov = model.cov(stocks).to_numpy()
    ok = ok and bool(is_psd(cov))
    later = rets.loc[(rets.index > pd.Timestamp(d)) & (rets.index <= pd.Timestamp(cal.offset(d, 21)))]
    if later.empty:
        continue
    block = later.reindex(columns=stocks)
    n_missing += int(block.isna().sum().sum())
    n_cells += block.size
    block = block.fillna(0.0)
    caps = pd.Series(np.exp(model.exposures.loc[stocks, "size"].to_numpy()), index=stocks)
    ports = {
        "cap_weighted": caps / caps.sum(),
        "equal_weighted": pd.Series(1.0 / len(stocks), index=stocks),
    }
    for k in range(10):
        w = pd.Series(rng.normal(size=len(stocks)), index=stocks)
        ports[f"random_long_short_{k}"] = w / w.abs().sum()
    for style in STYLE_NAMES:
        w = model.exposures.loc[stocks, style]
        ports[f"tilt_{style}"] = w / w.abs().sum()
    for name, w in ports.items():
        sigma = portfolio_risk(model, w).total_vol
        key = "random_long_short" if name.startswith("random") else name
        pooled.setdefault(key, []).extend(((block @ w) / sigma).tolist())
    singles = block.to_numpy() / model.vol().reindex(stocks).to_numpy()
    pooled.setdefault("single_names", []).extend(singles.ravel().tolist())

for year, rows in sorted(by_year_cov.items()):
    print(
        f"  {year}: {len(rows):2d} snapshots, stocks {min(n for n, _ in rows)}-{max(n for n, _ in rows)}, "
        f"ETFs {min(e for _, e in rows)}-{max(e for _, e in rows)}"
    )
print(f"every snapshot's stock covariance is PSD; realised-return cells {n_cells}, missing (counted as 0) {n_missing} ({n_missing / max(n_cells, 1):.2%})")
print("bias statistic sqrt(mean z^2), pooled out of sample over the 21 sessions after each snapshot (1.00 = calibrated):")
for key, values in pooled.items():
    print(f"  {key:22s} {float(np.sqrt(np.mean(np.square(values)))):6.3f}  (n={len(values)})")
print("READ-BACK OK" if ok else "READ-BACK FAILED")
raise SystemExit(0 if ok else 1)
