"""Stage 22 Task 5 evidence: in-sample research run of the alpha models on the real lake.

    ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data MPLCONFIGDIR=$TMPDIR/mpl \
    UV_NO_SYNC=1 uv run python -u \
        Planning/stages/stage-22-systematic-alpha/artifacts/research_run.py <data lake> <out dir>

Reads the data lake **read only**. Decision days: the first session of each month from
2016-01-01 whose longest label (63 sessions) closes on or before `backtest.in_sample_end`
(2021-12-31), so no bar after the in-sample end is read (checked: the labels' last session).
At each day, through one shared `DataView` advanced day by day: the multi-factor components
(`component_frame`) and the fixed-weight model (`MultiFactorModel.predict_components`), PEAD
and TSMOM (`predict`). Labels: Stage 17 `forward_returns`. Evaluation: the Stage 17 tear sheet
of the composite score (IC, NW t, quantiles, decay, L/S backtest, by sector and by vol
regime), the components' ICs, the point-in-time IC-weighted composite, PEAD's IC, TSMOM's
time-series Sharpe and hit rate, and the calibration slope of each model's expected returns
against realised (annualised) returns. Writes `summary.json`, `report.txt` and the tear sheet
under <out dir>; a per-date cache (`signals.pkl`) lets the evaluation be re-run. On a cache
hit the multi-factor predictions are **re-scored** from the cached component panels under the
current config (`MultiFactorModel.predict_components`), so a changed weighting is never
reported with the cached scores of an older one (the panels do not depend on the weights).
"""

import json
import math
import pickle
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from atlas.core.calendar import TradingCalendar
from atlas.core.config import AtlasConfig
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.alpha import (
    COMPOSITE,
    ComponentPanel,
    MultiFactorModel,
    PEADModel,
    TSMOMModel,
    calibration_slope,
    combine,
    component_frame,
    ic_history,
    ic_weights,
    trailing_ic,
)
from atlas.quant.research import (
    build_tearsheet,
    evaluation_dates,
    forward_returns,
    ic_series,
    nw_lags,
    summarize_ic,
)

DATA = Path(sys.argv[1]).expanduser()
OUT = Path(sys.argv[2]).expanduser()
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else None  # smoke test: first N days only
START = date(2016, 1, 1)
HORIZONS = (1, 5, 21, 63)
H = 21


def decision_days(cfg: AtlasConfig, cal: TradingCalendar) -> list[date]:
    end = cfg.backtest.in_sample_end
    days = evaluation_dates(START, end, "monthly", cal)
    kept = [d for d in days if cal.offset(d, max(HORIZONS)) <= end]
    return kept[:LIMIT] if LIMIT else kept


def compute(cfg: AtlasConfig, lake: Lake, days: list[date]) -> dict[str, Any]:
    cache = OUT / "signals.pkl"
    if cache.exists():
        with cache.open("rb") as fh:
            cached: dict[str, Any] = pickle.load(fh)  # noqa: S301 - our own file
        assert sorted(cached["components"]) == days, "cache holds other decision days"
        base = DataView(lake, days[0], cfg)
        mf = MultiFactorModel()
        for i, day in enumerate(days):
            view = base if i == 0 else base.advance(day)
            panel = ComponentPanel(
                values=cached["components"][day],
                sectors=cached["sectors"][day],
                resid_vol=cached["resid_vol"][day],
            )
            cached["mf"][day] = mf.predict_components(view, panel)
        print("cache hit: multi-factor re-scored under the current config", flush=True)
        return cached
    base = DataView(lake, days[0], cfg)
    mf, pead, tsmom = MultiFactorModel(), PEADModel(), TSMOMModel()
    out: dict[str, Any] = {
        k: {} for k in ("components", "sectors", "resid_vol", "mf", "pead", "tsmom")
    }
    for i, day in enumerate(days):
        t0 = time.time()
        view = base if i == 0 else base.advance(day)
        panel = component_frame(view)
        out["components"][day] = panel.values
        out["sectors"][day] = panel.sectors
        out["resid_vol"][day] = panel.resid_vol
        out["mf"][day] = mf.predict_components(view, panel)
        out["pead"][day] = pead.predict(view)
        out["tsmom"][day] = tsmom.predict(view)
        print(
            f"{day} members={len(panel.values)} mf={len(out['mf'][day])} "
            f"pead={len(out['pead'][day])} tsmom={len(out['tsmom'][day])} "
            f"{time.time() - t0:.1f}s",
            flush=True,
        )
    OUT.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as fh:
        pickle.dump(out, fh)
    return out


