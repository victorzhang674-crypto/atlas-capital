"""Tests for canonical JSON and the hash-chained audit log."""

import contextvars
import hashlib
import json
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from atlas.core.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditLog,
    EventType,
    audit_path,
    canonical_json,
)
from atlas.core.clock import SimClock
from atlas.core.errors import AtlasError
from atlas.core.run_context import run_context
from atlas.core.types.ids import SeededIds, use_ids

# --- canonical JSON -------------------------------------------------------------------------


class _Leg(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    weight: float


def test_sorted_compact() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert canonical_json({"z": {"y": 1, "x": 2}}) == '{"z":{"x":2,"y":1}}'


def test_same_content_same_bytes() -> None:
    first = {"a": 1, "b": {"c": 2, "d": 3}}
    second = {"b": {"d": 3, "c": 2}, "a": 1}
    assert canonical_json(first) == canonical_json(second)


def test_decimal_keeps_digits() -> None:
    assert canonical_json({"x": Decimal("1.10")}) == '{"x":"1.10"}'
    with pytest.raises(ValueError):
        canonical_json({"x": Decimal("NaN")})
    with pytest.raises(ValueError):
        canonical_json({"x": Decimal("Infinity")})


def test_dates_and_datetimes() -> None:
    assert canonical_json(date(2024, 1, 2)) == '"2024-01-02"'
    ny = datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert canonical_json(ny) == '"2024-01-02T14:30:00+00:00"'
    with pytest.raises(ValueError, match="naive"):
        canonical_json(datetime(2024, 1, 2, 9, 30))


def test_enum_and_model() -> None:
    assert canonical_json(EventType.ORDER) == '"ORDER"'
    assert canonical_json(_Leg(symbol="AAPL", weight=0.05)) == '{"symbol":"AAPL","weight":0.05}'


def test_rejects_nan_inf_and_unknown() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_json({"x": bad})
    for unknown in ({1, 2}, object()):
        with pytest.raises(TypeError):
            canonical_json({"x": unknown})


def test_non_ascii_kept() -> None:
    assert canonical_json({"note": "€ 公司"}) == '{"note":"€ 公司"}'


# --- audit log ------------------------------------------------------------------------------


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_lines(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("".join(canonical_json(line) + "\n" for line in lines), encoding="utf-8")


def _expected_hash(event: dict[str, Any]) -> str:
    body = {k: v for k, v in event.items() if k != "hash"}
    return hashlib.sha256((event["prev_hash"] + canonical_json(body)).encode()).hexdigest()


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    return tmp_path / "run" / "audit.jsonl"


@pytest.fixture
def three_events(log_path: Path) -> Path:
    with run_context(mode="backtest", as_of=date(2024, 1, 2)):
        audit = AuditLog(log_path)
        audit.record(EventType.RUN_STARTED, "fund", {"note": "start"})
        audit.record(EventType.ORDER, "oms", {"symbol": "AAPL", "qty": 100})
        audit.record(EventType.RUN_FINISHED, "fund", {"note": "end"})
    assert AuditLog.verify_chain(log_path) is True
    return log_path


def test_seeded_event_types() -> None:
    assert {e.value for e in EventType} == {
        "RUN_STARTED", "RUN_FINISHED", "DATA_SYNC", "ANALYST_REPORT", "THESIS", "PROPOSAL",
        "ALLOCATION", "RISK_DECISION", "COMPLIANCE", "ORDER", "FILL", "LIMIT_BREACH",
        "CIRCUIT_BREAKER", "OVERRIDE", "LESSON",
    }  # fmt: skip
    assert all(e.value == e.name for e in EventType)


def test_record_chains_events(log_path: Path) -> None:
    with run_context(mode="backtest", as_of=date(2024, 1, 2)) as ctx:
        audit = AuditLog(log_path)
        first = audit.record(EventType.RUN_STARTED, "fund", {"universe": "sp500"})
        second = audit.record(EventType.ORDER, "oms", {"symbol": "AAPL", "qty": 100})
    assert audit.path == log_path
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    for event in (first, second):
        dumped = event.model_dump(mode="json")
        assert event.hash == _expected_hash(dumped)
        assert event.run_id == ctx.run_id
        assert event.as_of == date(2024, 1, 2)
        assert event.ts.utcoffset() is not None and event.ts.utcoffset().total_seconds() == 0
        assert len(event.event_id) == 26
    assert first.event_type is EventType.RUN_STARTED
    assert first.actor == "fund"
    assert _lines(log_path) == [first.model_dump(mode="json"), second.model_dump(mode="json")]
    assert AuditLog.verify_chain(log_path) is True


def test_record_outside_run_raises(log_path: Path) -> None:
    audit = AuditLog(log_path)
    with pytest.raises(AtlasError, match="run context"):
        audit.record(EventType.ORDER, "oms", {})
    assert not log_path.exists() or log_path.read_text() == ""


def test_empty_actor_rejected(log_path: Path) -> None:
    with run_context():
        audit = AuditLog(log_path)
        audit.record(EventType.ORDER, "oms", {})
        before = log_path.read_bytes()
        with pytest.raises(ValidationError):
            audit.record(EventType.ORDER, "", {})
    assert log_path.read_bytes() == before


def test_rich_payload_round_trips_and_verifies(log_path: Path) -> None:
    payload = {
        "cash": Decimal("1000000.10"),
        "day": date(2024, 1, 2),
        "at": datetime(2024, 1, 2, 9, 30, tzinfo=ZoneInfo("America/New_York")),
        "kind": EventType.FILL,
        "leg": _Leg(symbol="BRK.B", weight=-0.025),
        "note": "€ 公司",
        "nested": {"b": [1, 2.5, None, True], "a": "x"},
    }
    with run_context():
        event = AuditLog(log_path).record(EventType.FILL, "execution", payload)
    assert event.payload == {
        "cash": "1000000.10",
        "day": "2024-01-02",
        "at": "2024-01-02T14:30:00+00:00",
        "kind": "FILL",
        "leg": {"symbol": "BRK.B", "weight": -0.025},
        "note": "€ 公司",
        "nested": {"a": "x", "b": [1, 2.5, None, True]},
    }
    (line,) = _lines(log_path)
    assert line["payload"] == event.payload
    assert AuditLog.verify_chain(log_path) is True


@pytest.mark.parametrize(
    ("bad", "error"),
    [
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (datetime(2024, 1, 2, 9, 30), ValueError),
        ({1, 2}, TypeError),
    ],
    ids=["nan", "inf", "naive-datetime", "set"],
)
def test_bad_payload_writes_nothing(log_path: Path, bad: object, error: type[Exception]) -> None:
    with run_context():
        audit = AuditLog(log_path)
        first = audit.record(EventType.ORDER, "oms", {"ok": 1})
        before = log_path.read_bytes()
        with pytest.raises(error):
            audit.record(EventType.ORDER, "oms", {"bad": bad})
        assert log_path.read_bytes() == before
        second = audit.record(EventType.ORDER, "oms", {"ok": 2})
    assert second.prev_hash == first.hash
    assert AuditLog.verify_chain(log_path) is True


def test_tamper_edited_payload(three_events: Path) -> None:
    lines = _lines(three_events)
    lines[1]["payload"]["qty"] = 1000
    _write_lines(three_events, lines)
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_edited_line_text(three_events: Path) -> None:
    text = three_events.read_text(encoding="utf-8")
    three_events.write_text(text.replace('"qty":100', '"qty":101'), encoding="utf-8")
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_deleted_line(three_events: Path) -> None:
    lines = _lines(three_events)
    _write_lines(three_events, [lines[0], lines[2]])
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_deleted_first_line(three_events: Path) -> None:
    _write_lines(three_events, _lines(three_events)[1:])
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_swapped_lines(three_events: Path) -> None:
    lines = _lines(three_events)
    _write_lines(three_events, [lines[0], lines[2], lines[1]])
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_rehashed_line_breaks_next_link(three_events: Path) -> None:
    lines = _lines(three_events)
    lines[1]["payload"]["qty"] = 1000
    lines[1]["hash"] = _expected_hash(lines[1])
    _write_lines(three_events, lines)
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_invalid_json_line(three_events: Path) -> None:
    with three_events.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
    assert AuditLog.verify_chain(three_events) is False


def test_tamper_invalid_event_field(three_events: Path) -> None:
    lines = _lines(three_events)
    lines[2]["event_type"] = "NOT_A_KIND"
    lines[2]["hash"] = _expected_hash(lines[2])
    _write_lines(three_events, lines)
    assert AuditLog.verify_chain(three_events) is False


def test_truncated_last_line_fails_and_reopen_refuses(three_events: Path) -> None:
    data = three_events.read_bytes()
    last_start = data.rstrip(b"\n").rfind(b"\n") + 1
    three_events.write_bytes(data[: last_start + (len(data) - last_start) // 2])
    assert AuditLog.verify_chain(three_events) is False
    with pytest.raises(AtlasError, match="verification"):
        AuditLog(three_events)


def test_missing_final_newline_fails(three_events: Path) -> None:
    # A complete last line without its newline would glue the next append onto it.
    three_events.write_bytes(three_events.read_bytes().rstrip(b"\n"))
    assert AuditLog.verify_chain(three_events) is False


def test_verify_missing_and_empty(tmp_path: Path) -> None:
    assert AuditLog.verify_chain(tmp_path / "missing.jsonl") is False
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    assert AuditLog.verify_chain(empty) is True


def test_reopen_continues_chain(log_path: Path) -> None:
    with run_context():
        audit = AuditLog(log_path)
        audit.record(EventType.ORDER, "oms", {"n": 1})
        second = audit.record(EventType.ORDER, "oms", {"n": 2})
        third = AuditLog(log_path).record(EventType.ORDER, "oms", {"n": 3})
    assert third.prev_hash == second.hash
    assert AuditLog.verify_chain(log_path) is True


def test_two_instances_same_file_share_chain(log_path: Path) -> None:
    with run_context():
        a, b = AuditLog(log_path), AuditLog(log_path)
        for n in range(10):
            (a if n % 2 == 0 else b).record(EventType.ORDER, "oms", {"n": n})
    assert len(_lines(log_path)) == 10
    assert AuditLog.verify_chain(log_path) is True


def test_concurrent_threads_keep_chain_valid(log_path: Path) -> None:
    errors: list[BaseException] = []

    def worker(audit: AuditLog, thread_no: int) -> None:
        try:
            for n in range(50):
                audit.record(EventType.FILL, f"thread-{thread_no}", {"n": n})
        except BaseException as exc:  # surfaced by the assert below
            errors.append(exc)

    with run_context():
        audit = AuditLog(log_path)
        threads = [
            threading.Thread(target=contextvars.copy_context().run, args=(worker, audit, i))
            for i in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert errors == []
    lines = _lines(log_path)
    assert len(lines) == 400
    assert len({line["event_id"] for line in lines}) == 400
    assert AuditLog.verify_chain(log_path) is True


def test_audit_path(tmp_path: Path) -> None:
    assert audit_path(Path("data"), "01ABC") == Path("data/runs/01ABC/audit.jsonl")
    with run_context() as ctx:
        path = audit_path(tmp_path / "lake", ctx.run_id)
        AuditLog(path).record(EventType.RUN_STARTED, "fund", {})
    assert path.is_file()
    assert AuditLog.verify_chain(path) is True


def test_audit_event_is_frozen(log_path: Path) -> None:
    with run_context():
        event = AuditLog(log_path).record(EventType.ORDER, "oms", {})
    assert isinstance(event, AuditEvent)
    with pytest.raises(ValidationError):
        event.actor = "someone else"  # type: ignore[misc]


# --- Phase 0 checkpoint, Tier 3: reproducible event ids --------------------------------------


def _seeded_events(path: Path) -> tuple[list[AuditEvent], datetime]:
    clock = SimClock(start=date(2024, 3, 1))
    log = AuditLog(path)
    with use_ids(SeededIds(3, clock)), run_context(mode="backtest", as_of=clock.today()):
        events = [log.record(EventType.ORDER, "oms", {"n": n}) for n in range(3)]
    return events, clock.now()


def test_event_ids_and_times_follow_the_installed_id_source(tmp_path: Path) -> None:
    first, now = _seeded_events(tmp_path / "a" / "audit.jsonl")
    second, _ = _seeded_events(tmp_path / "b" / "audit.jsonl")
    assert [e.event_id for e in first] == [e.event_id for e in second]
    assert [e.ts for e in first] == [now] * 3  # simulated time, not the wall clock
    assert first[0].run_id != second[0].run_id  # each run still gets its own id
    assert AuditLog.verify_chain(tmp_path / "a" / "audit.jsonl")


# --- Phase 0 checkpoint, Tier 3: several processes on one file -------------------------------

# Opens the log, signals `ready`, waits for `go` (a barrier set by the test), then appends.
_WRITER = """
import sys, time
from pathlib import Path
from atlas.core.audit import AuditLog, EventType
from atlas.core.run_context import run_context
log_path, ready, go = (Path(arg) for arg in sys.argv[1:4])
count = int(sys.argv[4])
log = AuditLog(log_path)
ready.touch()
for _ in range(3000):
    if go.exists():
        break
    time.sleep(0.01)
with run_context():
    for n in range(count):
        log.record(EventType.FILL, f"proc-{ready.name}", {"n": n})
"""


def _writer(log_path: Path, tmp_path: Path, name: str, count: int) -> subprocess.Popen[bytes]:
    args = [str(log_path), str(tmp_path / name), str(tmp_path / "go"), str(count)]
    return subprocess.Popen([sys.executable, "-c", _WRITER, *args])


def _wait_ready(tmp_path: Path, names: list[str]) -> None:
    for _ in range(3000):
        if all((tmp_path / name).exists() for name in names):
            return
        time.sleep(0.01)
    raise AssertionError(f"writers never became ready: {names}")


def test_appends_from_another_process_are_picked_up(log_path: Path, tmp_path: Path) -> None:
    (tmp_path / "go").touch()
    with run_context():
        audit = AuditLog(log_path)
        audit.record(EventType.ORDER, "here", {"n": 1})
        assert _writer(log_path, tmp_path, "other", 2).wait(timeout=60) == 0
        last = audit.record(EventType.ORDER, "here", {"n": 2})
    lines = _lines(log_path)
    assert len(lines) == 4
    assert last.prev_hash == lines[2]["hash"]
    assert AuditLog.verify_chain(log_path) is True


def test_concurrent_processes_keep_chain_valid(log_path: Path, tmp_path: Path) -> None:
    names = [f"w{i}" for i in range(4)]
    procs = [_writer(log_path, tmp_path, name, 25) for name in names]
    try:
        _wait_ready(tmp_path, names)  # every writer has opened the log before anyone writes
        (tmp_path / "go").touch()
        assert [proc.wait(timeout=120) for proc in procs] == [0, 0, 0, 0]
    finally:
        for proc in procs:
            proc.kill()
    lines = _lines(log_path)
    assert len(lines) == 100
    assert len({line["event_id"] for line in lines}) == 100
    assert AuditLog.verify_chain(log_path) is True
