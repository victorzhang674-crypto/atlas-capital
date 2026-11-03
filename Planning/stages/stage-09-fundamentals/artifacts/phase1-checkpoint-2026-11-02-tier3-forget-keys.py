"""Forget the sync checkpoint of the 78 names given a CIK in the Phase 1 checkpoint (Tier 3).

They were synced on 2026-11-01 without a CIK (`empty`), and an `empty` key is re-checked only
after `data.sync.empty_retry_days`; removing their entries is the documented way to re-fetch
(docs/data.md, "Start over"). Steps `fundamentals`, `earnings` and `reactions` only; the file is
copied to `sync_state.json.bak-phase1-tier3` first. Stored data is untouched (the re-sync
upserts). Usage, from the main checkout: uv run python <this file> data/cache/sync_state.json
"""

import json
import re
import shutil
import sys
from pathlib import Path

ART = Path(__file__).parent
STEPS = ("fundamentals", "earnings", "reactions")

state_file = Path(sys.argv[1])
build = (ART / "phase1-checkpoint-2026-11-02-build-cik-overrides-2015.txt").read_text()
symbols = sorted(set(re.findall(r"^== (\S+) \|", build, flags=re.M)))
assert len(symbols) == 78, len(symbols)
backup = state_file.with_name(state_file.name + ".bak-phase1-tier3")
shutil.copy2(state_file, backup)
state = json.loads(state_file.read_text())
removed = {}
for step in STEPS:
    keys = state["steps"].get(step, {})
    removed[step] = sum(keys.pop(s, None) is not None for s in symbols)
tmp = state_file.with_suffix(".json.tmp")
tmp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n")
tmp.replace(state_file)
print(f"backup: {backup}")
print(f"removed entries: {removed}")
print("symbols:", ",".join(symbols))
