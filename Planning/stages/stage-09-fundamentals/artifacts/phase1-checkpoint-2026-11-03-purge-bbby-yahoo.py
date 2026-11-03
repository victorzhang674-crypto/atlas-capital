"""Delete BBBY's Yahoo scheduled rows (Phase 1 checkpoint, 2026-11-03).

Owner's words: "Yes, delete them" (answering "Also delete BBBY's two wrong Yahoo rows?"). The
BBBY ticker now belongs to Beyond Inc; the 2026-11-05 earnings date Yahoo gave for it is
Beyond's, not the former member Bed Bath & Beyond's. Prints the rows; deletes them only with
--apply, after copying `events` to data/cache/backup-phase1-purge-bbby-2026-11-03/.
Run from a checkout with the lake path set: uv run python <this file> [--apply]
"""

import shutil
import sys

from atlas.core.config import load_config
from atlas.core.storage import Lake
from atlas.data.events import EVENTS

FILTERS = [("symbol", "=", "BBBY"), ("source", "=", "yahoo")]


def main() -> int:
    lake = Lake(load_config().data.lake_path)
    rows = lake.read_table(EVENTS.name, filters=FILTERS)
    print(rows[["symbol", "event_type", "event_date", "event_id", "available_at"]].to_string())
    print(f"rows: {len(rows)}")
    if "--apply" not in sys.argv[1:]:
        print("dry run: nothing deleted (pass --apply)")
        return 0
    backup = lake.cache_dir / "backup-phase1-purge-bbby-2026-11-03"
    if backup.exists():
        raise SystemExit(f"{backup} exists: refusing to overwrite an earlier backup")
    shutil.copytree(lake.table_path(EVENTS.name), backup / EVENTS.name)
    print(f"backup: {backup}")
    deleted = lake.delete_rows(EVENTS.name, FILTERS)
    print(f"{'ok' if deleted == len(rows) else 'MISMATCH'}: deleted {deleted} (expected {len(rows)})")
    return 0 if deleted == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
