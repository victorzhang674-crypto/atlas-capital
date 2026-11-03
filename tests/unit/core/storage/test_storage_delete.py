"""Phase 1 checkpoint, Tier 3: `Lake.delete_rows` (rewrites only the files with matching rows)."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from storage_helpers import PricesFrame, make_prices_schema

from atlas.core.storage import Column, ColumnType, Lake, SchemaRegistry


def _mtimes(lake: Lake, name: str) -> dict[str, int]:
    root = lake.table_path(name)
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in lake.table_files(name)}


def test_deletes_matching_rows_only_in_their_files(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(symbols=("AAPL", "MSFT", "SPY")))
    before = _mtimes(lake, "prices_daily")
    gen = lake.generation
    removed = lake.delete_rows(
        "prices_daily", [("symbol", "=", "MSFT"), ("date", "=", date(2024, 1, 2))]
    )
    assert removed == 1
    out = lake.read_table("prices_daily")
    assert len(out) == 5
    assert not ((out["symbol"] == "MSFT") & (out["date"] == date(2024, 1, 2))).any()
    after = _mtimes(lake, "prices_daily")
    assert after["symbol=AAPL/data.parquet"] == before["symbol=AAPL/data.parquet"]
    assert after["symbol=SPY/data.parquet"] == before["symbol=SPY/data.parquet"]
    assert lake.generation == gen + 1


def test_emptied_partition_file_is_removed(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    assert lake.delete_rows("prices_daily", [("symbol", "=", "AAPL")]) == 2
    root = lake.table_path("prices_daily")
    assert [str(p.relative_to(root)) for p in lake.table_files("prices_daily")] == [
        "symbol=MSFT/data.parquet"
    ]
    assert set(lake.read_table("prices_daily")["symbol"]) == {"MSFT"}


def test_no_match_writes_nothing(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    before, gen = _mtimes(lake, "prices_daily"), lake.generation
    assert lake.delete_rows("prices_daily", [("symbol", "=", "TSLA")]) == 0
    assert _mtimes(lake, "prices_daily") == before
    assert lake.generation == gen


def test_unwritten_table_deletes_nothing(lake: Lake) -> None:
    assert lake.delete_rows("prices_daily", [("symbol", "=", "AAPL")]) == 0


def test_null_filter_result_is_not_a_match(tmp_path: Path, prices_frame: PricesFrame) -> None:
    # `note != "x"` is null (unknown) for a null note: such a row must be kept, not deleted.
    schema = make_prices_schema(
        columns=(
            *make_prices_schema().columns,
            Column(name="note", dtype=ColumnType.STRING, nullable=True),
        )
    )
    lake = Lake(tmp_path / "lake", registry=SchemaRegistry([schema]))
    frame = prices_frame().assign(note=pd.Series(["x", None, "y", None], dtype="object"))
    lake.write_table("prices_daily", frame)
    assert lake.delete_rows("prices_daily", [("note", "!=", "x")]) == 1  # only "y"
    assert sorted(lake.read_table("prices_daily")["note"].fillna("<null>")) == [
        "<null>",
        "<null>",
        "x",
    ]


@pytest.mark.parametrize(
    ("filters", "error"),
    [([], "at least one filter"), ([("nope", "=", 1)], "nope")],
    ids=["no filters", "unknown column"],
)
def test_bad_filters_rejected(
    lake: Lake, prices_frame: PricesFrame, filters: list[tuple[str, str, object]], error: str
) -> None:
    lake.write_table("prices_daily", prices_frame())
    with pytest.raises(ValueError, match=error):
        lake.delete_rows("prices_daily", filters)
    assert len(lake.read_table("prices_daily")) == 4
