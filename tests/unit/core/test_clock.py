"""Tests for `atlas.core.clock`: SimClock, LiveClock, the clock contextvar, the wall-clock ban."""

import ast
import contextvars
import subprocess
import sys
import threading
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from atlas.core.calendar import market_date
from atlas.core.clock import Clock, LiveClock, SimClock, get_clock, use_clock
from atlas.core.errors import AtlasError


def test_simclock_starts_at_decision_time() -> None:
    clock = SimClock(start=date(2020, 1, 2))
    assert clock.now() == datetime(2020, 1, 2, 13, 0, tzinfo=UTC)  # 08:00 EST
    assert clock.now().tzinfo == UTC
    assert clock.today() == date(2020, 1, 2)


def test_simclock_advance_skips_weekend_and_holiday() -> None:
    clock = SimClock(start=date(2024, 3, 28))
    assert clock.advance() == datetime(2024, 4, 1, 12, 0, tzinfo=UTC)  # Good Friday skipped; EDT
    assert clock.today() == date(2024, 4, 1)
    clock = SimClock(start=date(2024, 3, 1))
    assert clock.advance(2) == datetime(2024, 3, 5, 13, 0, tzinfo=UTC)


def test_simclock_advance_after_set_moves_to_next_session() -> None:
    clock = SimClock(start=date(2024, 3, 1))
    clock.set(datetime(2024, 3, 1, 21, 15, tzinfo=UTC))  # 16:15 ET, same session
    assert clock.advance() == datetime(2024, 3, 4, 13, 0, tzinfo=UTC)


def test_simclock_advance_before_decision_runs_todays_decision() -> None:  # review I3
    clock = SimClock(start=date(2024, 3, 1))
    clock.set(datetime(2024, 3, 4, 11, 30, tzinfo=UTC))  # 06:30 ET Monday, before 08:00
    assert clock.advance() == datetime(2024, 3, 4, 13, 0, tzinfo=UTC)  # Monday is not skipped
    clock.set(datetime(2024, 3, 5, 11, 30, tzinfo=UTC))
    assert clock.advance(2) == datetime(2024, 3, 6, 13, 0, tzinfo=UTC)
    clock = SimClock(start=datetime(2024, 3, 2, 11, 30, tzinfo=UTC))  # Saturday morning
    assert clock.advance() == datetime(2024, 3, 4, 13, 0, tzinfo=UTC)


def test_simclock_custom_decision_time() -> None:
    clock = SimClock(start=date(2024, 3, 1), decision_time=time(17, 30))
    assert clock.now() == datetime(2024, 3, 1, 22, 30, tzinfo=UTC)


def test_simclock_accepts_aware_datetime_start() -> None:
    start = datetime(2024, 3, 2, 15, 0, tzinfo=UTC)  # a Saturday instant is allowed
    clock = SimClock(start=start)
    assert clock.now() == start
    assert clock.advance() == datetime(2024, 3, 4, 13, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "start",
    [date(2024, 3, 30), datetime(2024, 3, 1, 13, 0)],
    ids=["non-session-date", "naive-datetime"],
)
def test_simclock_rejects_bad_start(start: date) -> None:
    with pytest.raises(ValueError):
        SimClock(start=start)


def test_simclock_rejects_bad_moves() -> None:
    clock = SimClock(start=date(2024, 3, 1))
    with pytest.raises(ValueError, match="n must be >= 1"):
        clock.advance(0)
    with pytest.raises(ValueError, match="backwards"):
        clock.set(clock.now() - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="naive"):
        clock.set(datetime(2024, 3, 2, 13, 0))
    assert clock.now() == datetime(2024, 3, 1, 13, 0, tzinfo=UTC)  # unchanged after errors


def test_simclock_set_moves_forward() -> None:
    clock = SimClock(start=date(2024, 3, 1))
    clock.set(datetime(2024, 3, 1, 21, 15, tzinfo=UTC))
    assert clock.now() == datetime(2024, 3, 1, 21, 15, tzinfo=UTC)
    clock.set(datetime(2024, 3, 1, 21, 15, tzinfo=UTC))  # equal is not backwards
    assert clock.now() == datetime(2024, 3, 1, 21, 15, tzinfo=UTC)


def test_liveclock_is_utc_and_monotone() -> None:
    clock = LiveClock()
    first, second = clock.now(), clock.now()
    assert first.tzinfo == UTC
    assert first <= second
    assert clock.today() in {market_date(first), market_date(clock.now())}


def test_clocks_satisfy_protocol() -> None:
    clocks: list[Clock] = [SimClock(start=date(2024, 3, 1)), LiveClock()]
    assert all(isinstance(c.now(), datetime) for c in clocks)


def test_get_clock_without_clock_raises() -> None:
    with pytest.raises(AtlasError, match="no clock"):
        get_clock()


