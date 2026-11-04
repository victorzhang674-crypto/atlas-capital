"""Write config/yahoo_no_data.yaml from the main lake's sync checkpoint (Phase 1 checkpoint,
Tier 4, 2026-11-03): every `prices` key the 2026-11-01 backfill recorded `empty` (no bars from
Yahoo, nor from the Alpaca fallback), dated with its last check, plus AVB (Yahoo returned no
bars on 2026-11-02 after the delisting; Stage 07 worklog, Phase 1 checkpoint Tier 1).
Usage: python <this file> <lake>/cache/sync_state.json config/yahoo_no_data.yaml
"""

import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text())
keys = state["steps"]["prices"]
rows = {s: (c["synced_at"][:10], "no bars from Yahoo or Alpaca in the 2026-11-01 backfill")
        for s, c in keys.items() if c["status"] == "empty"}  # fmt: skip
rows["AVB"] = ("2026-11-02", "Yahoo dropped it after the 2026-08 delisting; Alpaca has 2020-07-27 onward")
lines = [
    "# Symbols Yahoo has no daily bars for (Stage 07; list added in the Phase 1 checkpoint,",
    "# 2026-11-03), read by atlas.data.providers.yfinance_provider.load_yahoo_no_data through",
    "# data.prices.yahoo_no_data_file. The yfinance adapter does not request them, as if Yahoo had",
    "# answered with nothing: each costs ~25 s of yfinance retries otherwise, and a former member's",
    "# reused ticker would give another company's prices. With the Alpaca keys set, the fallback",
    "# asks Alpaca instead. Entries: symbol, date last checked, note. Built from the main lake's",
    "# sync checkpoint by Planning/stages/stage-07-price-data/artifacts/",
    "# phase1-checkpoint-2026-11-03-write-yahoo-no-data.py; delete an entry to ask Yahoo again.",
    "",
    f"# The list, by symbol ({len(rows)} entries).",
    "symbols:",
]
for sym in sorted(rows):
    checked, note = rows[sym]
    lines.append(f'  - {{symbol: {sym}, checked: {checked}, note: "{note}"}}')
Path(sys.argv[2]).write_text("\n".join(lines) + "\n")
print(f"wrote {sys.argv[2]}: {len(rows)} symbols")
