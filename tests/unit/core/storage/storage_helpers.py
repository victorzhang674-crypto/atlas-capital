"""Helpers shared by the storage tests: a `prices_daily`-like schema and frames for it."""

from collections.abc import Callable, Sequence
from datetime import date

import pandas as pd

from atlas.core.storage import Column, ColumnType, TableSchema

PricesFrame = Callable[..., pd.DataFrame]


def make_prices_schema(**overrides: object) -> TableSchema:
    """`prices_daily`: one row per (symbol, trading day), partitioned by symbol."""
    fields: dict[str, object] = {
        "name": "prices_daily",
        "columns": (
            Column(name="symbol", dtype=ColumnType.STRING),
            Column(name="date", dtype=ColumnType.DATE),
            Column(name="close", dtype=ColumnType.FLOAT64),
            Column(name="volume", dtype=ColumnType.INT64),
            Column(name="available_at", dtype=ColumnType.TIMESTAMP),
        ),
        "primary_key": ("symbol", "date"),
        "partition_by": ("symbol",),
    }
    fields.update(overrides)
    return TableSchema.model_validate(fields)


def make_prices_frame(
    symbols: Sequence[str] = ("AAPL", "MSFT"),
    days: Sequence[date] = (date(2024, 1, 2), date(2024, 1, 3)),
    close: float = 100.0,
) -> pd.DataFrame:
    rows = [(s, d) for s in symbols for d in days]
    frame = pd.DataFrame(
        {
            "symbol": pd.Series([s for s, _ in rows], dtype="str"),
            "date": pd.Series([d for _, d in rows], dtype="object"),
            "close": [close + i for i in range(len(rows))],
            "volume": pd.Series(range(1000, 1000 + len(rows)), dtype="int64"),
        }
    )
    # available at 16:15 New York time on the bar's own day
    frame["available_at"] = (
        pd.to_datetime(pd.Series([d.isoformat() for _, d in rows]))
        + pd.Timedelta(hours=16, minutes=15)
    ).dt.tz_localize("America/New_York")
    return frame
