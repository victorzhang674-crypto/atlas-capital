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
