"""Stage 29 follow-up 2 (E6): read the `filing_text` table back from the main lake (read-only).

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_filing_text_readback.py
"""

import json
from datetime import date
from pathlib import Path

import atlas.cli.app  # noqa: F401  (registers every table)
from atlas.agents.tools import fundamental_tools as ft
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView

ROOT = Path(__file__).resolve().parents[4]
MAIN = Path.home() / "Projects" / "atlas-capital" / "data"
BACKUP = MAIN / "cache" / "backup-stage29-filing-text-2026-11-18"

old = json.loads((BACKUP / "_meta.json").read_text())["tables"]
new = json.loads((MAIN / "curated" / "_meta.json").read_text())["tables"]
print("_meta.json: tables added", sorted(set(new) - set(old)), "| others unchanged:",
      all(new[k] == v for k, v in old.items()))
old_s = json.loads((BACKUP / "sync_state.json").read_text())["steps"]
new_s = json.loads((MAIN / "cache" / "sync_state.json").read_text())["steps"]
print("sync_state: steps added", sorted(set(new_s) - set(old_s)), "| others unchanged:",
      all(new_s[k] == v for k, v in old_s.items()))

cfg = load_config("dev", overrides={"data": {"lake_path": str(MAIN)}}, config_dir=ROOT / "config")
lake = Lake(MAIN)
rows = lake.read_table("filing_text")
print(f"filing_text: {len(rows)} rows, {rows.symbol.nunique()} symbols, "
      f"{(rows.status == 'ok').sum()} with an MD&A")
for symbol, day in [("ENPH", date(2017, 3, 16)), ("ENPH", date(2017, 3, 17)),
                    ("ENPH", date(2018, 6, 1)), ("FTR", date(2020, 6, 1)),
                    ("AAPL", date(2024, 5, 1)), ("JPM", date(2024, 5, 1)),
                    ("WFC", date(2024, 5, 1))]:
    view = DataView(lake, day, cfg)
    mda = view.mda(symbol)
    flag, periods = ft._going_concern(view, symbol, cfg.agents.fundamental.red_flags)
    print(f"{symbol} at {day}: latest MD&A {mda.form if mda else None} filed "
          f"{mda.filed if mda else None} ({mda.status if mda else None}) -> {flag.status}: "
          f"{flag.detail[:150]} [{periods}]")
