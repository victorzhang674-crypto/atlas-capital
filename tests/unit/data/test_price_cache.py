"""Stage 07 Task 5: the on-disk price cache."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from price_helpers import FakePriceProvider, daily_rows

from atlas.core.clock import SimClock
from atlas.core.errors import DataError
from atlas.data.providers.base import PriceProvider
from atlas.data.providers.cache import CachedPriceProvider

D1, D2 = date(2024, 2, 26), date(2024, 2, 27)  # before the sim_clock's 2024-03-01


def _inner(**kwargs: object) -> FakePriceProvider:
    daily = pd.concat(
        [
            daily_rows("AAPL", [(D1, 10.0, 100), (D2, 11.0, 200)]),
            daily_rows("SPY", [(D1, 50.0, 300), (D2, 51.0, 400)]),
        ]
    )
    actions = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "ex_date": [D2],
            "type": ["dividend"],
            "ratio": [float("nan")],
            "amount": [0.24],
            "vendor": ["fake"],
        }
    )
    return FakePriceProvider(daily, actions, **kwargs)  # type: ignore[arg-type]


def test_is_a_price_provider(tmp_path: Path) -> None:
    cached = CachedPriceProvider(_inner(), tmp_path)
    assert isinstance(cached, PriceProvider)
    assert cached.name == "fake"


def test_second_call_is_served_from_cache(tmp_path: Path, sim_clock: SimClock) -> None:
    inner = _inner()
    cached = CachedPriceProvider(inner, tmp_path)
    first = cached.fetch_daily(["AAPL", "SPY"], D1, D2)
    second = cached.fetch_daily(["SPY", "AAPL"], D1, D2)
    assert len(inner.calls) == 1
    pd.testing.assert_frame_equal(first, second)
    again = CachedPriceProvider(_inner(), tmp_path)  # files persist across instances
    pd.testing.assert_frame_equal(again.fetch_daily(["AAPL", "SPY"], D1, D2), first)


def test_cache_key_depends_on_symbols_and_window(tmp_path: Path, sim_clock: SimClock) -> None:
    inner = _inner()
    cached = CachedPriceProvider(inner, tmp_path)
    cached.fetch_daily(["AAPL"], D1, D2)
    cached.fetch_daily(["AAPL", "SPY"], D1, D2)
    cached.fetch_daily(["AAPL"], D2, D2)
    assert [call[1:] for call in inner.calls] == [
        (("AAPL",), D1, D2),
        (("SPY",), D1, D2),  # only the symbol not yet cached
        (("AAPL",), D2, D2),
    ]


def test_window_reaching_today_is_not_cached(tmp_path: Path, sim_clock: SimClock) -> None:
    inner = _inner()
    cached = CachedPriceProvider(inner, tmp_path)
    today = sim_clock.today()
    cached.fetch_daily(["AAPL"], D1, today)
    cached.fetch_daily(["AAPL"], D1, today)
    assert len(inner.calls) == 2
    assert not list(tmp_path.rglob("*.parquet"))


def test_inner_error_is_not_cached(tmp_path: Path, sim_clock: SimClock) -> None:
    cached = CachedPriceProvider(_inner(fail_all=True), tmp_path)
    with pytest.raises(DataError):
        cached.fetch_daily(["AAPL"], D1, D2)
    assert not list(tmp_path.rglob("*.parquet"))


def test_missing_symbol_is_not_cached(tmp_path: Path, sim_clock: SimClock) -> None:
    inner = _inner()
    cached = CachedPriceProvider(inner, tmp_path)
    assert cached.fetch_daily(["SIVB"], D1, D2).empty
    cached.fetch_daily(["SIVB"], D1, D2)
    assert len(inner.calls) == 2


def test_actions_cached_only_for_symbols_with_cached_bars(
    tmp_path: Path, sim_clock: SimClock
) -> None:
    inner = _inner()
    cached = CachedPriceProvider(inner, tmp_path)
    cached.fetch_daily(["AAPL", "SPY"], D1, D2)
    first = cached.fetch_actions(["AAPL", "SPY", "SIVB"], D1, D2)
    second = cached.fetch_actions(["AAPL", "SPY", "SIVB"], D1, D2)
    pd.testing.assert_frame_equal(first, second)
    action_calls = [call[1] for call in inner.calls if call[0] == "actions"]
    # AAPL (one dividend) and SPY (none) are cached; SIVB has no cached bars, so it is re-asked
    assert action_calls == [("AAPL", "SPY", "SIVB"), ("SIVB",)]
    assert list(first["symbol"]) == ["AAPL"]
