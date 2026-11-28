"""ETF exposure study (Stage 19 follow-up, 2026-11-28): the design and the decision rule are in
`worklog.md` ("pre-registration"), written before this script ran.

Run (read only on the lake):

    ATLAS__DATA__LAKE_PATH=<lake> PYTHONPATH=src python etf_exposure_study.py OUT_DIR

Writes `candidates.csv` (per-candidate metrics on both samples), `monthly.csv` (per month and
candidate) and prints the decision.
"""

import glob
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import yaml

from atlas.core.config import load_config
from atlas.quant.risk_model.factor_model import FactorSet

LAKE = Path(os.environ["ATLAS__DATA__LAKE_PATH"]).expanduser()
HASH = "62592c37a19c"
WINDOW, HORIZON, MIN_OBS = 252, 21, 126
SELECT_END = pd.Timestamp("2022-12-31")
RIDGES = (0.0, 1e-4, 1e-3, 1e-2, 1e-1)
PENALTIES = ("uniform", "scaled")
INCUMBENT = ("uniform", 1e-3)


def read(table: str, columns: list[str] | None = None) -> pd.DataFrame:
    files = sorted(glob.glob(str(LAKE / "curated" / table / "**" / "*.parquet"), recursive=True))
    return ds.dataset(files, format="parquet").to_table(columns=columns).to_pandas()


def load() -> tuple[pd.DataFrame, pd.DataFrame, float]:
    cfg = load_config()
    fm = cfg.quant.risk_model.factor_model
    names = list(FactorSet.from_config(fm).names)
    fr = read("factor_returns")
    fr = fr[fr["config_hash"] == HASH]
    factors = fr.pivot_table(index="date", columns="factor", values="value", aggfunc="last")
    factors = factors[names].dropna(how="any")
    factors.index = pd.DatetimeIndex(pd.to_datetime(factors.index))
    tickers = yaml.safe_load((Path("config") / "universe.yaml").read_text())["macro_etfs"]["tickers"]
    px = read("prices_daily", ["symbol", "date", "adj_close"])
    px = px[px["symbol"].isin(tickers)]
    wide = px.pivot_table(index="date", columns="symbol", values="adj_close", aggfunc="last")
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    rets = wide.sort_index().pct_change(fill_method=None).iloc[1:]
    return factors, rets, fm.specific_floor_vol


def fit(f: np.ndarray, y: np.ndarray, penalty: str, ridge: float) -> np.ndarray:
    """Betas of centred `y` on centred `f` (n x K)."""
    n, k = f.shape
    if penalty == "uniform":
        gram = f.T @ f
        if ridge == 0.0:
            return np.linalg.lstsq(f, y, rcond=None)[0]
        return np.linalg.solve(gram + ridge * float(np.trace(gram)) / k * np.eye(k), f.T @ y)
    scale = np.sqrt((f**2).sum(axis=0))
    z = f / scale
    if ridge == 0.0:
        return np.linalg.lstsq(z, y, rcond=None)[0] / scale
    return np.linalg.solve(z.T @ z + ridge * n * np.eye(k), z.T @ y) / scale


