"""Stage 19 real-lake build (read only): daily factor returns for the S&P 500 members over a
window, monthly risk models, and bias statistics of out-of-sample portfolios.

For each session of the window the cross-section is built through `DataView` (monthly
fundamental anchors, daily technical styles, caps rolled with the adjusted close) and fitted by
the constrained WLS. At each month-end data date D (after `--warmup` sessions of factor
returns) the model is built from `style_inputs(DataView at the next session)` and the factor
returns up to D; fixed portfolios are formed from the model's names and held through the next
calendar month: the bias statistic is `sqrt(mean((r_t / sigma_p)**2))` pooled over all months,
where `sigma_p` is the model's predicted daily vol at D and `r_t` the realised daily portfolio
return (missing returns count as 0, reported).

Run from the repository root (read only on the main lake):
    ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data MPLCONFIGDIR=$TMPDIR/mpl \
    UV_CACHE_DIR=$TMPDIR/uv-cache-s19 UV_NO_SYNC=1 uv run python \
    Planning/stages/stage-19-factor-risk-model/artifacts/real_lake_check.py 2018-01-02 2024-12-31 252 6
(arguments: start end warmup-sessions model-every-k-months; set
ATLAS__QUANT__RISK_MODEL__FACTOR_MODEL__ANCHOR_MONTHS=3 for quarterly fundamental anchors)
"""

import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.factors.store import monthly_rebalance_dates
from atlas.quant.risk_model import (
    STYLE_NAMES,
    FactorSet,
    build_model_from_view,
    fit_factor_history,
    load_cross_sections,
    portfolio_risk,
)
from atlas.quant.risk_model.analytics import bias_statistic

start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
warmup = int(sys.argv[3]) if len(sys.argv) > 3 else 252
model_every = int(sys.argv[4]) if len(sys.argv) > 4 else 1  # build a model at every k-th month-end
cfg = load_config()
fm = cfg.quant.risk_model.factor_model
lake = Lake(Path(str(cfg.data.lake_path)).expanduser())
cal = TradingCalendar(config=cfg.calendar)
print(f"# Stage 19 real-lake check · {start} .. {end} · lake {lake.root}")

t0 = time.perf_counter()
xs = load_cross_sections(lake, start, end, cfg)
t1 = time.perf_counter()
print(f"cross-sections: {len(xs)} sessions, names/session {np.mean([len(c.caps) for c in xs]):.0f}, "
      f"load {t1 - t0:.0f} s")
factors = FactorSet.from_config(fm)
hist = fit_factor_history(xs, factors, fm, on_short="skip")
t2 = time.perf_counter()
print(f"regressions: {len(hist.factor_returns)} sessions fitted, skipped {len(hist.skipped)}, "
      f"fit {t2 - t1:.0f} s ({1000 * (t2 - t1) / len(xs):.1f} ms/session); "
      f"R2 mean {hist.r2.mean():.3f} (min {hist.r2.min():.3f}), n_names mean {hist.n_names.mean():.0f}")
fr = hist.factor_returns
print("annualised factor vol (%) over the window:")
print((fr.std() * np.sqrt(252) * 100).round(2).to_string())
print(f"NaN factor-days: {int(fr.isna().sum().sum())}")

rets = pd.DataFrame({c.day: c.returns for c in xs}).T
rets.index = pd.DatetimeIndex(rets.index)
months = [d for d in monthly_rebalance_dates(start, end, cal) if d != end]
months = [d for d in months if (fr.index <= pd.Timestamp(d)).sum() >= warmup][::model_every]
rng = np.random.default_rng(0)
pooled: dict[str, list[float]] = {}
build_times, adjust = [], {}
n_missing = n_cells = 0
for d in months:
    nxt = cal.next_session(d)
    view = DataView(lake, nxt, cfg)
    t = time.perf_counter()
    model = build_model_from_view(view, hist.up_to(d))
    build_times.append(time.perf_counter() - t)
    for step in model.adjustments:
        adjust[step] = adjust.get(step, 0) + 1
    later = rets.loc[rets.index > pd.Timestamp(d)]
    later = later.loc[later.index <= pd.Timestamp(cal.offset(d, 21))]
    if later.empty:
        continue
    names = list(model.symbols)
    block = later.reindex(columns=names)
    n_missing += int(block.isna().sum().sum())
    n_cells += block.size
    block = block.fillna(0.0)
    caps = pd.Series(np.exp(model.exposures["size"].to_numpy()), index=names)
    ports = {
        "cap_weighted": caps / caps.sum(),
        "equal_weighted": pd.Series(1.0 / len(names), index=names),
    }
    for k in range(10):
        w = pd.Series(rng.normal(size=len(names)), index=names)
        ports[f"random_long_short_{k}"] = w / w.abs().sum()
    for style in STYLE_NAMES:
        w = model.exposures[style]
        ports[f"tilt_{style}"] = w / w.abs().sum()
    for name, w in ports.items():
        sigma = portfolio_risk(model, w).total_vol
        z = (block @ w) / sigma
        key = "random_long_short" if name.startswith("random") else name
        pooled.setdefault(key, []).extend(z.tolist())
    singles = block.to_numpy() / model.vol().to_numpy()
    pooled.setdefault("single_names", []).extend(singles.ravel().tolist())
print(f"models built at {len(months)} month-ends; build time mean {np.mean(build_times):.1f} s "
      f"(incl. style_inputs: factor_panel + features); adjustments {adjust}")
print(f"realised-return cells: {n_cells}, missing (counted as 0): {n_missing} ({n_missing / max(n_cells, 1):.2%})")
print("bias statistic sqrt(mean z^2), pooled out-of-sample (1.00 = calibrated):")
rows = {k: (float(np.sqrt(np.mean(np.square(v)))), len(v)) for k, v in pooled.items()}
for k, (b, n) in rows.items():
    print(f"  {k:24s} {b:6.3f}  (n={n})")

# build time of a model from a fitted history (the brief's "build time for one date")
view = DataView(lake, cal.next_session(months[-1]), cfg)
from atlas.quant.risk_model import build_risk_model, style_inputs  # noqa: E402

inputs = style_inputs(view)
tb = time.perf_counter()
model = build_risk_model(hist.up_to(months[-1]), inputs.raw_styles, inputs.sectors, inputs.caps,
                         inputs.data_date, cfg)
print(f"build_risk_model from a fitted history: {len(model.symbols)} names, "
      f"{time.perf_counter() - tb:.3f} s; dropped {len(model.dropped)}; adjustments {model.adjustments}")
