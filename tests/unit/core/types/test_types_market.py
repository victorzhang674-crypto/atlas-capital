"""Tests for `Bar` validation."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.types.market import Bar

GOOD: dict[str, Any] = {
    "symbol": "aapl",
    "date": date(2024, 1, 2),
    "open": 100.0,
    "high": 105.0,
    "low": 99.0,
    "close": 104.0,
    "adj_close": 103.5,
    "volume": 1_000_000.0,
}


def test_valid_bar() -> None:
    bar = Bar(**GOOD)
    assert bar.symbol == "AAPL"
    assert bar.high == 105.0


def test_doji_bar_ok() -> None:
    Bar(**{**GOOD, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0})


@pytest.mark.parametrize(
    "change",
    [
        {"high": 99.5},  # high < open
        {"open": 100.0, "close": 106.0},  # high < close
        {"low": 101.0},  # low > open
        {"open": 103.0, "close": 102.0, "low": 102.5},  # low > close
        {"high": 98.0},  # high < low
        {"low": 0.0},
        {"open": -1.0},
        {"adj_close": 0.0},
        {"volume": -1.0},
        {"close": float("nan")},
        {"high": float("inf")},
        {"vendor": "yfinance"},  # unknown key
    ],
)
def test_bad_bars_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Bar(**{**GOOD, **change})
