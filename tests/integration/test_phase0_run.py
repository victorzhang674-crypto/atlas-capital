"""Phase 0 end to end, offline: repo config, simulated clock, seeded ids, run context, audit
log and its DuckDB mirror working together (phase 0 checkpoint)."""

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from atlas.core.audit import AuditLog, EventType, audit_path
from atlas.core.clock import SimClock, use_clock
from atlas.core.config import config_fingerprint, load_config
from atlas.core.run_context import run_context
from atlas.core.storage import Database, Lake
from atlas.core.types import IdPrefix, SeededIds, new_id, use_ids

pytestmark = pytest.mark.integration

_SESSIONS = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)]


def _backtest_run(config_dir: Path, lake_root: Path, seed: int) -> tuple[str, pd.DataFrame]:
    """One seeded backtest-profile run over three sessions, each recording one ORDER event;
    returns the run id and the events as mirrored into DuckDB, in order."""
    cfg = load_config(profile="backtest", config_dir=config_dir)
    lake = Lake(lake_root)
    clock = SimClock(start=_SESSIONS[0])
    with use_clock(clock), use_ids(SeededIds(seed, clock)), run_context(config=cfg) as ctx:
        assert (ctx.mode, ctx.config_fingerprint) == ("backtest", config_fingerprint(cfg))
        log = AuditLog(audit_path(lake.root, ctx.run_id))
        for _ in _SESSIONS:
            with run_context(as_of=clock.today(), stage="execution"):
                log.record(EventType.ORDER, "oms", {"order_id": new_id(IdPrefix.ORDER)})
            clock.advance()
    assert AuditLog.verify_chain(log.path)
    with Database(lake) as db:
        assert db.mirror_audit_log(log.path) == len(_SESSIONS)
        rows = db.query(
            "select event_id, ts, as_of, payload->>'order_id' as order_id from audit_events "
            "where run_id = ? order by seq",
            [ctx.run_id],
        )
    return ctx.run_id, rows


def test_seeded_backtest_run_is_reproducible(repo_root: Path, tmp_path: Path) -> None:
    config_dir = repo_root / "config"
    run_a, first = _backtest_run(config_dir, tmp_path / "a", seed=7)
    run_b, again = _backtest_run(config_dir, tmp_path / "b", seed=7)
    _, other = _backtest_run(config_dir, tmp_path / "c", seed=8)

    assert first["as_of"].tolist() == _SESSIONS
    decision = load_config(profile="backtest", config_dir=config_dir).calendar.decision_time
    assert first["ts"].tolist() == [  # each event is stamped with its session's decision time
        pd.Timestamp(datetime.combine(d, decision), tz="America/New_York").tz_convert("UTC")
        for d in _SESSIONS
    ]
    assert run_a != run_b  # a re-run never reuses an earlier run's folder
    pd.testing.assert_frame_equal(first, again)
    assert set(first["event_id"]).isdisjoint(other["event_id"])
    assert set(first["order_id"]).isdisjoint(other["order_id"])