def test_use_clock_installs_and_nests() -> None:
    outer, inner = SimClock(start=date(2024, 3, 1)), SimClock(start=date(2024, 3, 4))
    with use_clock(outer) as installed:
        assert installed is outer
        assert get_clock() is outer
        with use_clock(inner):
            assert get_clock() is inner
        assert get_clock() is outer
    with pytest.raises(AtlasError):
        get_clock()


def test_use_clock_restores_after_exception() -> None:  # Review focus 5
    outer, inner = SimClock(start=date(2024, 3, 1)), SimClock(start=date(2024, 3, 4))
    with use_clock(outer):
        with pytest.raises(RuntimeError), use_clock(inner):
            raise RuntimeError("boom")
        assert get_clock() is outer


def test_new_thread_has_no_clock() -> None:  # Review focus 5
    errors: list[BaseException] = []

    def target() -> None:
        try:
            get_clock()
        except AtlasError as exc:
            errors.append(exc)

    with use_clock(SimClock(start=date(2024, 3, 1))):
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
    assert len(errors) == 1


def test_copy_context_propagates_clock() -> None:  # Review focus 5
    clock = SimClock(start=date(2024, 3, 1))
    seen: list[Clock] = []
    with use_clock(clock):
        ctx = contextvars.copy_context()
        thread = threading.Thread(target=lambda: seen.append(ctx.run(get_clock)))
        thread.start()
        thread.join()
    assert seen == [clock]


def test_sim_clock_fixture(sim_clock: SimClock) -> None:
    assert get_clock() is sim_clock
    assert sim_clock.today() == date(2024, 3, 1)


# --- the wall-clock ban (ruff TID251) ----------------------------------------------------

WALL_CLOCK_SNIPPETS = [
    "from datetime import datetime\n\ndatetime.now()\n",
    "from datetime import date\n\ndate.today()\n",
    "import time\n\ntime.time()\n",
    "import datetime as dt\n\ndt.datetime.utcnow()\n",
    "import pandas as pd\n\npd.Timestamp.now()\n",
    "import time\n\ntime.localtime()\n",
    "import time\n\ntime.gmtime()\n",
    "import time\n\ntime.ctime()\n",
    "import time\n\ntime.asctime()\n",
    'import time\n\ntime.strftime("%Y")\n',
    "import time\n\ntime.clock_gettime(time.CLOCK_REALTIME)\n",
    "import time\n\ntime.clock_gettime_ns(time.CLOCK_REALTIME)\n",
]


def _ruff_on(snippet: str, filename: str, repo_root: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "ruff", "check", "--no-cache", "--select", "TID251"]
    return subprocess.run(
        [*cmd, "--stdin-filename", filename, "-"],
        input=snippet,
        capture_output=True,
        text=True,
        cwd=repo_root,
        check=False,
    )


@pytest.mark.parametrize("snippet", WALL_CLOCK_SNIPPETS)
def test_ruff_bans_wall_clock_outside_clock_module(snippet: str, repo_root: Path) -> None:
    for filename in ("src/atlas/data/example.py", "tests/unit/core/test_example.py"):
        result = _ruff_on(snippet, filename, repo_root)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "TID251" in result.stdout


def test_ruff_allows_wall_clock_in_clock_module(repo_root: Path) -> None:
    result = _ruff_on(WALL_CLOCK_SNIPPETS[0], "src/atlas/core/clock.py", repo_root)
    assert result.returncode == 0, result.stdout + result.stderr


# String forms read the wall clock too ("now"/"today" parsed by pandas/numpy); ruff cannot see
# them, so an AST scan guards src/ and tests/ (review I2).
_STRING_CLOCK_CALLS = {"Timestamp", "to_datetime", "datetime64", "date_range", "bdate_range"}


def _string_clock_reads(path: Path) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", None))
        values = [*node.args, *(kw.value for kw in node.keywords)]
        if name in _STRING_CLOCK_CALLS and any(
            isinstance(v, ast.Constant) and str(v.value).strip().lower() in {"now", "today"}
            for v in values
        ):
            hits.append(f"{path}:{node.lineno}")
    return hits


def test_string_clock_detector_flags_samples(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import numpy as np\nimport pandas as pd\n"
        'pd.Timestamp("now")\npd.to_datetime("today")\nnp.datetime64("now")\n'
        'pd.date_range(end="today", periods=3)\npd.Timestamp("2024-03-01")\n'
    )
    assert len(_string_clock_reads(sample)) == 4


def test_no_string_clock_reads_outside_clock_module(repo_root: Path) -> None:
    files = [*(repo_root / "src").rglob("*.py"), *(repo_root / "tests").rglob("*.py")]
    clock_module = repo_root / "src" / "atlas" / "core" / "clock.py"
    hits = [hit for f in files if f != clock_module for hit in _string_clock_reads(f)]
    assert hits == []
