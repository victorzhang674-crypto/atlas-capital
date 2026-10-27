"""Stage 07 Task 1: the price provider contract, request checks, retry and throttle."""

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from price_helpers import FakePriceProvider, daily_rows

from atlas.core.config import AtlasConfig, PriceDataConfig, load_config
from atlas.core.errors import DataError
from atlas.data.providers.base import (
    ACTION_COLUMNS,
    DAILY_COLUMNS,
    PriceProvider,
    RetryPolicy,
    Throttle,
    call_with_retry,
    check_request,
    normalize_actions,
    normalize_daily,
)

D1, D2 = date(2024, 3, 1), date(2024, 3, 4)


def test_fake_provider_satisfies_protocol() -> None:
    assert isinstance(FakePriceProvider(), PriceProvider)


def test_normalize_daily_orders_columns_and_dtypes() -> None:
    raw = pd.DataFrame(
        {
            "vendor": ["v", "v"],
            "volume": [10, 20],
            "symbol": ["MSFT", "AAPL"],
            "date": pd.to_datetime(["2024-03-04", "2024-03-01"]),
            "open": [1, 2],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.0, 2.0],
            "adj_close": [1.0, 2.0],
        },
        index=[7, 3],
    )
    out = normalize_daily(raw)
    assert tuple(out.columns) == DAILY_COLUMNS
    assert list(out["symbol"]) == ["AAPL", "MSFT"]
    assert list(out["date"]) == [D1, D2]
    assert all(type(d) is date for d in out["date"])
    assert out["open"].dtype == np.float64
    assert out["volume"].dtype == np.int64
    assert list(out.index) == [0, 1]


def test_normalize_daily_missing_column_raises() -> None:
    frame = daily_rows("AAPL", [(D1, 1.0, 1)]).drop(columns=["adj_close"])
    with pytest.raises(DataError, match="adj_close"):
        normalize_daily(frame)


def test_normalize_actions_dtypes_and_nullable_ratio_amount() -> None:
    raw = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL"],
            "ex_date": [date(2020, 8, 31), date(2020, 8, 7)],
            "type": ["split", "dividend"],
            "ratio": [4.0, None],
            "amount": [None, 0.82],
            "vendor": ["v", "v"],
        }
    )
    out = normalize_actions(raw)
    assert tuple(out.columns) == ACTION_COLUMNS
    assert list(out["type"]) == ["dividend", "split"]  # sorted by ex_date
    assert out["ratio"].dtype == np.float64 and out["amount"].dtype == np.float64
    assert math.isnan(out["ratio"].iloc[0]) and out["amount"].iloc[0] == 0.82


def test_normalize_actions_rejects_unknown_type() -> None:
    raw = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "ex_date": [D1],
            "type": ["spinoff"],
            "ratio": [None],
            "amount": [1.0],
            "vendor": ["v"],
        }
    )
    with pytest.raises(DataError, match="spinoff"):
        normalize_actions(raw)


def test_check_request_normalises_and_dedupes() -> None:
    assert check_request(["aapl", " AAPL", "brk-b"], D1, D2) == ("AAPL", "BRK.B")
    assert check_request([], D1, D1) == ()


def test_check_request_rejects_reversed_window() -> None:
    with pytest.raises(ValueError, match="start"):
        check_request(["AAPL"], D2, D1)


def test_check_request_rejects_invalid_symbol() -> None:
    with pytest.raises(ValueError, match="invalid symbol"):
        check_request(["AAPL", "not a ticker"], D1, D2)


def _policy() -> RetryPolicy:
    return RetryPolicy(max_retries=2, backoff_seconds=1.0, min_interval_seconds=0.5)


def test_call_with_retry_backs_off_then_succeeds() -> None:
    sleeps: list[float] = []
    attempts = iter([OSError("boom"), OSError("boom"), "ok"])

    def fn() -> str:
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    assert call_with_retry(fn, _policy(), sleeps.append, "test fetch", (OSError,)) == "ok"
    assert sleeps == [1.0, 2.0]


def test_call_with_retry_exhausted_raises_data_error() -> None:
    sleeps: list[float] = []

    def fn() -> str:
        raise OSError("vendor down")

    with pytest.raises(DataError, match=r"test fetch.*3 attempt") as info:
        call_with_retry(fn, _policy(), sleeps.append, "test fetch", (OSError,))
    assert isinstance(info.value.__cause__, OSError)
    assert sleeps == [1.0, 2.0]


def test_call_with_retry_does_not_catch_other_errors() -> None:
    def fn() -> str:
        raise KeyError("bug")

    with pytest.raises(KeyError):
        call_with_retry(fn, _policy(), lambda s: None, "test fetch", (OSError,))


def test_throttle_pauses_between_calls() -> None:
    sleeps: list[float] = []
    throttle = Throttle(0.5, sleeps.append)
    throttle.wait()
    throttle.wait()
    throttle.wait()
    assert sleeps == [0.5, 0.5]


def test_retry_policy_from_config() -> None:
    policy = RetryPolicy.from_config(PriceDataConfig(max_retries=5, backoff_seconds=0.25))
    assert (policy.max_retries, policy.backoff_seconds) == (5, 0.25)


def test_price_config_defaults_and_yaml(repo_root: Path) -> None:
    assert AtlasConfig().data.prices == PriceDataConfig()
    cfg = load_config(config_dir=repo_root / "config")
    prices = cfg.data.prices
    assert prices.adj_mismatch_tolerance == 0.005
    assert prices.alpaca_feed == "iex"
    assert prices.batch_size == 50
    assert prices.max_retries == 3
    assert prices.alpaca_data_url == "https://data.alpaca.markets"


def test_price_config_rejects_percent_typed_as_fraction() -> None:
    with pytest.raises(ValueError):
        PriceDataConfig(adj_mismatch_tolerance=5)
