"""Stage 23 Task 4 evidence: in-sample pairs backtest on the real lake (read only).

    ATLAS__DATA__LAKE_PATH=~/Projects/atlas-capital/data MPLCONFIGDIR=$TMPDIR/mpl \
    LOKY_MAX_CPU_COUNT=4 UV_NO_SYNC=1 uv run python -u \
        Planning/stages/stage-23-stat-arb/artifacts/insample_run.py <data lake> <out dir>

One `DataView` at 2022-01-03 (the session after the in-sample end, so its latest known bar
is 2021-12-31: no 2022 bar is read; checked below). Returns of every name that is an S&P 500
member on any formation date (first session of each month, 2017-01 .. 2021-12), over all
sessions the lake has before that; point-in-time members per formation (`members_on`);
sectors from the instruments table (today's values: a known limitation). Every threshold is
`quant.statarb` as pre-registered in plan.md (nothing tuned on this window). Writes
`summary.json`, `trades.csv`, `selections.csv`, `daily.csv` (net / gross returns, costs,
open pairs) and `report.txt` under <out dir>.
"""

import json
import math
import pickle
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from atlas.core.config import StatArbConfig, load_config
from atlas.core.logging import configure_logging
from atlas.core.storage import Lake
from atlas.data.view import DataView
from atlas.quant.alpha.multifactor import member_sectors
from atlas.quant.metrics import sharpe_ratio
from atlas.quant.research.vector_backtest import rebalance_dates
from atlas.quant.statarb import PairsBacktestResult, pairs_backtest

START, END = date(2017, 1, 1), date(2021, 12, 31)
VIEW_DAY = date(2022, 1, 3)
LOOKBACK = 1800  # sessions: back to the lake's first bars (2015)


def main(lake_path: Path, out: Path) -> int:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    configure_logging(level="WARNING", log_file=out / "run.log", console_level="WARNING")
    config = load_config("dev")
    cfg = config.quant.statarb
    periods = config.quant.features.sessions_per_year
    view = DataView(Lake(lake_path), VIEW_DAY, config)
    grid = view.sessions(LOOKBACK)
    assert grid[-1] == END, f"latest known session {grid[-1]} is not the in-sample end {END}"
    inside = [d for d in grid if START <= d <= END]
    formations = [d.date() for d in rebalance_dates(inside, "monthly")]
    universe: set[str] = set()
    for d in formations:
        universe.update(view.members_on(d, cfg.universe))
    symbols = sorted(universe)
    print(f"[{time.time() - t0:.0f}s] {len(formations)} formations, {len(symbols)} names", flush=True)
    returns = view.returns(symbols, LOOKBACK - 1)
    returns = returns.loc[: pd.Timestamp(END)]
    assert returns.index[-1].date() == END
    have = returns.notna().any()
    returns = returns.loc[:, have]
    print(
        f"[{time.time() - t0:.0f}s] returns {returns.shape}, first {returns.index[0].date()}, "
        f"{int((~have).sum())} names without bars dropped",
        flush=True,
    )
    sectors = member_sectors(view, list(returns.columns))

    def members(day: date) -> list[str]:
        return view.members_on(day, cfg.universe)

    def progress(day: date, n: int) -> None:
        print(f"[{time.time() - t0:.0f}s] formation {day}: {n} pairs", flush=True)

    result = pairs_backtest(
        returns, sectors, start=START, end=END, members=members, config=cfg,
        sessions_per_year=periods, progress=progress,
    )  # fmt: skip
    with (out / "result.pkl").open("wb") as fh:  # keep the run before any reporting step
        pickle.dump(result, fh)
    return report(result, int(returns.shape[1]), out, cfg, periods, t0)


def report(
    result: PairsBacktestResult, n_names: int, out: Path, cfg: StatArbConfig, periods: int,
    t0: float,
) -> int:
    """Write summary.json, trades / selections / daily CSVs and report.txt for `result`."""
    bt = result.backtest
    s = result.summary
    gross_sharpe = sharpe_ratio(bt.gross_returns.to_numpy(), risk_free=0.0, periods=periods)
    s["gross_sharpe"] = gross_sharpe
    s["n_names"] = n_names
    s["sectors_selected"] = dict(Counter(result.selections["sector"]).most_common())
    s["top_pairs"] = dict(Counter(result.selections["pair_id"]).most_common(15))
    s["distinct_pairs"] = int(result.selections["pair_id"].nunique())
    s["pairs_per_formation"] = {
        str(k): int(v) for k, v in result.selections.groupby("formed").size().items()
    }
    by_year = pd.DataFrame({"net": bt.returns, "gross": bt.gross_returns})
    yearly = by_year.groupby(by_year.index.year).agg(
        lambda x: float(np.prod(1 + x) - 1)
    )
    s["yearly"] = {int(y): {k: float(v) for k, v in row.items()} for y, row in yearly.iterrows()}
    s["runtime_s"] = round(time.time() - t0, 1)
    s["view_day"] = VIEW_DAY.isoformat()
    s["config"] = cfg.model_dump()

    def clean(v: object) -> object:
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v

    (out / "summary.json").write_text(json.dumps(clean(s), indent=2, default=str) + "\n")
    result.trades.to_csv(out / "trades.csv", index=False)
    result.selections.to_csv(out / "selections.csv", index=False)
    daily = pd.DataFrame(
        {"net_return": bt.returns, "gross_return": bt.gross_returns, "cost": bt.costs}
    ).join(result.daily)
    daily.to_csv(out / "daily.csv")
    lines = [
        f"Stage 23 in-sample pairs backtest {s['start']} .. {s['end']} (real lake, read only)",
        f"names {s['n_names']}, formations {s['n_formations']}, mean pairs selected "
        f"{s['mean_pairs_selected']:.1f}, distinct pairs {s['distinct_pairs']}",
        f"net: Sharpe {s['sharpe']:.2f}, ann return {s['ann_return']:.2%}, ann vol "
        f"{s['ann_vol']:.2%}, total {s['total_return']:.2%}, max DD {s['max_drawdown']:.2%}",
        f"gross Sharpe {gross_sharpe:.2f}; total cost {s['total_cost']:.2%}; annual turnover "
        f"{s['annual_turnover']:.1f}; mean gross {s['mean_gross']:.3f}; mean open pairs "
        f"{s['mean_open_pairs']:.1f}",
        f"trades {s['n_trades']} (closed {s['n_closed']}), hit rate {s['hit_rate']:.1%}, "
        f"mean trade return {s['mean_trade_return']:.4f} (per unit pair gross), mean held "
        f"{s['mean_held']:.1f} sessions, exits {s['exits']}",
        f"yearly {s['yearly']}",
        f"sectors {s['sectors_selected']}",
        f"runtime {s['runtime_s']} s",
    ]
    (out / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--report":  # re-report a saved run
        target = Path(sys.argv[2])
        with (target / "result.pkl").open("rb") as fh:
            saved = pickle.load(fh)  # noqa: S301 (our own file)
        cfg0 = load_config("dev")
        sys.exit(report(saved, int(saved.summary.get("n_names", 0)), target,
                        cfg0.quant.statarb, cfg0.quant.features.sessions_per_year, time.time()))
    sys.exit(main(Path(sys.argv[1]).expanduser(), Path(sys.argv[2])))
