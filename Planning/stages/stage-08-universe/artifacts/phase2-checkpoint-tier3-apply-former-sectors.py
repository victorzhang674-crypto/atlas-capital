"""Phase 2 checkpoint, Tier 3 item 1: fill the reviewed sectors of
`config/former_sectors.yaml` into a lake's `instruments` table (`apply_former_sectors`: no
source pages, no index change).

Run from the repo root; the lake is `data.lake_path` (`ATLAS__DATA__LAKE_PATH` overrides it):

    uv run python <this file>
"""

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.universe import apply_former_sectors

config = load_config("dev")
lake = Lake(config.data.lake_path)
before = lake.read_table("instruments")
filled = apply_former_sectors(lake, config=config)
after = lake.read_table("instruments")
print(f"lake: {lake.root}")
print(f"instruments: {len(before)} rows before, {len(after)} after")
print(f"with a sector: {int(before['sector'].notna().sum())} before, {int(after['sector'].notna().sum())} after")
print(f"filled {len(filled)}: {' '.join(filled)}")
old = before.set_index("symbol").sort_index()
new = after.set_index("symbol").sort_index()
same_rest = old.drop(columns="sector").equals(new.drop(columns="sector"))
kept = bool((old["sector"].dropna() == new.loc[old["sector"].dropna().index, "sector"]).all())
print(f"every other column unchanged: {same_rest}; every earlier sector unchanged: {kept}")
