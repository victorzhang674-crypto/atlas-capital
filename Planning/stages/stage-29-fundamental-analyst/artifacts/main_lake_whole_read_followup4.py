"""Stage 29 follow-up 4 (J1): read the main lake's whole `filing_text` table with
`Lake.read_table` (read-only). Before J1 this raised Arrow's "offset overflow while
concatenating arrays": the text column passes 2 GB in all.

    uv run python Planning/stages/stage-29-fundamental-analyst/artifacts/main_lake_whole_read_followup4.py [lake root]
"""

import sys
import time
from pathlib import Path

import pyarrow as pa

import atlas.cli.app  # noqa: F401  (registers every table)
from atlas.core.storage import Lake

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Projects" / "atlas-capital" / "data"
lake = Lake(root)
start = time.monotonic()
frame = lake.read_table("filing_text")
text_bytes = int(frame["text"].dropna().map(lambda t: len(t.encode())).sum())
print(f"pyarrow {pa.__version__}; filing_text read whole in {time.monotonic() - start:.1f} s: "
      f"{len(frame)} rows, {frame['symbol'].nunique()} symbols, text {text_bytes / 2**30:.2f} GiB "
      f"(32-bit offsets end at 2 GiB)")
keys = ["symbol", "accn", "section"]
print("sorted by the primary key:", frame[keys].equals(frame[keys].sort_values(keys).reset_index(drop=True)))
print("columns:", list(frame.columns))
