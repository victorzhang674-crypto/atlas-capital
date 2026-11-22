"""Stage 29 follow-up 3 (H4): read the replaced `filing_text` table back from the main lake
(read-only): only that table and its sync step changed; the 10-K and 10-Q MD&As through
`DataView.mda` and the going-concern check, for 10-Q doubts, the seven companies whose MD&A
was not found before, and the reviewed false positives.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_filing_text_readback_followup3.py [backup suffix]

The backup suffix (default 2026-11-22) names the swap read back: 2026-11-22 for round 1,
2026-11-22b for the H8-H9 re-ingest.

The table is counted in record batches: a whole-table `Lake.read_table` overflows Arrow's 2 GB
string offsets once the text column passes 2 GB (Backlog); `DataView` reads one symbol at a time.
"""

import collections
import json
import sys
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds

import atlas.cli.app  # noqa: F401  (registers every table)
from atlas.agents.tools import fundamental_tools as ft
from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.view import DataView

ROOT = Path(__file__).resolve().parents[4]
MAIN = Path.home() / "Projects" / "atlas-capital" / "data"
SUFFIX = sys.argv[1] if len(sys.argv) > 1 else "2026-11-22"
BACKUP = MAIN / "cache" / f"backup-stage29-filing-text-{SUFFIX}"
print("backup:", BACKUP.name)

old = json.loads((BACKUP / "_meta.json").read_text())["tables"]
new = json.loads((MAIN / "curated" / "_meta.json").read_text())["tables"]
print("_meta.json equal to the backup:", old == new)
old_s = json.loads((BACKUP / "sync_state.json").read_text())["steps"]
new_s = json.loads((MAIN / "cache" / "sync_state.json").read_text())["steps"]
print("sync_state steps changed:", sorted(k for k in set(old_s) | set(new_s)
                                          if old_s.get(k) != new_s.get(k)))

cfg = load_config("dev", overrides={"data": {"lake_path": str(MAIN)}}, config_dir=ROOT / "config")
lake = Lake(MAIN)
for label, root in (("backup (before)", BACKUP / "filing_text"),
                    ("main (now)", MAIN / "curated" / "filing_text")):
    t = ds.dataset(str(root), format="parquet").to_table(
        columns=["symbol", "form", "status"]).to_pandas()
    print(f"{label}: {len(t)} rows, {t.symbol.nunique()} symbols, "
          f"{dict(sorted(collections.Counter(zip(t.form, t.status)).items()))}")

CASES = [
    ("ENPH", date(2017, 8, 10)),   # 10-Q repeating the 10-K's doubt
    ("BBBY", date(2023, 1, 27)),   # doubt first disclosed in a 10-Q, read at once
    ("BBBY", date(2023, 1, 25)),   # the day before: the earlier 10-Q
    ("CHK", date(2019, 11, 6)),    # 10-Q doubt, no 10-K said it
    ("ECHO", date(2026, 8, 4)),    # "does not exist": CLEAR
    ("ZION", date(2026, 11, 20)),  # a borrower's doubt: CLEAR
    ("HOG", date(2026, 11, 20)),   # a subsidiary's (LiveWire), quoted whole
    ("AAPL", date(2024, 5, 1)),
    ("WFC", date(2024, 5, 1)),     # the seven: Financial Review
    ("AEP", date(2024, 5, 1)),
    ("CLX", date(2024, 5, 1)),
    ("MOS", date(2024, 5, 1)),
    ("TWX", date(2017, 6, 1)),
    ("TWC", date(2016, 3, 1)),
    ("WIN", date(2019, 3, 18)),
    ("XOM", date(2026, 11, 20)),   # new holding-company CIK: only its own filings
    # H7-H9 (the re-ingest)
    ("SO", date(2017, 5, 4)),      # combined MD&A read whole, not its pointer page (Toshiba)
    ("ETR", date(2024, 5, 10)),    # "MANAGEMENT'S FINANCIAL DISCUSSION AND ANALYSIS"
    ("HST", date(2020, 5, 11)),    # was the Part II contents entry + notes: now its MD&A, CLEAR
    ("ROP", date(2017, 11, 7)),    # "MANAGMENT'S DISCUSSION" (a 10-Q filed 2017-11-06)
    ("TSS", date(2018, 2, 26)),    # a 10-K pointing to the annual report: its EX-13
]
for symbol, day in CASES:
    view = DataView(lake, day, cfg)
    mda = view.mda(symbol)
    flag, periods = ft._going_concern(view, symbol, cfg.agents.fundamental.red_flags)
    print(f"{symbol} at {day}: {mda.form if mda else None} filed {mda.filed if mda else None} "
          f"({mda.status if mda else None}, {mda.source if mda else None}, "
          f"{len(mda.text) if mda and mda.text else 0} chars) -> {flag.status}: "
          f"{flag.detail[:140]} [{periods}]")