def panel_of(frames: dict[date, pd.DataFrame], column: str) -> pd.DataFrame:
    days = sorted(frames)
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    return pd.DataFrame({ts: frames[d][column] for ts, d in zip(index, days, strict=True)}).T.reindex(
        index
    ).astype("float64")


def demeaned(frame: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    """Each row minus its mean over the names where `mask` is set (the scored names)."""
    kept = frame.where(mask)
    return kept.sub(kept.mean(axis=1), axis=0)


def summary_dict(series: pd.Series, lags: int) -> dict[str, float | int | None]:
    """`summarize_ic` as a JSON-friendly dict (NaN -> None)."""
    raw = summarize_ic(series, lags).to_dict()
    return {k: None if isinstance(v, float) and math.isnan(v) else v for k, v in raw.items()}


def main() -> None:
    cfg = AtlasConfig()
    cal = TradingCalendar(config=cfg.calendar)
    lake = Lake(DATA)
    days = decision_days(cfg, cal)
    print(f"{len(days)} decision days {days[0]} .. {days[-1]}", flush=True)
    t0 = time.time()
    sig = compute(cfg, lake, days)
    print(f"signals computed in {time.time() - t0:.0f}s", flush=True)
    index = pd.DatetimeIndex(pd.to_datetime(days), name="date")
    symbols = sorted(
        {s for d in days for s in sig["components"][d].index}
        | {s for d in days for s in sig["tsmom"][d].index}
    )
    base = DataView(lake, days[0], cfg)
    labels = forward_returns(lambda d: base.advance(d), days, symbols, HORIZONS, config=cfg)
    last_label = pd.Timestamp(labels.daily.index.max()).date()
    assert last_label <= cfg.backtest.in_sample_end, last_label
    assert labels.known_at.day <= cal.next_session(cfg.backtest.in_sample_end)
    y = labels.horizon(H)
    lags = nw_lags(H, days, cal)
    report: dict[str, Any] = {
        "decision_days": {"n": len(days), "first": str(days[0]), "last": str(days[-1])},
        "labels_last_session": str(last_label),
        "labels_known_at": labels.known_at.cutoff.isoformat(),
        "horizon": H,
        "nw_lags": lags,
        "config": cfg.quant.alpha.model_dump(mode="json"),
    }

    # --- multi-factor composite (fixed weights): Stage 17 tear sheet -------------------------
    score = panel_of(sig["mf"], "score")
    exp = panel_of(sig["mf"], "exp_return_ann")
    sectors = pd.concat([sig["sectors"][d] for d in days]).groupby(level=0).last()
    try:
        from atlas.quant.regime import RegimeStore

        states = RegimeStore(lake, cfg).read(days[0], days[-1])
        regimes = pd.Series({pd.Timestamp(s.as_of): s.vol_regime for s in states})
        regimes = regimes.reindex(index)
    except Exception as exc:  # noqa: BLE001 - optional section of the tear sheet
        print(f"no regimes: {exc}", flush=True)
        regimes = None
    sheet = build_tearsheet(
        "alpha_multifactor",
        score,
        labels,
        sectors=sectors,
        regimes=regimes,
        kind="alpha",
        frequency="monthly",
        universe=cfg.quant.alpha.multifactor.universe,
        config=cfg,
    )
    sheet.save(OUT / "tearsheet-multifactor")
    ts = sheet.summary
    report["multifactor"] = {
        "ic_h21_spearman": ts["ic"][f"h{H}"]["spearman"],
        "ic_h21_pearson": ts["ic"][f"h{H}"]["pearson"],
        "ic_all_horizons_spearman": {h: ts["ic"][f"h{h}"]["spearman"] for h in HORIZONS},
        "quintile_spread_h21": {
            k: ts["quantiles"][f"h{H}"][k] for k in ("spread_mean", "spread_t", "monotonicity")
        },
        "backtest": ts["backtest"],
        "by_regime": ts["by_regime"],
        "names_per_date": float(score.notna().sum(axis=1).mean()),
    }
    realised_ann = y * (cfg.quant.features.sessions_per_year / H)
    mask = score.notna()
    fit = calibration_slope(
        exp.to_numpy().ravel(), demeaned(realised_ann, mask).reindex_like(exp).to_numpy().ravel()
    )
    report["multifactor"]["calibration"] = fit.__dict__
    report["multifactor"]["exp_return_ann_abs_mean"] = float(exp.abs().stack().mean())

    # --- components and the point-in-time IC-weighted composite --------------------------------
    comp_ic = ic_history(sig["components"], y)
    comp_ic[COMPOSITE] = ic_series(score, y)
    report["components_ic_h21"] = {c: summary_dict(comp_ic[c], lags) for c in comp_ic.columns}
    mfc = cfg.quant.alpha.multifactor
    fixed = {k: c.weight for k, c in mfc.components.items()}
    z_icw: dict[date, pd.Series] = {}
    sources = []
    for d in days:
        tr = trailing_ic(
            comp_ic.drop(columns=[COMPOSITE]), d, H,
            window_months=mfc.ic_window_months, min_obs=mfc.ic_min_obs, calendar=cal,
        )
        w, source = ic_weights(tr, fixed) if tr["n_obs"].max() >= mfc.ic_min_obs else (fixed, "fixed")
        sources.append(source)
        res = combine(
            sig["components"][d], sig["sectors"][d], w,
            min_coverage=mfc.min_coverage, min_names=mfc.min_names,
        )
        z_icw[d] = res.z
    icw = pd.DataFrame({pd.Timestamp(d): z for d, z in z_icw.items()}).T.reindex(index)
    icw_series = ic_series(icw.astype("float64"), y)
    used = [pd.Timestamp(d) for d, s in zip(days, sources, strict=True) if s == "ic"]
    report["ic_weighted"] = {
        "dates_with_ic_weights": len(used),
        "ic_h21_all_dates": summary_dict(icw_series, lags),
        "ic_h21_ic_weighted_dates_only": summary_dict(icw_series.loc[used], lags) if used else None,
        "fixed_ic_h21_same_dates": summary_dict(comp_ic[COMPOSITE].loc[used], lags) if used else None,
    }

    # --- PEAD -----------------------------------------------------------------------------------
    pead_score = panel_of(sig["pead"], "score").reindex(columns=symbols)
    pead_exp = panel_of(sig["pead"], "exp_return_ann").reindex(columns=symbols)
    pead_ic = ic_series(pead_score.reindex(index), y)
    pmask = pead_score.notna()
    pfit = calibration_slope(
        pead_exp.to_numpy().ravel(), demeaned(realised_ann.reindex_like(pead_exp), pmask).to_numpy().ravel()
    )
    report["pead"] = {
        "ic_h21_spearman": summary_dict(pead_ic, lags),
        "names_per_date": float(pmask.sum(axis=1).mean()),
        "calibration": pfit.__dict__,
    }

    # --- TSMOM (time series) ------------------------------------------------------------------
    t_score = panel_of(sig["tsmom"], "score")
    t_exp = panel_of(sig["tsmom"], "exp_return_ann")
    r21 = y.reindex(index=t_score.index, columns=t_score.columns)
    n_assets = t_score.notna().sum(axis=1)
    book = (t_score.fillna(0.0) * r21.fillna(0.0)).sum(axis=1) / n_assets.where(n_assets > 0)
    periods = cfg.quant.features.sessions_per_year / H
    hits = (np.sign(t_exp) == np.sign(r21)).where(t_exp.notna() & r21.notna() & (t_exp != 0))
    tfit = calibration_slope(
        t_exp.to_numpy().ravel(), (r21 * periods).to_numpy().ravel()
    )
    report["tsmom"] = {
        "assets_per_date": float(n_assets.mean()),
        "book_monthly_mean": float(book.mean()),
        "book_monthly_std": float(book.std(ddof=1)),
        "book_sharpe_ann": float(book.mean() / book.std(ddof=1) * math.sqrt(periods)),
        "book_n_months": int(book.notna().sum()),
        "hit_rate": float(hits.stack().mean()),
        "calibration": tfit.__dict__,
        "ic_h21_cross_section": summary_dict(ic_series(t_score, y, min_names=10), lags),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(report, indent=2, default=str))
    comp_ic.to_csv(OUT / "ic_history_h21.csv")
    print(json.dumps(report, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