def month_ends(index: pd.DatetimeIndex) -> list[int]:
    """Positions in `index` of each month's last session."""
    s = pd.Series(np.arange(len(index)), index=index)
    return [int(p) for p in s.groupby([index.year, index.month]).last().to_numpy()]


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    factors, rets, floor_vol = load()
    common = factors.index.intersection(rets.index)
    factors, rets = factors.loc[common], rets.loc[common]
    etfs = [c for c in rets.columns if rets[c].notna().sum() >= WINDOW]
    print(f"{len(common)} sessions {common[0].date()}..{common[-1].date()}; {len(etfs)} ETFs: {etfs}")
    fvals, yvals = factors.to_numpy(), rets[etfs].to_numpy()
    candidates = [(p, r) for p in PENALTIES for r in RIDGES]
    rows: list[dict] = []
    prev_id: dict[tuple[str, float, int], np.ndarray] = {}
    for pos in month_ends(factors.index):
        if pos + HORIZON >= len(factors) or pos + 1 < WINDOW:
            continue
        lo = pos + 1 - WINDOW
        fw = fvals[lo : pos + 1]
        fc = fw - fw.mean(axis=0)
        v = np.linalg.svd(fc, full_matrices=False)[2][-1]
        cov_f = np.cov(fw, rowvar=False)
        nxt = slice(pos + 1, pos + 1 + HORIZON)
        f_next = fvals[nxt]
        for j, etf in enumerate(etfs):
            yw = yvals[lo : pos + 1, j]
            ok = ~np.isnan(yw)
            if ok.sum() < max(MIN_OBS, fc.shape[1] + 2):
                continue
            y_next = yvals[nxt, j]
            okn = ~np.isnan(y_next)
            if okn.sum() < HORIZON - 3:
                continue
            f_ok = fw[ok] - fw[ok].mean(axis=0)
            y_ok = yw[ok] - yw[ok].mean()
            for penalty, ridge in candidates:
                beta = fit(f_ok, y_ok, penalty, ridge)
                resid = y_ok - f_ok @ beta
                n, k = f_ok.shape
                spec = max(float(resid @ resid) / (n - k - 1), floor_vol**2)
                sigma = math.sqrt(max(float(beta @ cov_f @ beta), 0.0) + spec)
                err = y_next[okn] - f_next[okn] @ beta
                ident = beta - float(v @ beta) * v
                key = (penalty, ridge, j)
                cos = np.nan
                if key in prev_id:
                    a, b = prev_id[key], ident
                    cos = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
                prev_id[key] = ident
                rows.append(
                    {
                        "month": factors.index[pos],
                        "etf": etf,
                        "penalty": penalty,
                        "ridge": ridge,
                        "sse": float(err @ err),
                        "n": int(okn.sum()),
                        "z2": float(((y_next[okn] / sigma) ** 2).sum()),
                        "cos": cos,
                    }
                )
    df = pd.DataFrame(rows)
    df.to_csv(out / "monthly_by_etf.csv", index=False)
    df["sample"] = np.where(df["month"] <= SELECT_END, "selection", "confirmation")

    def agg(d: pd.DataFrame) -> pd.Series:
        return pd.Series(
            {
                "mse": d["sse"].sum() / d["n"].sum(),
                "vol_bias": math.sqrt(d["z2"].sum() / d["n"].sum()),
                "stability": d["cos"].mean(),
                "months": d["month"].nunique(),
                "etf_months": len(d),
            }
        )

    table = df.groupby(["sample", "penalty", "ridge"]).apply(agg, include_groups=False)
    table.to_csv(out / "candidates.csv")
    pd.set_option("display.width", 200)
    print(table.round(6).to_string())

    sel = df[df["sample"] == "selection"]
    by_month = sel.groupby(["penalty", "ridge", "month"])[["sse", "n"]].sum()
    mse_month = (by_month["sse"] / by_month["n"]).unstack(["penalty", "ridge"])
    mse = table.loc["selection", "mse"]
    best = mse.idxmin()
    keep = []
    for cand in mse.index:
        diff = (mse_month[cand] - mse_month[best]).dropna()
        se = float(diff.std(ddof=1) / math.sqrt(len(diff))) if len(diff) > 1 else 0.0
        if cand == best or float(diff.mean()) <= se:
            keep.append(cand)
    stab = table.loc["selection", "stability"]
    pick = max(keep, key=lambda c: (stab[c], c == INCUMBENT))
    conf = table.loc["confirmation"]
    print(f"\nbest MSE on selection: {best}; within 1 SE: {keep}")
    print(f"pick by the rule: {pick}; incumbent: {INCUMBENT}")
    if pick == INCUMBENT:
        print("DECISION: no change (the incumbent is the pick)")
        return
    ok = (
        conf.loc[pick, "mse"] <= conf.loc[INCUMBENT, "mse"]
        and abs(conf.loc[pick, "vol_bias"] - 1) <= abs(conf.loc[INCUMBENT, "vol_bias"] - 1) + 0.02
        and conf.loc[pick, "stability"] >= conf.loc[INCUMBENT, "stability"]
    )
    print(conf.loc[[pick, INCUMBENT]].round(6).to_string())
    print(f"DECISION: {'CHANGE to ' + str(pick) if ok else 'no change (confirmation test failed)'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
