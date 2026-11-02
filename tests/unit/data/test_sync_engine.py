"""Stage 14 Task 4: the generic sync engine (`run_step`) with pure fake steps."""

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from atlas.core.audit import AuditLog, EventType
from atlas.core.clock import SimClock, get_clock, use_clock
from atlas.core.errors import ConfigError, DataError
from atlas.core.run_context import current_run_context, run_context
from atlas.core.storage import (
    DEFAULT_REGISTRY,
    YEAR,
    Column,
    ColumnType,
    Lake,
    SchemaRegistry,
    TableSchema,
)
from atlas.data.sync import (
    ChunkOutcome,
    StepReport,
    StepSpec,
    SyncState,
    run_step,
    serial_lake,
)

D = date
KEYS = ("A", "B", "C", "D", "E", "F")
Y2015 = (date(2015, 1, 1), date(2015, 12, 31))
TABLE = TableSchema(
    name="sync_test",
    columns=(
        Column(name="symbol", dtype=ColumnType.STRING),
        Column(name="day", dtype=ColumnType.DATE),
        Column(name="value", dtype=ColumnType.FLOAT64),
    ),
    primary_key=("symbol", "day"),
    partition_by=(YEAR,),
    year_column="day",
    static=True,
    description="Stage 14 engine tests",
)


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    registry: SchemaRegistry = DEFAULT_REGISTRY.copy()
    registry.register(TABLE)
    return Lake(tmp_path / "lake", registry=registry)


@pytest.fixture(autouse=True)
def clock() -> Iterator[SimClock]:
    with use_clock(SimClock(start=D(2026, 10, 30))) as installed:
        assert isinstance(installed, SimClock)
        yield installed


