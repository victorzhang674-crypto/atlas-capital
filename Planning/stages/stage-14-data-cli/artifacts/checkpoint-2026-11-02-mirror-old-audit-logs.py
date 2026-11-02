"""One-time backfill (Phase 0 checkpoint): copy every run's audit log in the main checkout's
lake into DuckDB `audit_events`. `atlas data` mirrors its own run since 2026-11-02; runs
before that were never copied. Idempotent: `mirror_audit_log` skips events already stored.

Run from the main checkout: `uv run python <this file>`.
"""

from pathlib import Path

from atlas.core.audit import AuditLog
from atlas.core.storage import Database, Lake

LAKE = Path("data")

lake = Lake(LAKE)
logs = sorted(LAKE.glob("runs/*/audit.jsonl"))
print(f"lake {LAKE.resolve()}: {len(logs)} audit logs")
with Database(lake) as db:
    for path in logs:
        print(f"{path.parent.name}: chain valid {AuditLog.verify_chain(path)}, ", end="")
        print(f"inserted {db.mirror_audit_log(path)}")
    rows = db.query(
        "select run_id, count(*) as events, min(ts) as first_ts, max(seq) as last_seq "
        "from audit_events group by run_id order by run_id"
    )
    print(rows.to_string(index=False))
    print("total events", int(rows["events"].sum()))
