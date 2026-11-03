"""Stage 11 Task 1: the list-of-strings column type (`news.symbols[]`, Stage 06 Backlog D1)."""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from atlas.core.errors import DataError
from atlas.core.storage import (
    Column,
    ColumnType,
    Database,
    Lake,
    SchemaRegistry,
    TableSchema,
    validate_frame,
)


def _schema(nullable: bool = False) -> TableSchema:
    return TableSchema(
        name="tagged",
        columns=(
            Column(name="id", dtype=ColumnType.STRING),
            Column(name="tags", dtype=ColumnType.STRING_LIST, nullable=nullable),
            Column(name="available_at", dtype=ColumnType.TIMESTAMP),
        ),
        primary_key=("id",),
    )


def _frame(tags: list[object]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": pd.Series([f"r{i}" for i in range(len(tags))], dtype="str"),
            "tags": pd.Series(tags, dtype="object"),
            "available_at": pd.Series(
                [datetime(2020, 3, 2, 15, i, tzinfo=UTC) for i in range(len(tags))]
            ),
        }
    )


def _lake(tmp_path: Path, schema: TableSchema) -> Lake:
    return Lake(tmp_path / "lake", registry=SchemaRegistry([schema]))


def test_string_list_roundtrip(tmp_path: Path) -> None:
    schema = _schema()
    lake = _lake(tmp_path, schema)
    tags = [["AAPL", "MSFT"], [], ("SPY",), np.array(["QQQ"], dtype=object)]
    lake.write_table("tagged", _frame(tags), mode="upsert")
    out = lake.read_table("tagged")
    assert list(out["tags"]) == [("AAPL", "MSFT"), (), ("SPY",), ("QQQ",)]
    assert schema.arrow_schema().field("tags").type == pa.list_(pa.string())


@pytest.mark.parametrize("bad", [[1], "AAPL", [None], [["A"]], 3.0])
def test_string_list_rejects_non_strings(bad: object) -> None:
    with pytest.raises(DataError, match=r"tagged\.tags"):
        validate_frame(_schema(), _frame([["AAPL"], bad]))


def test_string_list_null_cell_only_if_nullable(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="null"):
        validate_frame(_schema(), _frame([["AAPL"], None]))
    lake = _lake(tmp_path, _schema(nullable=True))
    lake.write_table("tagged", _frame([["AAPL"], None]), mode="upsert")
    assert list(lake.read_table("tagged")["tags"]) == [("AAPL",), None]


@pytest.mark.parametrize("missing", [pd.NA, np.nan, None], ids=["pd.NA", "nan", "None"])
def test_string_list_missing_markers_are_null(tmp_path: Path, missing: object) -> None:
    # Phase 1 checkpoint, Tier 2 (Stage 11 Backlog): pd.NA was rejected as "not a list".
    with pytest.raises(DataError, match="null"):
        validate_frame(_schema(), _frame([["AAPL"], missing]))
    lake = _lake(tmp_path, _schema(nullable=True))
    lake.write_table("tagged", _frame([["AAPL"], missing]), mode="upsert")
    assert list(lake.read_table("tagged")["tags"]) == [("AAPL",), None]


def test_string_list_not_in_key_or_partition() -> None:
    columns = (
        Column(name="id", dtype=ColumnType.STRING),
        Column(name="tags", dtype=ColumnType.STRING_LIST),
        Column(name="available_at", dtype=ColumnType.TIMESTAMP),
    )
    with pytest.raises(ValueError, match="list"):
        TableSchema(name="tagged", columns=columns, primary_key=("id", "tags"))
    with pytest.raises(ValueError, match="partition"):
        TableSchema(name="tagged", columns=columns, primary_key=("id",), partition_by=("tags",))


def test_string_list_filter_rejected(tmp_path: Path) -> None:
    lake = _lake(tmp_path, _schema())
    lake.write_table("tagged", _frame([["AAPL"]]), mode="upsert")
    with pytest.raises(ValueError, match="list"):
        lake.read_table("tagged", filters=[("tags", "=", "AAPL")])


def test_string_list_empty_table_reads(tmp_path: Path) -> None:
    out = _lake(tmp_path, _schema()).read_table("tagged")
    assert out.empty
    assert list(out.columns) == ["id", "tags", "available_at"]


def test_string_list_database_view(tmp_path: Path) -> None:
    lake = _lake(tmp_path, _schema())
    lake.write_table("tagged", _frame([["AAPL", "MSFT"], []]), mode="upsert")
    with Database(lake) as db:
        out = db.query("SELECT id, len(tags) AS n FROM tagged ORDER BY id")
    assert list(out["n"]) == [2, 0]
