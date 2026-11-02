"""Stage 07 Task 8: live smoke tests against the real vendors (`-m live`; deselected by default).

They need network access; the Alpaca one also needs `ALPACA_API_KEY_ID` and
`ALPACA_API_SECRET_KEY` (environment or `.env`) and skips without them.
"""

from datetime import date
from pathlib import Path

import pytest

from atlas.core.config import load_config
from atlas.data.providers.alpaca_provider import AlpacaPriceProvider
from atlas.data.providers.base import ACTION_COLUMNS, DAILY_COLUMNS, PriceProvider
from atlas.data.providers.yfinance_provider import YFinanceProvider

pytestmark = pytest.mark.live

# A fixed, settled week (2024-03-04 → 2024-03-08: 5 sessions), so results are comparable.
START, END = date(2024, 3, 4), date(2024, 3, 8)


def _check_spy_week(provider: PriceProvider) -> None:
    out = provider.fetch_daily(["SPY"], START, END)
    assert tuple(out.columns) == DAILY_COLUMNS
    assert list(out["date"]) == [date(2024, 3, d) for d in (4, 5, 6, 7, 8)]
    assert (out[["open", "high", "low", "close", "adj_close"]] > 0).all().all()
    assert (out["high"] >= out["low"]).all()
    assert (out["volume"] > 0).all()
    assert set(out["vendor"]) == {provider.name}


def test_yfinance_live_smoke() -> None:
    _check_spy_week(YFinanceProvider())


def test_alpaca_live_smoke(repo_root: Path) -> None:
    provider = AlpacaPriceProvider.from_config(load_config(config_dir=repo_root / "config"))
    if provider is None:
        pytest.skip("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    try:
        _check_spy_week(provider)
    finally:
        provider.close()


def _check_aapl_split(provider: PriceProvider) -> None:
    # Phase 1 checkpoint: `fetch_actions` had no live test (Alpaca's only on a built fixture).
    out = provider.fetch_actions(["AAPL"], date(2020, 8, 24), date(2020, 9, 4))
    assert tuple(out.columns) == ACTION_COLUMNS
    splits = out[out["type"] == "split"]
    assert list(splits["ex_date"]) == [date(2020, 8, 31)]
    assert splits["ratio"].iloc[0] == 4.0
    assert set(out["vendor"]) == {provider.name}


def test_yfinance_live_actions() -> None:
    _check_aapl_split(YFinanceProvider())


def test_alpaca_live_actions(repo_root: Path) -> None:
    provider = AlpacaPriceProvider.from_config(load_config(config_dir=repo_root / "config"))
    if provider is None:
        pytest.skip("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    try:
        _check_aapl_split(provider)
    finally:
        provider.close()
