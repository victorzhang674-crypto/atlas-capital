"""Tests for structured logging: JSON file lines, pretty console, run_id on every line."""

import io
import json
import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import structlog

from atlas.core.logging import configure_logging, get_logger, reset_logging
from atlas.core.run_context import run_context


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    yield
    reset_logging()


def _json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_every_file_line_is_json_with_run_id(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "atlas.log"
    configure_logging(log_file=log_file, console=io.StringIO())
    log = get_logger("atlas.test")
    with run_context(mode="backtest", as_of=date(2024, 1, 2)) as ctx:
        log.info("first", x=1)
        with run_context(stage="risk"):
            log.warning("second")
        logging.getLogger("thirdparty").warning("third %s", "x")

    lines = _json_lines(log_file)
    assert [line["event"] for line in lines] == ["first", "second", "third x"]
    for line in lines:
        assert line["run_id"] == ctx.run_id
        assert line["mode"] == "backtest"
        assert line["as_of"] == "2024-01-02"
        assert line["timestamp"].endswith("Z")
        assert {"level", "logger"} <= line.keys()
    assert lines[0]["x"] == 1
    assert lines[0]["logger"] == "atlas.test"
    assert lines[1]["stage"] == "risk"
    assert "stage" not in lines[0]
    assert lines[2]["logger"] == "thirdparty"


def test_run_keys_win_and_caller_values_are_kept(tmp_path: Path) -> None:
    # Phase 0 checkpoint (Stage 03 backlog): a caller's `mode=`/`run_id=`/`as_of=`/`stage=`
    # used to be overwritten silently (Stage 06 lost `lake.write`'s write mode that way).
    log_file = tmp_path / "atlas.log"
    configure_logging(log_file=log_file, console=io.StringIO())
    log = get_logger("atlas.test")
    with run_context(mode="backtest", as_of=date(2024, 1, 2), stage="risk") as ctx:
        log.info("clash", mode="upsert", run_id="other", as_of="2024-01-02", stage="risk")
    log.info("outside", mode="upsert")

    clash, outside = _json_lines(log_file)
    assert (clash["run_id"], clash["mode"], clash["as_of"], clash["stage"]) == (
        ctx.run_id,
        "backtest",
        "2024-01-02",
        "risk",
    )
    assert clash["shadowed"] == {"mode": "upsert", "run_id": "other"}  # equal values not kept
    assert outside["mode"] == "upsert"
    assert "shadowed" not in outside


def test_console_is_pretty_text() -> None:
    console = io.StringIO()
    configure_logging(console=console)
    with run_context():
        get_logger("atlas.test").info("first")
    text = console.getvalue()
    assert "first" in text
    assert "run_id=" in text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text.splitlines()[0])


def test_outside_run_has_no_run_id(tmp_path: Path) -> None:
    configure_logging(log_file=tmp_path / "a.log", console=io.StringIO())
    get_logger("atlas.test").info("loose")
    (line,) = _json_lines(tmp_path / "a.log")
    assert "run_id" not in line
    assert line["event"] == "loose"


def test_level_filters(tmp_path: Path) -> None:
    configure_logging(level="WARNING", log_file=tmp_path / "a.log", console=io.StringIO())
    log = get_logger("atlas.test")
    log.info("quiet")
    log.warning("loud")
    assert [line["event"] for line in _json_lines(tmp_path / "a.log")] == ["loud"]


def test_reconfigure_does_not_duplicate(tmp_path: Path) -> None:
    configure_logging(log_file=tmp_path / "a.log", console=io.StringIO())
    configure_logging(log_file=tmp_path / "b.log", console=io.StringIO())
    get_logger("atlas.test").info("once")
    assert _json_lines(tmp_path / "a.log") == []
    assert [line["event"] for line in _json_lines(tmp_path / "b.log")] == ["once"]


def test_reset_logging_removes_handlers(tmp_path: Path) -> None:
    configure_logging(log_file=tmp_path / "a.log", console=io.StringIO())
    reset_logging()
    get_logger("atlas.test").warning("after reset")
    assert _json_lines(tmp_path / "a.log") == []


def test_exception_is_rendered_in_json(tmp_path: Path) -> None:
    configure_logging(log_file=tmp_path / "a.log", console=io.StringIO())
    try:
        raise ValueError("bad input")
    except ValueError:
        get_logger("atlas.test").exception("boom")
    (line,) = _json_lines(tmp_path / "a.log")
    assert line["level"] == "error"
    assert "ValueError: bad input" in line["exception"]


def test_get_logger_before_configure() -> None:
    structlog.reset_defaults()
    assert not structlog.is_configured()
    get_logger("atlas.test").info("ok")
    assert structlog.is_configured()


def test_console_level_quiets_console_only(tmp_path: Path) -> None:  # Stage 14 (CLI)
    console = io.StringIO()
    configure_logging(
        level="INFO", log_file=tmp_path / "a.log", console=console, console_level="WARNING"
    )
    log = get_logger("atlas.test")
    log.info("detail")
    log.warning("problem")
    assert [line["event"] for line in _json_lines(tmp_path / "a.log")] == ["detail", "problem"]
    assert "problem" in console.getvalue()
    assert "detail" not in console.getvalue()


def test_a_helper_thread_of_the_run_is_tagged_with_it(tmp_path: Path) -> None:
    """Phase 2 checkpoint: a library logging from a thread it started (matplotlib's "building
    the font cache" timer) has no run context of its own; while exactly one run is open in the
    process, its records belong to that run."""
    import threading

    log = tmp_path / "run.log"
    with run_context(stage="probe") as ctx:
        configure_logging(level="INFO", log_file=log, console_level="WARNING")
        thread = threading.Thread(
            target=lambda: logging.getLogger("thirdparty").warning("from a thread")
        )
        thread.start()
        thread.join()
    reset_logging()
    records = _json_lines(log)
    assert records and all(r["run_id"] == ctx.run_id for r in records)
    assert records[-1]["event"] == "from a thread" and records[-1]["stage"] == "probe"


def test_a_thread_is_not_tagged_when_several_runs_are_open(tmp_path: Path) -> None:
    """With two runs open at once (in two threads), a record from a third thread cannot be
    attributed: it carries no run id rather than a guessed one."""
    import threading

    log = tmp_path / "run.log"
    configure_logging(level="INFO", log_file=log, console_level="WARNING")
    entered, release = threading.Barrier(3), threading.Event()

    def hold() -> None:
        with run_context(stage="held"):
            entered.wait()
            release.wait()

    holders = [threading.Thread(target=hold) for _ in range(2)]
    for h in holders:
        h.start()
    entered.wait()
    third = threading.Thread(target=lambda: logging.getLogger("thirdparty").warning("orphan"))
    third.start()
    third.join()
    release.set()
    for h in holders:
        h.join()
    with run_context(stage="after"):
        pass
    reset_logging()
    orphan = [r for r in _json_lines(log) if r["event"] == "orphan"]
    assert len(orphan) == 1 and "run_id" not in orphan[0]
