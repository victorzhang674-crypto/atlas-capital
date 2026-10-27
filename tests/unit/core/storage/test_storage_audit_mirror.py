"""Tests for the append-only DuckDB mirror of the audit log (Stage 06, task 5)."""

import io
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from atlas.core.audit import AuditEvent, AuditLog, EventType, audit_path
from atlas.core.errors import DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.run_context import run_context
from atlas.core.storage import Database, Lake


@pytest.fixture
def db(tmp_lake: Lake) -> Iterator[Database]:
    database = Database(tmp_lake)
    yield database
    database.close()


def _record(log: AuditLog, n: int, start: int = 0) -> list[AuditEvent]:
    return [
        log.record(EventType.ORDER, "oms", {"k": f"v{i}", "i": i}) for i in range(start, start + n)
    ]


def _count(db: Database) -> int:
    return int(db.query("select count(*) as n from audit_events")["n"].iloc[0])


def test_mirror_inserts_all_events(tmp_lake: Lake, db: Database) -> None:
    with run_context(mode="backtest", as_of=date(2024, 1, 2)) as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        events = _record(log, 3)
    assert db.mirror_audit_log(log.path) == 3

    rows = db.query(
        "select *, payload->>'k' as k from audit_events where run_id = ? order by seq", [ctx.run_id]
    )
    assert rows["seq"].tolist() == [0, 1, 2]
    assert rows["event_id"].tolist() == [e.event_id for e in events]
    assert rows["hash"].tolist() == [e.hash for e in events]
    assert rows["prev_hash"].tolist() == [e.prev_hash for e in events]
    assert rows["k"].tolist() == ["v0", "v1", "v2"]
    assert rows["actor"].tolist() == ["oms"] * 3
    assert rows["event_type"].tolist() == ["ORDER"] * 3
    assert rows["as_of"].tolist() == [date(2024, 1, 2)] * 3
    assert str(rows["ts"].dtype) == "datetime64[us, UTC]"
    assert rows["ts"].tolist() == [pd.Timestamp(e.ts) for e in events]
    assert json.loads(rows["payload"].iloc[0]) == {"i": 0, "k": "v0"}


def test_mirror_is_idempotent_and_incremental(tmp_lake: Lake, db: Database) -> None:
    with run_context() as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        _record(log, 3)
        assert db.mirror_audit_log(log.path) == 3
        assert db.mirror_audit_log(log.path) == 0
        _record(log, 2, start=3)
    assert db.mirror_audit_log(log.path) == 2
    assert _count(db) == 5
    seqs = db.query("select seq from audit_events order by seq")["seq"].tolist()
    assert seqs == [0, 1, 2, 3, 4]


def test_mirror_logs_one_info_line(tmp_lake: Lake, db: Database, tmp_path: Path) -> None:
    with run_context() as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        _record(log, 3)
    log_file = tmp_path / "atlas.log"
    configure_logging(log_file=log_file, console=io.StringIO())
    try:
        with run_context() as mirroring:  # e.g. a later job mirroring an earlier run's log
            db.mirror_audit_log(log.path)
    finally:
        reset_logging()
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    storage = [line for line in lines if line["logger"].startswith("atlas.core.storage")]
    assert [(line["event"], line["level"]) for line in storage] == [("audit.mirror", "info")]
    assert storage[0]["run_id"] == mirroring.run_id
    assert storage[0]["mirrored_run_id"] == ctx.run_id
    assert (storage[0]["inserted"], storage[0]["total"]) == (3, 3)


def test_mirror_keeps_runs_apart(tmp_lake: Lake, db: Database) -> None:
    paths = []
    for _ in range(2):
        with run_context() as ctx:
            log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
            _record(log, 2)
            paths.append(log.path)
    assert [db.mirror_audit_log(p) for p in paths] == [2, 2]
    per_run = db.query("select run_id, count(*) as n from audit_events group by run_id")
    assert sorted(per_run["n"].tolist()) == [2, 2]


def test_mirror_refuses_broken_chain(tmp_lake: Lake, db: Database) -> None:
    with run_context() as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        _record(log, 3)
    lines = log.path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1] = lines[1].replace('"v1"', '"tampered"')
    log.path.write_text("".join(lines), encoding="utf-8")
    with pytest.raises(DataError, match="hash chain"):
        db.mirror_audit_log(log.path)
    assert _count(db) == 0


def test_mirror_detects_rewritten_history(tmp_lake: Lake, db: Database, tmp_path: Path) -> None:
    with run_context() as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        _record(log, 3)
        assert db.mirror_audit_log(log.path) == 3
        # a freshly chained log of the same run with other events (valid on its own)
        forged = AuditLog(tmp_path / "forged" / "audit.jsonl")
        forged.record(EventType.ORDER, "oms", {"k": "forged"})
        _record(forged, 3)
    log.path.write_bytes(forged.path.read_bytes())
    assert AuditLog.verify_chain(log.path)
    with pytest.raises(DataError, match="diverges"):
        db.mirror_audit_log(log.path)
    assert _count(db) == 3


def test_mirror_rejects_a_file_with_several_runs(tmp_lake: Lake, db: Database) -> None:
    path = tmp_lake.runs_dir / "shared" / "audit.jsonl"
    for _ in range(2):
        with run_context():
            _record(AuditLog(path), 1)
    with pytest.raises(DataError, match="run"):
        db.mirror_audit_log(path)
    assert _count(db) == 0


def test_audit_events_is_append_only_via_query(tmp_lake: Lake, db: Database) -> None:
    with run_context() as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        _record(log, 2)
    db.mirror_audit_log(log.path)
    for sql in ("delete from audit_events", "update audit_events set actor = 'x'"):
        with pytest.raises(ValueError, match="SELECT"):
            db.query(sql)
    assert _count(db) == 2


def test_mirror_empty_or_missing_file(tmp_lake: Lake, db: Database) -> None:
    empty = tmp_lake.runs_dir / "r" / "audit.jsonl"
    empty.parent.mkdir(parents=True)
    empty.touch()
    assert db.mirror_audit_log(empty) == 0
    with pytest.raises(DataError, match="missing"):
        db.mirror_audit_log(tmp_lake.runs_dir / "nope" / "audit.jsonl")


def test_read_only_database_cannot_mirror(tmp_lake: Lake, db: Database) -> None:
    with run_context() as ctx:
        log = AuditLog(audit_path(tmp_lake.root, ctx.run_id))
        _record(log, 1)
    db.close()
    with Database(tmp_lake, read_only=True) as reader, pytest.raises(ValueError, match="read-only"):
        reader.mirror_audit_log(log.path)
