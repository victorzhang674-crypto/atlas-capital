"""Tests for the lake layout and partitioned Parquet write/read (Stage 06, task 2)."""

import io
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from storage_helpers import PricesFrame, make_prices_schema

from atlas.core.errors import DataError
from atlas.core.logging import configure_logging, reset_logging
from atlas.core.pit import AsOf, pit_filter
from atlas.core.run_context import run_context
from atlas.core.storage import Column, ColumnType, Lake, SchemaRegistry, TableSchema

_DAYS = (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))


def _relative_files(lake: Lake, name: str) -> list[str]:
    return sorted(str(p.relative_to(lake.table_path(name))) for p in lake.table_files(name))


def test_lake_creates_layout(tmp_path: Path) -> None:
    lake = Lake(tmp_path / "lake", registry=SchemaRegistry())
    for sub in ("raw", "curated", "cache", "runs"):
        assert (tmp_path / "lake" / sub).is_dir()
    assert lake.curated_dir == tmp_path / "lake" / "curated"
    raw = lake.raw_path("yfinance", "daily")
    assert raw == tmp_path / "lake" / "raw" / "yfinance" / "daily"
    assert raw.is_dir()
    for vendor, dataset in [("../x", "d"), ("v", "a/b"), ("", "d"), ("V", "d")]:
        with pytest.raises(ValueError):
            lake.raw_path(vendor, dataset)
    assert lake.table_path("prices_daily") == tmp_path / "lake" / "curated" / "prices_daily"


def test_roundtrip_dtypes(lake: Lake, prices_frame: PricesFrame) -> None:
    schema = make_prices_schema(
        name="bars",
        columns=(
            *make_prices_schema().columns,
            Column(name="vwap", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="note", dtype=ColumnType.STRING, nullable=True),
        ),
    )
    lake.registry.register(schema)
    frame = prices_frame(symbols=("MSFT", "AAPL")).assign(
        vwap=[1.5, np.nan, 2.5, 3.5], note=["a", None, "b", "c"]
    )
    lake.write_table("bars", frame)
    out = lake.read_table("bars")

    expected = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
    expected["available_at"] = expected["available_at"].dt.tz_convert("UTC").dt.as_unit("us")
    pd.testing.assert_frame_equal(out, expected)
    assert out["symbol"].dtype == "str"
    assert out["date"].dtype == object
    assert isinstance(out["date"].iloc[0], date)
    assert str(out["available_at"].dtype) == "datetime64[us, UTC]"
    assert out["volume"].dtype == "int64"
    msft_day2 = out[(out["symbol"] == "MSFT") & (out["date"] == date(2024, 1, 3))]
    assert msft_day2["vwap"].isna().all() and msft_day2["note"].isna().all()


def test_partition_layout_by_symbol(lake: Lake, prices_frame: PricesFrame) -> None:
    result = lake.write_table("prices_daily", prices_frame())
    assert (result.rows, result.partitions, result.mode) == (4, 2, "append")
    assert _relative_files(lake, "prices_daily") == [
        "symbol=AAPL/data.parquet",
        "symbol=MSFT/data.parquet",
    ]
    assert lake.has_table("prices_daily")


