"""Stage 15 Task 16: all features for 500 symbols x 2,000 sessions in under 20 s."""

import time
from datetime import date

import numpy as np
import pandas as pd
import pytest

from atlas.core.calendar import TradingCalendar
from atlas.core.pit import bar_available_at
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.prices import PRICES_DAILY
from atlas.data.view import DataView
from atlas.quant.features import REGISTRY, compute_features

N_SYMBOLS = 500
N_SESSIONS = 2000
LIMIT_SECONDS = 20.0
LAST = date(2024, 2, 29)


@pytest.fixture(scope="module")
def big_lake(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lake, list[str]]:
    """500 random-walk symbols and SPY, 2,000 sessions each (1,002,000 bars)."""
    cal = TradingCalendar()
    days = list(cal.sessions(cal.offset(LAST, -(N_SESSIONS - 1)), LAST))
    symbols = [f"S{i:03d}" for i in range(N_SYMBOLS)]
    names = [*symbols, "SPY"]
    rng = np.random.default_rng(2000)
    shape = (N_SESSIONS, len(names))
    close = 50.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.015, shape), axis=0))
    open_ = np.vstack([close[:1], close[:-1]]) * (1 + rng.normal(0, 0.003, shape))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.005, shape)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.005, shape)))
    volume = rng.lognormal(14.5, 0.4, shape).round().astype("int64")
    stamps = pd.to_datetime(
        pd.Series([bar_available_at(d, cal) for d in days], dtype="object"), utc=True
    ).astype("datetime64[us, UTC]")
    frame = pd.DataFrame(
        {
            "symbol": np.tile(np.array(names, dtype=object), N_SESSIONS),
            "date": np.repeat(np.array(days, dtype=object), len(names)),
            "open": open_.ravel(),
            "high": high.ravel(),
            "low": low.ravel(),
            "close": close.ravel(),
            "volume": volume.ravel(),
            "adj_close": close.ravel(),
            "vendor": "yfinance",
            "available_at": np.repeat(stamps.to_numpy(), len(names)),
        }
    )[list(PRICES_DAILY.column_names)]
    lake = Lake(tmp_path_factory.mktemp("perf") / "lake", registry=DEFAULT_REGISTRY.copy())
    lake.write_table("prices_daily", frame)
    return lake, symbols


@pytest.mark.timing
def test_all_features_500x2000_under_20s(
    big_lake: tuple[Lake, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    lake, symbols = big_lake
    view = DataView(lake, date(2024, 3, 1))
    start = time.perf_counter()
    out = compute_features(view, symbols, history=N_SESSIONS)
    elapsed = time.perf_counter() - start
    with capsys.disabled():
        size = f"{len(out)} features x {N_SYMBOLS} symbols x {N_SESSIONS} sessions"
        print(f"\ncompute_features: {size} in {elapsed:.2f} s (limit {LIMIT_SECONDS:.0f} s)")
    assert elapsed < LIMIT_SECONDS
    assert list(out) == list(REGISTRY.names())
    for name, frame in out.items():
        assert frame.shape == (N_SESSIONS, N_SYMBOLS), name
        lookback = REGISTRY.get(name).lookback
        assert frame.iloc[lookback - 1 :].notna().all().all(), name  # full data: no gaps
