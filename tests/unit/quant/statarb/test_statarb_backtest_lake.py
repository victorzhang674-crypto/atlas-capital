"""Phase 2 checkpoint, Tier 4 item 2 (Stage 23 code): `pairs_backtest_from_view`, the
backtest's inputs read point in time through the view of the session after the window (as
the Stage 23 in-sample run did), for `atlas research pairs --backtest`."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from statarb_world import CAL, LAST, build_pairs_lake, planted_universe

from atlas.core.config import AtlasConfig
from atlas.core.errors import DataError
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import DataView
from atlas.quant.alpha.multifactor import member_sectors
from atlas.quant.statarb import pairs_backtest, pairs_backtest_from_view

CFG = AtlasConfig()
START = CAL.offset(LAST, -70)  # about three monthly formations after 504 sessions of history
END = CAL.offset(LAST, -10)  # ten sessions before the lake's last bar


def _view(lake: Lake, last: date) -> DataView:
    """The view of the session after `last` (its latest known bar is `last`)."""
    return DataView(lake, CAL.next_session(last), CFG)


def _lake(root: Path) -> Lake:
    lake = Lake(root / "lake", registry=DEFAULT_REGISTRY.copy())
    build_pairs_lake(lake, planted_universe(101))
    return lake


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    return _lake(tmp_path_factory.mktemp("full"))


@pytest.fixture(scope="module")
def cut(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    """The same lake without any bar after `END`."""
    lake = _lake(tmp_path_factory.mktemp("cut"))
    lake.delete_rows("prices_daily", [("date", ">", END)])
    return lake


def test_equals_pairs_backtest_on_the_views_inputs(lake: Lake) -> None:
    got = pairs_backtest_from_view(_view(lake, END), START, END)
    view = DataView(lake, CAL.next_session(END), CFG)
    cfg = CFG.quant.statarb
    n = len(CAL.sessions(START, END)) + cfg.formation + 1
    members = sorted({m for d in CAL.sessions(START, END) for m in view.members_on(d)})
    returns = view.returns(members, n - 1)
    returns = returns.loc[:, returns.notna().any()]
    want = pairs_backtest(
        returns,
        member_sectors(view, list(returns.columns)),
        start=START,
        end=END,
        members=lambda d: view.members_on(d, cfg.universe),
        config=cfg,
        sessions_per_year=CFG.quant.features.sessions_per_year,
    )
    assert got.summary["n_formations"] >= 3 and got.summary["n_trades"] > 0
    pd.testing.assert_series_equal(got.backtest.returns, want.backtest.returns)
    pd.testing.assert_frame_equal(got.trades, want.trades)
    pd.testing.assert_frame_equal(got.selections, want.selections)


def test_reads_nothing_after_the_end(lake: Lake, cut: Lake) -> None:
    """The same answer from a lake that has no bar after `END`: nothing later was read."""
    full = pairs_backtest_from_view(_view(lake, END), START, END)
    short = pairs_backtest_from_view(_view(cut, END), START, END)
    assert full.backtest.returns.index[-1].date() == END
    pd.testing.assert_series_equal(full.backtest.returns, short.backtest.returns)
    pd.testing.assert_frame_equal(full.trades, short.trades)


def test_an_end_that_is_not_a_session_uses_the_session_before(lake: Lake) -> None:
    saturday = next(
        d for d in (END + pd.Timedelta(days=k) for k in range(1, 8)) if d.weekday() == 5
    )
    last = CAL.prev_session(saturday)
    got = pairs_backtest_from_view(_view(lake, last), START, saturday)
    assert got.backtest.returns.index[-1].date() == last


def test_bad_windows(lake: Lake) -> None:
    with pytest.raises(ValueError, match="after"):
        pairs_backtest_from_view(_view(lake, END), END, START)
    with pytest.raises(ValueError, match="needs the view of"):
        pairs_backtest_from_view(_view(lake, LAST), START, END)  # a later view would see more
    early = date(2005, 6, 30)
    with pytest.raises(DataError, match="universe"):
        pairs_backtest_from_view(_view(lake, early), date(2005, 1, 3), early)
