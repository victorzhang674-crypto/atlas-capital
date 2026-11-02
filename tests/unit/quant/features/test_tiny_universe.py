"""Stage 15 Task 3: the shared `tiny_universe` fixture has the properties it promises."""

from datetime import date

import numpy as np
import pytest

from atlas.data.view import DataView
from tiny_universe import TinyUniverse


def test_tiny_universe_shape(tiny_universe: TinyUniverse) -> None:
    tu = tiny_universe
    assert tu.symbols == ("AAA", "BBB", "CCC", "DDD", "SPY")
    assert tu.stocks == ("AAA", "BBB", "CCC", "DDD")
    assert len(tu.sessions) == 300
    assert tu.sessions[-1] == date(2024, 2, 29)
    assert tu.as_of == date(2024, 3, 1)
    view = tu.view()
    assert isinstance(view, DataView)
    bars = view.prices(list(tu.symbols), 300)
    assert len(bars) == 5 * 300
    assert view.sessions(300) == tu.sessions
    assert view.universe() == list(tu.stocks)
    assert view.blocked == frozenset()


def test_tiny_universe_bars_are_consistent(tiny_universe: TinyUniverse) -> None:
    bars = tiny_universe.view().prices(list(tiny_universe.symbols), 300)
    assert (bars["low"] <= bars[["open", "close"]].min(axis=1)).all()
    assert (bars["high"] >= bars[["open", "close"]].max(axis=1)).all()
    assert (bars["low"] > 0).all()
    assert (bars["volume"] > 0).all()
    assert (bars["adj_close"] == bars["close"]).all()  # no corporate actions


def test_tiny_universe_known_betas_and_vols(tiny_universe: TinyUniverse) -> None:
    tu = tiny_universe
    rets = tu.view().returns(list(tu.symbols), 299)
    m = rets["SPY"].to_numpy()
    assert m.std(ddof=1) == pytest.approx(tu.market_vol, rel=1e-9)
    for symbol in tu.stocks:
        r = rets[symbol].to_numpy()
        slope, intercept = np.polyfit(m, r, 1)
        assert slope == pytest.approx(tu.betas[symbol], rel=1e-9)
        resid = r - (intercept + slope * m)
        assert resid.std(ddof=1) == pytest.approx(tu.idio_vol, rel=1e-9)
    assert dict(tu.betas) == {"AAA": 0.5, "BBB": 1.0, "CCC": 1.3, "DDD": 1.8}
