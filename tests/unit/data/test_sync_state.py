"""Stage 14 Task 3: the sync checkpoint (`SyncState`) and window planning."""

import json
import threading
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from atlas.core.errors import DataError
from atlas.data.sync import (
    MAX_RUNS,
    Chunk,
    Coverage,
    SyncState,
    group_chunks,
    plan_snapshot,
    plan_window,
)

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
D = date


def cov(start: date, end: date, status: str = "ok") -> Coverage:
    return Coverage(start=start, end=end, synced_at=T0, status=status)  # type: ignore[arg-type]


def test_state_missing_file_is_empty(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "cache" / "sync_state.json")
    assert state.keys("prices") == {}
    assert state.coverage("prices", "AAPL") is None
    assert state.runs == ()
    assert state.min_start() is None
    assert state.last_synced(["prices"]) is None


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "sync_state.json"
    state = SyncState.load(path)
    state.record("prices", ["AAPL", "MSFT"], D(2015, 1, 1), D(2015, 12, 31), synced_at=T0)
    state.record("fundamentals", ["XOM"], D(2015, 1, 1), D(2026, 11, 1), synced_at=T0, empty=True)
    state.add_run({"command": "sync", "run_id": "r1", "ok": True})
    state.save()
    again = SyncState.load(path)
    assert again.coverage("prices", "AAPL") == cov(D(2015, 1, 1), D(2015, 12, 31))
    assert again.coverage("fundamentals", "XOM") == cov(D(2015, 1, 1), D(2026, 11, 1), "empty")
    assert again.runs == ({"command": "sync", "run_id": "r1", "ok": True},)
    raw = json.loads(path.read_text())
    assert raw["format"] == 1
    assert raw["steps"]["prices"]["AAPL"] == {
        "from": "2015-01-01",
        "to": "2015-12-31",
        "synced_at": "2026-11-01T12:00:00+00:00",
        "status": "ok",
    }


def test_state_corrupt_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "sync_state.json"
    path.write_text("{not json")
    with pytest.raises(DataError, match="sync state"):
        SyncState.load(path)


@pytest.mark.parametrize(
    "content",
    [
        {"format": 2, "steps": {}, "runs": []},
        {"steps": {}, "runs": []},
        {"format": 1, "steps": {"prices": {"AAPL": {"from": "2015-01-01"}}}, "runs": []},
        {"format": 1, "steps": {"prices": {"AAPL": {"from": "x", "to": "y", "synced_at": "z",
                                                     "status": "ok"}}}, "runs": []},
        {"format": 1, "steps": [], "runs": []},
    ],
)  # fmt: skip
def test_state_wrong_format_raises(tmp_path: Path, content: object) -> None:
    path = tmp_path / "sync_state.json"
    path.write_text(json.dumps(content))
    with pytest.raises(DataError, match="sync state"):
        SyncState.load(path)


def test_state_save_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "sync_state.json"
    state = SyncState.load(path)
    state.record("prices", ["AAPL"], D(2015, 1, 1), D(2015, 1, 31), synced_at=T0)
    state.save()
    before = path.read_bytes()
    state.record("prices", ["MSFT"], D(2015, 1, 1), D(2015, 1, 31), synced_at=T0)

    def boom(*args: object, **kwargs: object) -> str:
        raise OSError("disk full")

    monkeypatch.setattr("atlas.data.sync.canonical_state_json", boom)
    with pytest.raises(OSError, match="disk full"):
        state.save()
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["sync_state.json"]


def test_record_chunk_merges_contiguous_windows(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "s.json")
    state.record("news", ["AAPL"], D(2015, 1, 1), D(2015, 3, 31), synced_at=T0)
    state.record("news", ["AAPL"], D(2015, 4, 1), D(2015, 6, 29), synced_at=T0)
    assert state.coverage("news", "AAPL") == cov(D(2015, 1, 1), D(2015, 6, 29))
    # overlapping (update with overlap days)
    state.record("news", ["AAPL"], D(2015, 6, 25), D(2015, 7, 10), synced_at=T0)
    assert state.coverage("news", "AAPL") == cov(D(2015, 1, 1), D(2015, 7, 10))
    # a gap replaces the coverage with the new window
    state.record("news", ["AAPL"], D(2016, 1, 1), D(2016, 2, 1), synced_at=T0)
    assert state.coverage("news", "AAPL") == cov(D(2016, 1, 1), D(2016, 2, 1))
    # an empty key that later gets data becomes ok
    state.record("prices", ["X"], D(2015, 1, 1), D(2015, 1, 31), synced_at=T0, empty=True)
    state.record("prices", ["X"], D(2015, 2, 1), D(2015, 2, 28), synced_at=T0)
    assert state.coverage("prices", "X") == cov(D(2015, 1, 1), D(2015, 2, 28), "ok")


def test_record_keeps_latest_synced_at(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "s.json")
    later = datetime(2026, 11, 2, tzinfo=UTC)
    state.record("macro", ["CPI"], D(2015, 1, 1), D(2026, 11, 1), synced_at=T0)
    state.record("macro", ["CPI"], D(2015, 1, 1), D(2026, 11, 2), synced_at=later)
    assert state.last_synced(["macro", "news"]) == later
    with pytest.raises(ValueError, match="naive"):
        state.record(
            "macro", ["CPI"], D(2015, 1, 1), D(2026, 11, 2), synced_at=datetime(2026, 1, 1)
        )
    with pytest.raises(ValueError, match="after"):
        state.record("macro", ["CPI"], D(2016, 1, 1), D(2015, 1, 1), synced_at=T0)


