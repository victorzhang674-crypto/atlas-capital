"""Phase 2 checkpoint, Tier 4: delete the bars and corporate actions of the reused tickers in
`config/reused_tickers.yaml` dated after their `until` (another company's listing), using
`atlas.data.prices.purge_reused_tickers`. Run from the repo root; the lake is
`data.lake_path` (`ATLAS__DATA__LAKE_PATH` overrides it):

    uv run python <this file>
"""

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.prices import load_reused_tickers, purge_reused_tickers

config = load_config("dev")
lake = Lake(config.data.lake_path)
reused = load_reused_tickers(config.data.prices.reused_tickers_file)
before = lake.read_table("prices_daily", columns=["symbol"])
removed = purge_reused_tickers(lake, reused)
after = lake.read_table("prices_daily", columns=["symbol"])
print(f"lake: {lake.root}")
print(f"listed: {len(reused)}; bars {len(before)} -> {len(after)}")
for symbol in sorted(removed):
    bars, actions = removed[symbol]
    print(f"  {symbol}: {bars} bars, {actions} actions deleted (after {reused[symbol]})")
print(f"total: {sum(b for b, _ in removed.values())} bars, {sum(a for _, a in removed.values())} actions")