def test_unpartitioned_table_has_one_file(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.registry.register(make_prices_schema(name="flat", partition_by=()))
    lake.write_table("flat", prices_frame())
    assert _relative_files(lake, "flat") == ["data.parquet"]
    assert len(lake.read_table("flat")) == 4


def test_partition_by_year(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.registry.register(
        make_prices_schema(name="by_year", year_column="date", partition_by=("year",))
    )
    frame = prices_frame(days=(date(2023, 12, 29), date(2024, 1, 2)))
    lake.write_table("by_year", frame)
    assert _relative_files(lake, "by_year") == ["year=2023/data.parquet", "year=2024/data.parquet"]
    out = lake.read_table("by_year")
    assert "year" not in out.columns
    assert list(out["date"]) == [date(2023, 12, 29), date(2024, 1, 2)] * 2


def test_partition_value_with_slash_stays_inside_table(
    lake: Lake, prices_frame: PricesFrame
) -> None:
    lake.write_table("prices_daily", prices_frame(symbols=("BRK/B", "..", "A B")))
    root = lake.table_path("prices_daily").resolve()
    files = lake.table_files("prices_daily")
    assert len(files) == 3
    for path in files:
        assert path.resolve().parent.parent == root
    assert sorted(set(lake.read_table("prices_daily")["symbol"])) == ["..", "A B", "BRK/B"]


def test_append_rejects_existing_or_repeated_keys(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    before = lake.read_table("prices_daily")
    overlap = prices_frame(symbols=("MSFT", "NVDA"), days=(date(2024, 1, 3),))
    with pytest.raises(DataError, match="already"):
        lake.write_table("prices_daily", overlap)
    pd.testing.assert_frame_equal(lake.read_table("prices_daily"), before)
    assert not (lake.table_path("prices_daily") / "symbol=NVDA").exists()

    repeated = pd.concat([prices_frame(symbols=("NVDA",))] * 2, ignore_index=True)
    with pytest.raises(DataError, match="duplicate"):
        lake.write_table("prices_daily", repeated)


def test_append_adds_rows(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=_DAYS[:2]))
    lake.write_table("prices_daily", prices_frame(days=_DAYS[2:]))
    assert len(lake.read_table("prices_daily")) == 6
    assert len(lake.table_files("prices_daily")) == 2


def test_overwrite_replaces_table(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(symbols=("AAPL", "MSFT")))
    result = lake.write_table("prices_daily", prices_frame(symbols=("NVDA",)), mode="overwrite")
    assert result.mode == "overwrite"
    assert _relative_files(lake, "prices_daily") == ["symbol=NVDA/data.parquet"]
    assert set(lake.read_table("prices_daily")["symbol"]) == {"NVDA"}
    leftovers = [p.name for p in lake.curated_dir.iterdir()]
    assert sorted(leftovers) == ["_meta.json", "prices_daily"]  # no temp directories left


def test_overwrite_with_empty_frame_empties_table(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    lake.write_table("prices_daily", prices_frame().iloc[0:0], mode="overwrite")
    assert lake.table_files("prices_daily") == ()
    assert lake.read_table("prices_daily").empty
    assert lake.has_table("prices_daily")


def test_bad_mode_rejected(lake: Lake, prices_frame: PricesFrame) -> None:
    with pytest.raises(ValueError, match="mode"):
        lake.write_table("prices_daily", prices_frame(), mode="merge")  # type: ignore[arg-type]


def test_read_filters_and_columns(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=_DAYS))
    out = lake.read_table(
        "prices_daily",
        filters=[("symbol", "in", ["AAPL"]), ("date", ">=", date(2024, 1, 3))],
        columns=["date", "close"],
    )
    assert list(out.columns) == ["date", "close"]
    assert list(out["date"]) == [date(2024, 1, 3), date(2024, 1, 4)]
    assert list(out["close"]) == [101.0, 102.0]

    cutoff = datetime(2024, 1, 3, 21, 15, tzinfo=UTC)
    known = lake.read_table("prices_daily", filters=[("available_at", "<", cutoff)])
    assert set(known["date"]) == {date(2024, 1, 2)}
    assert len(lake.read_table("prices_daily", filters=[("symbol", "not in", ["AAPL"])])) == 3
    assert len(lake.read_table("prices_daily", filters=[("symbol", "!=", "AAPL")])) == 3
    assert len(lake.read_table("prices_daily", filters=[("close", "==", 100.0)])) == 1

    with pytest.raises(ValueError, match="nope"):
        lake.read_table("prices_daily", filters=[("nope", "=", 1)])
    with pytest.raises(ValueError, match="like"):
        lake.read_table("prices_daily", filters=[("symbol", "like", "A%")])
    with pytest.raises(ValueError, match="nope"):
        lake.read_table("prices_daily", columns=["nope"])


def test_read_unwritten_registered_table_is_empty(lake: Lake, prices_schema: TableSchema) -> None:
    assert not lake.has_table("prices_daily")
    out = lake.read_table("prices_daily")
    assert out.empty
    assert list(out.columns) == list(prices_schema.column_names)
    assert str(out["available_at"].dtype) == "datetime64[us, UTC]"
    with pytest.raises(DataError, match="nope"):
        lake.read_table("nope")
    with pytest.raises(DataError, match="nope"):
        lake.table_schema("nope")
    assert lake.table_schema("prices_daily") is prices_schema


def test_partition_change_requires_overwrite(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=_DAYS[:1]))
    with pytest.raises(ValueError, match="partition"):
        lake.write_table("prices_daily", prices_frame(days=_DAYS[1:2]), partition_cols=[])
    lake.write_table("prices_daily", prices_frame(), partition_cols=[], mode="overwrite")
    assert _relative_files(lake, "prices_daily") == ["data.parquet"]
    meta = json.loads((lake.curated_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["tables"]["prices_daily"]["partition_by"] == []
    # later writes keep the stored partitioning
    lake.write_table("prices_daily", prices_frame(days=_DAYS[2:]))
    assert _relative_files(lake, "prices_daily") == ["data.parquet"]
    with pytest.raises(ValueError):
        lake.write_table("prices_daily", prices_frame(), partition_cols=["close"], mode="overwrite")


def test_read_output_works_with_pit_filter(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=_DAYS))
    as_of = AsOf.for_session(date(2024, 1, 4))  # cutoff 08:00 New York on 2024-01-04
    visible = pit_filter(lake.read_table("prices_daily"), as_of)
    assert sorted(set(visible["date"])) == [date(2024, 1, 2), date(2024, 1, 3)]


def test_write_logs_and_bumps_generation(
    lake: Lake, prices_frame: PricesFrame, tmp_path: Path
) -> None:
    log_file = tmp_path / "atlas.log"
    configure_logging(log_file=log_file, console=io.StringIO())
    try:
        start = lake.generation
        with run_context() as ctx:
            lake.write_table("prices_daily", prices_frame(days=_DAYS[:1]))
            lake.write_table("prices_daily", prices_frame(days=_DAYS[1:2]))
    finally:
        reset_logging()
    assert lake.generation == start + 2
    lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    storage = [line for line in lines if line["logger"].startswith("atlas.core.storage")]
    assert [(line["event"], line["level"]) for line in storage] == [("lake.write", "info")] * 2
    for line in storage:
        assert line["run_id"] == ctx.run_id
        assert line["table"] == "prices_daily"
        assert line["write_mode"] == "append"
        assert line["rows"] == len(prices_frame(days=_DAYS[:1]))


def test_failed_validation_writes_nothing(lake: Lake, prices_frame: PricesFrame) -> None:
    with pytest.raises(DataError):
        lake.write_table("prices_daily", prices_frame().drop(columns="available_at"))
    assert not lake.has_table("prices_daily")
    assert lake.table_files("prices_daily") == ()


def test_failed_overwrite_keeps_old_table(
    lake: Lake, prices_frame: PricesFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    lake.write_table("prices_daily", prices_frame())
    before = lake.read_table("prices_daily")
    real_replace = os.replace
    calls = []

    def flaky_replace(src: object, dst: object) -> None:
        calls.append((src, dst))
        # calls: 1 = partition file into staging, 2 = old table → retired, 3 = staging → table
        if len(calls) == 3:
            raise OSError("disk full")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr("atlas.core.storage.lake.os.replace", flaky_replace)
    with pytest.raises(OSError, match="disk full"):
        lake.write_table("prices_daily", prices_frame(symbols=("NVDA",)), mode="overwrite")
    monkeypatch.undo()
    assert Path(str(calls[2][1])) == lake.table_path("prices_daily")
    pd.testing.assert_frame_equal(lake.read_table("prices_daily"), before)
    assert sorted(p.name for p in lake.curated_dir.iterdir()) == ["_meta.json", "prices_daily"]


def test_partition_dir_names_for_each_type(lake: Lake) -> None:
    schema = TableSchema(
        name="typed_parts",
        columns=(
            Column(name="day", dtype=ColumnType.DATE),
            Column(name="bucket", dtype=ColumnType.INT64),
            Column(name="flag", dtype=ColumnType.BOOL),
            Column(name="available_at", dtype=ColumnType.TIMESTAMP),
        ),
        primary_key=("day", "bucket", "flag"),
        partition_by=("day", "bucket", "flag"),
    )
    lake.registry.register(schema)
    frame = pd.DataFrame(
        {
            "day": [date(2024, 1, 2), date(2024, 1, 3)],
            "bucket": pd.Series([7, 8], dtype="int64"),
            "flag": [True, False],
            "available_at": pd.to_datetime(["2024-01-02", "2024-01-03"]).tz_localize("UTC"),
        }
    )
    lake.write_table("typed_parts", frame)
    assert _relative_files(lake, "typed_parts") == [
        "day=2024-01-02/bucket=7/flag=true/data.parquet",
        "day=2024-01-03/bucket=8/flag=false/data.parquet",
    ]
    assert lake.read_table("typed_parts")["flag"].tolist() == [True, False]