def test_record_chunk_thread_safe(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "s.json")

    def work(i: int) -> None:
        for j in range(50):
            state.record("prices", [f"S{i}_{j}"], D(2015, 1, 1), D(2015, 1, 2), synced_at=T0)
            state.save()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(SyncState.load(tmp_path / "s.json").keys("prices")) == 200


def test_runs_are_capped(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "s.json")
    for i in range(MAX_RUNS + 7):
        state.add_run({"run_id": str(i)})
    assert len(state.runs) == MAX_RUNS
    assert state.runs[0] == {"run_id": "7"}


def test_min_start(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "s.json")
    state.record("prices", ["A"], D(2016, 1, 1), D(2016, 2, 1), synced_at=T0)
    state.record("news", ["A"], D(2015, 6, 1), D(2016, 2, 1), synced_at=T0)
    assert state.min_start() == D(2015, 6, 1)
    assert state.min_start(["prices"]) == D(2016, 1, 1)


@pytest.mark.parametrize(
    ("coverage", "start", "end", "overlap", "expected"),
    [
        (None, D(2015, 1, 1), D(2026, 10, 30), 5, (D(2015, 1, 1), D(2026, 10, 30))),
        (cov(D(2015, 1, 1), D(2026, 10, 30)), D(2015, 1, 1), D(2026, 10, 30), 5, None),
        (cov(D(2015, 1, 1), D(2026, 10, 30)), D(2016, 1, 1), D(2026, 10, 1), 5, None),
        (cov(D(2015, 1, 1), D(2026, 10, 20)), D(2015, 1, 1), D(2026, 10, 30), 5,
         (D(2026, 10, 16), D(2026, 10, 30))),
        (cov(D(2015, 1, 1), D(2026, 10, 20)), D(2015, 1, 1), D(2026, 10, 30), 0,
         (D(2026, 10, 21), D(2026, 10, 30))),
        (cov(D(2016, 1, 1), D(2026, 10, 20)), D(2015, 1, 1), D(2026, 10, 30), 5,
         (D(2015, 1, 1), D(2026, 10, 30))),
        (cov(D(2015, 1, 1), D(2015, 1, 2)), D(2015, 1, 1), D(2015, 1, 9), 30,
         (D(2015, 1, 1), D(2015, 1, 9))),
        (cov(D(2015, 1, 1), D(2015, 1, 2), "empty"), D(2015, 1, 1), D(2015, 1, 9), 0,
         (D(2015, 1, 3), D(2015, 1, 9))),
        (None, D(2015, 1, 2), D(2015, 1, 1), 0, None),
    ],
)  # fmt: skip
def test_plan_window_cases(
    coverage: Coverage | None,
    start: date,
    end: date,
    overlap: int,
    expected: tuple[date, date] | None,
) -> None:
    assert plan_window(coverage, start, end, overlap) == expected


def test_plan_snapshot_cases() -> None:
    assert plan_snapshot(None, D(2026, 11, 1)) is True
    assert plan_snapshot(cov(D(2015, 1, 1), D(2026, 10, 31)), D(2026, 11, 1)) is True
    assert plan_snapshot(cov(D(2015, 1, 1), D(2026, 11, 1)), D(2026, 11, 1)) is False
    assert plan_snapshot(cov(D(2015, 1, 1), D(2026, 11, 1), "empty"), D(2026, 11, 1)) is False


def test_group_chunks_by_window_and_size() -> None:
    a = (D(2015, 1, 1), D(2026, 1, 1))
    b = (D(2025, 12, 1), D(2026, 1, 1))
    windows = {"A": a, "B": a, "C": b, "D": a, "E": a, "F": b}
    chunks = group_chunks(windows, 3)
    assert chunks == [
        Chunk(keys=("A", "B", "D"), start=a[0], end=a[1]),
        Chunk(keys=("E",), start=a[0], end=a[1]),
        Chunk(keys=("C", "F"), start=b[0], end=b[1]),
    ]
    assert group_chunks({}, 3) == []
    with pytest.raises(ValueError, match="size"):
        group_chunks(windows, 0)


def test_plan_window_empty_key_rechecked_only_after_retry_days() -> None:
    from datetime import timedelta

    empty = cov(D(2015, 1, 1), D(2026, 10, 30), "empty")  # synced at T0
    later = D(2026, 11, 20)
    assert (
        plan_window(
            empty, D(2015, 1, 1), later, 5, now=T0 + timedelta(days=29), empty_retry_days=30
        )
        is None
    )
    assert plan_window(
        empty, D(2015, 1, 1), later, 5, now=T0 + timedelta(days=30), empty_retry_days=30
    ) == (D(2026, 10, 26), later)
    # without the option, or when the request reaches before the empty window: fetch
    assert plan_window(empty, D(2015, 1, 1), later, 5) == (D(2026, 10, 26), later)
    assert plan_window(empty, D(2014, 1, 1), later, 5, now=T0, empty_retry_days=30) == (
        D(2014, 1, 1),
        later,
    )
    ok = cov(D(2015, 1, 1), D(2026, 10, 30))
    assert plan_window(ok, D(2015, 1, 1), later, 5, now=T0, empty_retry_days=30) == (
        D(2026, 10, 26),
        later,
    )
    with pytest.raises(ValueError, match="now"):
        plan_window(empty, D(2015, 1, 1), later, 5, empty_retry_days=30)