class Recorder:
    """A fake fetch: writes one row per key per day boundary into `sync_test`, records calls.
    `crash_on` (1-based call number) raises `RuntimeError`; `fail` keys come back failed;
    `empty` keys come back empty; `delay` seconds per call."""

    def __init__(
        self,
        lake: Lake,
        *,
        crash_on: int | None = None,
        fail: frozenset[str] = frozenset(),
        empty: frozenset[str] = frozenset(),
        delay: float = 0.0,
    ) -> None:
        self.lake = lake
        self.crash_on = crash_on
        self.fail = fail
        self.empty = empty
        self.delay = delay
        self.calls: list[tuple[tuple[str, ...], date, date]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        with self._lock:
            self.calls.append((keys, start, end))
            number = len(self.calls)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if number == self.crash_on:
                raise RuntimeError("simulated crash")
            good = [k for k in keys if k not in self.fail and k not in self.empty]
            if good:
                rows = pd.DataFrame(
                    {
                        "symbol": [k for k in good for _ in (start, end)],
                        "day": [d for _ in good for d in (start, end)],
                        "value": [1.0 for _ in good for _ in (start, end)],
                    }
                ).drop_duplicates(["symbol", "day"])
                self.lake.write_table("sync_test", rows, mode="upsert")
            return ChunkOutcome(
                rows=2 * len(good),
                ok=tuple(good),
                empty=tuple(k for k in keys if k in self.empty),
                failed={k: "vendor error" for k in keys if k in self.fail},
            )
        finally:
            with self._lock:
                self.in_flight -= 1


def spec(
    fetch: Recorder,
    keys: tuple[str, ...],
    *,
    mode: str = "window",
    start: date = Y2015[0],
    end: date = Y2015[1],
    chunk_size: int = 2,
    window_days: int | None = None,
    overlap_days: int = 0,
    provider: str = "yahoo",
) -> StepSpec:
    return StepSpec(
        name="prices",
        dataset="prices",
        provider=provider,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        keys=keys,
        start=start,
        end=end,
        chunk_size=chunk_size,
        fetch=fetch,
        window_days=window_days,
        overlap_days=overlap_days,
    )


def stored(lake: Lake) -> pd.DataFrame:
    return lake.read_table("sync_test")


def test_runs_all_chunks_and_checkpoints(lake: Lake, tmp_path: Path) -> None:
    state = SyncState(tmp_path / "state.json")
    fetch = Recorder(lake)
    report = run_step(lake, state, spec(fetch, KEYS))
    assert [c[0] for c in fetch.calls] == [("A", "B"), ("C", "D"), ("E", "F")]
    assert (report.keys, report.skipped, report.ok, report.empty, report.chunks) == (6, 0, 6, 0, 3)
    assert report.rows == 12 and report.error is None and dict(report.failed) == {}
    on_disk = SyncState.load(tmp_path / "state.json")
    assert set(on_disk.keys("prices")) == set(KEYS)
    assert on_disk.coverage("prices", "A").end == D(2015, 12, 31)  # type: ignore[union-attr]


def test_resume_after_crash_fetches_only_the_rest(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    crashing = Recorder(lake, crash_on=3)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run_step(lake, SyncState.load(path), spec(crashing, KEYS))
    assert set(SyncState.load(path).keys("prices")) == {"A", "B", "C", "D"}

    healthy = Recorder(lake)
    report = run_step(lake, SyncState.load(path), spec(healthy, KEYS))
    assert [c[0] for c in healthy.calls] == [("E", "F")]
    assert (report.skipped, report.ok) == (4, 2)

    clean_lake = Lake(tmp_path / "clean", registry=lake.registry)
    run_step(clean_lake, SyncState(tmp_path / "clean.json"), spec(Recorder(clean_lake), KEYS))
    pd.testing.assert_frame_equal(stored(lake), stored(clean_lake))


def test_failed_keys_are_retried_next_run(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = run_step(lake, SyncState.load(path), spec(Recorder(lake, fail=frozenset({"C"})), KEYS))
    assert dict(first.failed) == {"C": "vendor error"}
    assert first.ok == 5
    again = Recorder(lake)
    second = run_step(lake, SyncState.load(path), spec(again, KEYS))
    assert again.calls == [(("C",), D(2015, 1, 1), D(2015, 12, 31))]
    assert (second.skipped, second.ok) == (5, 1)


def test_empty_keys_are_not_retried(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = run_step(lake, SyncState.load(path), spec(Recorder(lake, empty=frozenset({"F"})), KEYS))
    assert (first.ok, first.empty) == (5, 1)
    again = Recorder(lake)
    second = run_step(lake, SyncState.load(path), spec(again, KEYS))
    assert again.calls == []
    assert second.skipped == 6
    assert SyncState.load(path).coverage("prices", "F").status == "empty"  # type: ignore[union-attr]


def test_windowed_subwindows_checkpoint_in_order(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    fetch = Recorder(lake)
    run_step(
        lake,
        SyncState.load(path),
        spec(fetch, ("A",), start=D(2015, 1, 1), end=D(2015, 1, 25), window_days=10),
    )
    assert fetch.calls == [
        (("A",), D(2015, 1, 1), D(2015, 1, 10)),
        (("A",), D(2015, 1, 11), D(2015, 1, 20)),
        (("A",), D(2015, 1, 21), D(2015, 1, 25)),
    ]
    # crash in the second sub-window: the first one stays covered
    path2 = tmp_path / "state2.json"
    crashing = Recorder(lake, crash_on=2)
    with pytest.raises(RuntimeError):
        run_step(
            lake,
            SyncState.load(path2),
            spec(crashing, ("A",), start=D(2015, 1, 1), end=D(2015, 1, 25), window_days=10),
        )
    cov = SyncState.load(path2).coverage("prices", "A")
    assert cov is not None and (cov.start, cov.end) == (D(2015, 1, 1), D(2015, 1, 10))
    resumed = Recorder(lake)
    run_step(
        lake,
        SyncState.load(path2),
        spec(resumed, ("A",), start=D(2015, 1, 1), end=D(2015, 1, 25), window_days=10),
    )
    assert resumed.calls[0] == (("A",), D(2015, 1, 11), D(2015, 1, 20))


def test_failed_key_stops_its_later_subwindows(lake: Lake, tmp_path: Path) -> None:
    fetch = Recorder(lake, fail=frozenset({"B"}))
    report = run_step(
        lake,
        SyncState(tmp_path / "s.json"),
        spec(fetch, ("A", "B"), start=D(2015, 1, 1), end=D(2015, 1, 25), window_days=10),
    )
    assert [c[0] for c in fetch.calls] == [("A", "B"), ("A",), ("A",)]
    assert dict(report.failed) == {"B": "vendor error"}
    assert report.ok == 1


def test_overlap_refetches_recent_days(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    run_step(lake, SyncState.load(path), spec(Recorder(lake), ("A",), end=D(2015, 6, 30)))
    fetch = Recorder(lake)
    run_step(lake, SyncState.load(path), spec(fetch, ("A",), end=D(2015, 7, 10), overlap_days=5))
    assert fetch.calls == [(("A",), D(2015, 6, 26), D(2015, 7, 10))]


def test_snapshot_mode_refetches_when_older_than_end(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    first = Recorder(lake)
    run_step(lake, SyncState.load(path), spec(first, ("A", "B"), mode="snapshot"))
    assert first.calls == [(("A", "B"), D(2015, 1, 1), D(2015, 12, 31))]
    same_day = Recorder(lake)
    run_step(lake, SyncState.load(path), spec(same_day, ("A", "B"), mode="snapshot"))
    assert same_day.calls == []
    next_day = Recorder(lake)
    run_step(
        lake, SyncState.load(path), spec(next_day, ("A", "B"), mode="snapshot", end=D(2016, 1, 1))
    )
    assert next_day.calls == [(("A", "B"), D(2015, 1, 1), D(2016, 1, 1))]


def test_chunks_run_concurrently_on_provider_pool(lake: Lake, tmp_path: Path) -> None:
    locked = serial_lake(lake)  # run_sync always passes one; a plain Lake loses writes here
    fetch = Recorder(locked, delay=0.05)
    keys = tuple(f"K{i}" for i in range(12))
    run_step(locked, SyncState(tmp_path / "a.json"), spec(fetch, keys), workers=3)
    assert fetch.max_in_flight > 1
    serial_lake_ = Lake(tmp_path / "serial", registry=lake.registry)
    one = Recorder(serial_lake_)
    run_step(serial_lake_, SyncState(tmp_path / "b.json"), spec(one, keys), workers=1)
    assert one.max_in_flight == 1
    pd.testing.assert_frame_equal(stored(lake), stored(serial_lake_))


def test_lake_writes_are_serialised(lake: Lake, tmp_path: Path) -> None:
    locked = serial_lake(lake)
    assert serial_lake(locked) is locked
    keys = tuple(f"K{i:02d}" for i in range(40))
    fetch = Recorder(locked)
    run_step(locked, SyncState(tmp_path / "s.json"), spec(fetch, keys, chunk_size=1), workers=8)
    assert len(stored(lake)) == 80  # one upsert per key into the same 2015 partition: none lost


def test_workers_see_clock_and_run_context(lake: Lake, tmp_path: Path) -> None:
    seen: list[tuple[datetime, str | None]] = []
    lock = threading.Lock()

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        ctx = current_run_context()
        with lock:
            seen.append((get_clock().now(), ctx.run_id if ctx else None))
        return ChunkOutcome(ok=keys)

    with run_context(stage="sync") as ctx:
        run_step(lake, SyncState(tmp_path / "s.json"), spec(fetch, KEYS), workers=3)  # type: ignore[arg-type]
    assert len(seen) == 3
    assert {run_id for _, run_id in seen} == {ctx.run_id}


def test_unreported_key_counts_as_failed(lake: Lake, tmp_path: Path) -> None:
    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        return ChunkOutcome(ok=keys[:1])

    report = run_step(lake, SyncState(tmp_path / "s.json"), spec(fetch, ("A", "B")))  # type: ignore[arg-type]
    assert dict(report.failed) == {"B": "no outcome reported for this key"}


def test_data_error_fails_the_chunk_keys_only(lake: Lake, tmp_path: Path) -> None:
    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        if "C" in keys:
            raise DataError("vendor down")
        if "E" in keys:
            raise ConfigError("key missing")
        return ChunkOutcome(ok=keys)

    report = run_step(lake, SyncState(tmp_path / "s.json"), spec(fetch, KEYS))  # type: ignore[arg-type]
    assert dict(report.failed) == {
        "C": "vendor down",
        "D": "vendor down",
        "E": "key missing",
        "F": "key missing",
    }
    assert report.ok == 2


class Progress:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def start_step(self, step: str, total: int) -> None:
        self.events.append(("start", (step, total)))

    def advance(self, step: str, n: int) -> None:
        self.events.append(("advance", (step, n)))

    def end_step(self, report: StepReport) -> None:
        self.events.append(("end", report.step))


def test_progress_callbacks(lake: Lake, tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    run_step(lake, SyncState.load(path), spec(Recorder(lake), KEYS[:2]))
    progress = Progress()
    run_step(lake, SyncState.load(path), spec(Recorder(lake), KEYS), progress=progress)
    assert progress.events == [
        ("start", ("prices", 6)),
        ("advance", ("prices", 2)),  # already covered
        ("advance", ("prices", 2)),
        ("advance", ("prices", 2)),
        ("end", "prices"),
    ]


def test_audit_event_per_step(lake: Lake, tmp_path: Path) -> None:
    with run_context(stage="sync") as ctx:
        audit = AuditLog(tmp_path / "runs" / ctx.run_id / "audit.jsonl")
        report = run_step(
            lake,
            SyncState(tmp_path / "s.json"),
            spec(Recorder(lake, fail=frozenset({"A"})), KEYS),
            audit=audit,
        )
    lines = [json.loads(x) for x in audit.path.read_text().splitlines()]
    assert [x["event_type"] for x in lines] == [EventType.DATA_SYNC.value]
    assert lines[0]["actor"] == "data.sync"
    payload = lines[0]["payload"]
    assert payload == report.payload()
    assert payload["step"] == "prices" and payload["failed"] == 1
    assert payload["failure_reasons"] == {"A": "vendor error"}
    assert payload["start"] == "2015-01-01" and payload["end"] == "2015-12-31"
    assert AuditLog.verify_chain(audit.path)


def test_report_payload_caps_reasons(lake: Lake, tmp_path: Path) -> None:
    keys = tuple(f"K{i:03d}" for i in range(60))
    report = run_step(
        lake, SyncState(tmp_path / "s.json"), spec(Recorder(lake, fail=frozenset(keys)), keys)
    )
    payload = report.payload()
    assert payload["failed"] == 60
    assert len(payload["failure_reasons"]) == 20  # a sample; the full list is in the report


def test_synced_at_is_the_clock(lake: Lake, tmp_path: Path, clock: SimClock) -> None:
    state = SyncState(tmp_path / "s.json")
    run_step(lake, state, spec(Recorder(lake), ("A",)))
    cov = state.coverage("prices", "A")
    assert cov is not None and cov.synced_at == clock.now()
    assert cov.synced_at.tzinfo is not None and cov.synced_at.utcoffset() == UTC.utcoffset(None)


def test_empty_key_not_refetched_for_new_days_until_retry(
    lake: Lake, tmp_path: Path, clock: SimClock
) -> None:
    from dataclasses import replace

    path = tmp_path / "s.json"
    base = replace(spec(Recorder(lake), ("A",)), empty_retry_days=30)
    run_step(
        lake, SyncState.load(path), replace(base, fetch=Recorder(lake, empty=frozenset({"A"})))
    )
    soon = Recorder(lake)
    run_step(lake, SyncState.load(path), replace(base, fetch=soon, end=D(2016, 1, 10)))
    assert soon.calls == []  # empty for its whole history: not asked again within 30 days
    clock.advance(31)
    late = Recorder(lake)
    run_step(lake, SyncState.load(path), replace(base, fetch=late, end=D(2016, 1, 10)))
    assert late.calls == [(("A",), D(2016, 1, 1), D(2016, 1, 10))]


def test_empty_subwindow_does_not_stop_later_ones(lake: Lake, tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], date]] = []

    def fetch(keys: tuple[str, ...], start: date, end: date) -> ChunkOutcome:
        calls.append((keys, start))
        if start < D(2015, 1, 11):  # nothing listed yet in the first window
            return ChunkOutcome(empty=keys)
        return ChunkOutcome(rows=1, ok=keys)

    state = SyncState(tmp_path / "s.json")
    report = run_step(
        lake,
        state,
        spec(fetch, ("A",), start=D(2015, 1, 1), end=D(2015, 1, 25), window_days=10),  # type: ignore[arg-type]
    )
    assert [start for _, start in calls] == [D(2015, 1, 1), D(2015, 1, 11), D(2015, 1, 21)]
    assert (report.ok, report.empty) == (1, 0)
    cov = state.coverage("prices", "A")
    assert cov is not None and (cov.start, cov.end, cov.status) == (
        D(2015, 1, 1),
        D(2015, 1, 25),
        "ok",
    )
