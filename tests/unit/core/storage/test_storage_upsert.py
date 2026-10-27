"""Tests for upsert semantics, idempotency and the 1M-row target (Stage 06, task 3)."""

import time
from datetime import date

import numpy as np
import pandas as pd
import pytest
from storage_helpers import PricesFrame

from atlas.core.storage import Lake

_D1, _D2 = date(2024, 1, 2), date(2024, 1, 3)


def test_upsert_later_ingestion_wins(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=(_D1, _D2), close=1.0))
    msft = lake.table_path("prices_daily") / "symbol=MSFT" / "data.parquet"
    msft_stat = msft.stat()
    result = lake.write_table(
        "prices_daily", prices_frame(symbols=("AAPL",), days=(_D1,), close=50.0), mode="upsert"
    )
    assert (result.mode, result.rows, result.partitions) == ("upsert", 1, 1)
    out = lake.read_table("prices_daily")
    assert len(out) == 4
    aapl = out[out["symbol"] == "AAPL"].set_index("date")["close"].to_dict()
    assert aapl == {_D1: 50.0, _D2: 2.0}  # replaced row; the other AAPL row is untouched
    assert msft.stat().st_mtime_ns == msft_stat.st_mtime_ns  # untouched partition not rewritten
    assert msft.stat().st_ino == msft_stat.st_ino


def test_upsert_adds_new_keys_and_partitions(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=(_D1,)))
    lake.write_table(
        "prices_daily", prices_frame(symbols=("AAPL", "NVDA"), days=(_D2,)), mode="upsert"
    )
    out = lake.read_table("prices_daily")
    assert sorted(zip(out["symbol"], out["date"], strict=True)) == [
        ("AAPL", _D1),
        ("AAPL", _D2),
        ("MSFT", _D1),
        ("NVDA", _D2),
    ]


def test_upsert_batch_duplicates_keep_last(lake: Lake, prices_frame: PricesFrame) -> None:
    batch = pd.concat(
        [
            prices_frame(symbols=("AAPL",), days=(_D1,), close=1.0),
            prices_frame(symbols=("AAPL",), days=(_D1,), close=2.0),
            prices_frame(symbols=("AAPL",), days=(_D1,), close=3.0),
        ],
        ignore_index=True,
    )
    result = lake.write_table("prices_daily", batch, mode="upsert")
    assert result.rows == 1
    assert list(lake.read_table("prices_daily")["close"]) == [3.0]


def test_upsert_is_idempotent(lake: Lake, prices_frame: PricesFrame) -> None:
    batch = prices_frame(symbols=("AAPL", "MSFT", "BRK.B"), days=(_D1, _D2))
    lake.write_table("prices_daily", batch, mode="upsert")
    first = lake.read_table("prices_daily")
    files = lake.table_files("prices_daily")
    lake.write_table("prices_daily", batch, mode="upsert")
    pd.testing.assert_frame_equal(lake.read_table("prices_daily"), first)
    assert lake.table_files("prices_daily") == files
    assert len(first) == 6


def test_upsert_into_new_table_creates_it(lake: Lake, prices_frame: PricesFrame) -> None:
    assert not lake.has_table("prices_daily")
    lake.write_table("prices_daily", prices_frame(), mode="upsert")
    assert lake.has_table("prices_daily")
    assert len(lake.read_table("prices_daily")) == 4


def test_upsert_empty_batch_is_a_noop(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    result = lake.write_table("prices_daily", prices_frame().iloc[0:0], mode="upsert")
    assert (result.rows, result.partitions) == (0, 0)
    assert len(lake.read_table("prices_daily")) == 4


def test_upsert_keys_must_match_primary_key(lake: Lake, prices_frame: PricesFrame) -> None:
    with pytest.raises(ValueError, match="primary key"):
        lake.write_table("prices_daily", prices_frame(), keys=["symbol"], mode="upsert")
    lake.write_table("prices_daily", prices_frame(), keys=["date", "symbol"], mode="upsert")
    assert len(lake.read_table("prices_daily")) == 4


def _synthetic(symbols: int, first_day: int, days: int, close: float) -> pd.DataFrame:
    """`symbols` times `days` rows of `prices_daily`, vectorised (days counted from 2000-01-03)."""
    sym = np.repeat(np.array([f"S{i:04d}" for i in range(symbols)]), days)
    day_index = np.tile(np.arange(first_day, first_day + days), symbols)
    day = pd.Timestamp("2000-01-03") + pd.to_timedelta(day_index, unit="D")
    return pd.DataFrame(
        {
            "symbol": pd.Series(sym, dtype="str"),
            "date": day,  # naive midnight datetime64, stored as date32
            "close": np.full(len(sym), close),
            "volume": np.arange(len(sym), dtype="int64"),
            "available_at": (day + pd.Timedelta(hours=21, minutes=15)).tz_localize("UTC"),
        }
    )


def test_upsert_one_million_rows_under_five_seconds(lake: Lake) -> None:
    lake.write_table("prices_daily", _synthetic(500, 0, 2_000, close=1.0))  # seed, not timed
    batch = _synthetic(500, 1_000, 2_000, close=2.0)  # days 1000-2999: half overlap, half new
    assert len(batch) == 1_000_000

    start = time.perf_counter()
    result = lake.write_table("prices_daily", batch, mode="upsert")
    elapsed = time.perf_counter() - start
    print(f"\n1M-row upsert into a 1M-row table (500 partitions): {elapsed:.2f} s")

    assert result.rows == 1_000_000
    assert elapsed < 5.0
    out = lake.read_table("prices_daily", filters=[("symbol", "=", "S0007")])
    assert len(out) == 3_000
    assert (out["close"].iloc[:1_000] == 1.0).all()
    assert (out["close"].iloc[1_000:] == 2.0).all()
    assert len(lake.read_table("prices_daily", columns=["symbol"])) == 1_500_000
