"""Phase 2 checkpoint (backlog, Stage 13): the stale-price issues of the old rule and of the
tick-aware rule on a lake's bars, at every month-end decision 2015-2026 (read only).

    uv run python <this file> <lake>

Per decision: the bars of the quality report's price window (the last `window_sessions +
outlier_vol_window + stale_min_sessions + 1` sessions known), `check_stale_prices` with
`min_move_ticks=0` (the old rule: every run) and with the configured rule.
"""
import sys
from datetime import date
from collections import Counter

import pandas as pd

import atlas.data.prices
from atlas.core.calendar import TradingCalendar
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.quality import Severity, check_stale_prices
from atlas.quant.factors import monthly_rebalance_dates

config = load_config("dev")
q = config.data.quality
lake = Lake(sys.argv[1])
cal = TradingCalendar(config=config.calendar)
bars = lake.read_table("prices_daily", columns=["symbol", "date", "close", "volume", "available_at"])
bars = bars.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
n = q.window_sessions + q.outlier_vol_window + q.stale_min_sessions + 1
old_all, new_all, removed = Counter(), Counter(), Counter()
for day in monthly_rebalance_dates(date(2015, 1, 1), date(2026, 10, 30), cal):
    first = cal.offset(day, -(n - 1))
    since = cal.offset(day, -(q.window_sessions - 1))
    window = bars[(bars["date"] >= first) & (bars["date"] <= day)]
    old = check_stale_prices(window, since=since, min_sessions=q.stale_min_sessions,
                             severity=Severity.CRITICAL, tick_size=q.tick_size, min_move_ticks=0.0)
    new = check_stale_prices(window, since=since, min_sessions=q.stale_min_sessions,
                             severity=Severity.CRITICAL, tick_size=q.tick_size,
                             min_move_ticks=q.stale_min_move_ticks)
    o, w = {i.symbol for i in old}, {i.symbol for i in new}
    old_all.update(o); new_all.update(w)
    gone = o - w
    removed.update(gone)
    if o:
        print(f"{day}: old {sorted(o)} new {sorted(w)}" + (f"  REMOVED {sorted(gone)}" if gone else ""))
    assert w <= o, (day, w - o)
print(f"\nmonth-end decisions with a stale_price issue: old rule {sum(old_all.values())}, tick-aware {sum(new_all.values())}")
print(f"removed by symbol: {dict(removed)}")
print(f"kept by symbol: {dict(new_all)}")
