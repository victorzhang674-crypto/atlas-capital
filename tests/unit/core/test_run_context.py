"""Tests for the run context (contextvar + context manager)."""

import contextvars
import threading
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.core.clock import SimClock
from atlas.core.config import AtlasConfig, config_fingerprint
from atlas.core.errors import AtlasError
from atlas.core.run_context import RunContext, current_run_context, run_context, run_dir
from atlas.core.types.ids import SeededIds, use_ids


def _in_thread(fn: object) -> list[object]:
    out: list[object] = []
    thread = threading.Thread(target=lambda: out.append(fn()))  # type: ignore[operator]
    thread.start()
    thread.join()
    return out


def test_top_level_opens_new_run() -> None:
    assert current_run_context() is None
    with run_context(mode="backtest", as_of=date(2024, 1, 2)) as ctx:
        assert len(ctx.run_id) == 26
        assert ctx.mode == "backtest"
        assert ctx.as_of == date(2024, 1, 2)
        assert ctx.stage is None
        assert ctx.config_fingerprint is None
        assert current_run_context() is ctx
    assert current_run_context() is None


def test_default_mode_is_dev() -> None:
    with run_context() as ctx:
        assert ctx.mode == "dev"
        assert ctx.as_of is None


def test_each_run_gets_new_id() -> None:
    with run_context() as first:
        pass
    with run_context() as second:
        pass
    assert first.run_id != second.run_id


def test_config_sets_mode_and_fingerprint() -> None:
    cfg = AtlasConfig(profile="paper")
    with run_context(config=cfg) as ctx:
        assert ctx.mode == "paper"
        assert ctx.config_fingerprint == config_fingerprint(cfg)
    with run_context(mode="paper", config=cfg) as ctx:
        assert ctx.mode == "paper"


def test_mode_conflicting_with_config_raises() -> None:
    with (
        pytest.raises(AtlasError, match="mode"),
        run_context(mode="dev", config=AtlasConfig(profile="paper")),
    ):
        pass
    assert current_run_context() is None


def test_invalid_mode_rejected() -> None:
    # Paper trading only: there is no "live" mode.
    with pytest.raises(ValidationError), run_context(mode="live"):  # type: ignore[arg-type]
        pass
    assert current_run_context() is None


def test_nested_inherits_and_overrides() -> None:
    cfg = AtlasConfig(profile="backtest")
    with run_context(config=cfg, as_of=date(2024, 1, 2)) as outer:
        with run_context(as_of=date(2024, 1, 3), stage="risk") as inner:
            assert inner.run_id == outer.run_id
            assert inner.mode == "backtest"
            assert inner.config_fingerprint == outer.config_fingerprint
            assert inner.as_of == date(2024, 1, 3)
            assert inner.stage == "risk"
            with run_context() as innermost:
                assert innermost.as_of == date(2024, 1, 3)
                assert innermost.stage == "risk"
        with run_context(stage="research") as other:
            assert other.as_of == date(2024, 1, 2)
        assert current_run_context() is outer


def test_nested_cannot_change_mode_or_config() -> None:
    with run_context(mode="backtest") as outer:
        with pytest.raises(AtlasError, match="nested"), run_context(mode="backtest"):
            pass
        with pytest.raises(AtlasError, match="nested"), run_context(config=AtlasConfig()):
            pass
        assert current_run_context() is outer


def test_nested_restored_after_exception() -> None:
    with run_context() as outer:
        with pytest.raises(RuntimeError), run_context(stage="risk"):
            raise RuntimeError("boom")
        assert current_run_context() is outer
    assert current_run_context() is None


def test_new_thread_has_no_context() -> None:
    with run_context():
        assert _in_thread(current_run_context) == [None]


def test_copy_context_propagates() -> None:
    with run_context() as ctx:
        copied = contextvars.copy_context()
        assert _in_thread(lambda: copied.run(current_run_context)) == [ctx]


def test_run_context_is_frozen() -> None:
    with run_context() as ctx:
        with pytest.raises(ValidationError):
            ctx.stage = "risk"  # type: ignore[misc]
        assert isinstance(ctx, RunContext)


def test_run_dir() -> None:
    assert run_dir(Path("data"), "01ABC") == Path("data/runs/01ABC")


def test_run_id_stays_fresh_under_seeded_ids() -> None:  # Phase 0 checkpoint, Tier 3
    # A re-run must not reuse (and append to) the earlier run's `runs/<run_id>/` files.
    clock = SimClock(start=date(2024, 3, 1))
    ids = []
    for _ in range(2):
        with use_ids(SeededIds(1, clock)), run_context(mode="backtest") as ctx:
            ids.append(ctx.run_id)
    assert ids[0] != ids[1]
