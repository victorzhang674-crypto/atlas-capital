"""Tests for the schema registry and write validation (Stage 06, task 1)."""

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from storage_helpers import PricesFrame, make_prices_schema

from atlas.core.errors import DataError
from atlas.core.storage import (
    DEFAULT_REGISTRY,
    Column,
    ColumnType,
    SchemaRegistry,
    TableSchema,
    validate_frame,
)

_SYMBOL = Column(name="symbol", dtype=ColumnType.STRING)
_DATE = Column(name="date", dtype=ColumnType.DATE)
_CLOSE = Column(name="close", dtype=ColumnType.FLOAT64)
_AVAIL = Column(name="available_at", dtype=ColumnType.TIMESTAMP)

# --- declarations -----------------------------------------------------------------------------


def test_schema_requires_available_at() -> None:
    with pytest.raises(ValueError, match="available_at"):
        TableSchema(name="t", columns=(_SYMBOL, _CLOSE), primary_key=("symbol",))
    with pytest.raises(ValueError, match="available_at"):
        TableSchema(
            name="t",
            columns=(_SYMBOL, Column(name="available_at", dtype=ColumnType.DATE)),
            primary_key=("symbol",),
        )
    with pytest.raises(ValueError, match="available_at"):
        TableSchema(
            name="t",
            columns=(
                _SYMBOL,
                Column(name="available_at", dtype=ColumnType.TIMESTAMP, nullable=True),
            ),
            primary_key=("symbol",),
        )


def test_static_schema_may_omit_available_at() -> None:
    schema = TableSchema(name="sectors", columns=(_SYMBOL,), primary_key=("symbol",), static=True)
    assert schema.column_names == ("symbol",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"columns": (_SYMBOL, _SYMBOL, _DATE, _AVAIL)},  # duplicate column
        {"primary_key": ()},  # empty primary key
        {"primary_key": ("symbol", "nope")},  # undeclared key column
        {
            "columns": (
                Column(name="symbol", dtype=ColumnType.STRING, nullable=True),
                _DATE,
                _AVAIL,
            ),
            "primary_key": ("symbol", "date"),
        },  # nullable key column
        {"partition_by": ("close",)},  # partition column outside the key
        {"partition_by": ("year",)},  # year partition without year_column
        {
            "columns": (_SYMBOL, _DATE, _CLOSE, _AVAIL),
            "primary_key": ("symbol", "close"),
            "partition_by": ("close",),
        },  # float partition column
        {"year_column": "symbol", "partition_by": ("year",)},  # year from a string column
        {"name": "Bad-Name"},
        {"name": "audit_events"},  # reserved for the DuckDB audit mirror
        {"name": "llm_usage"},  # reserved for the native LLM usage table (Stage 24)
        {"name": "llm_cache"},  # reserved for the native LLM cache table (Stage 25)
        {"version": 0},
    ],
)
def test_schema_rejects_bad_declarations(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        make_prices_schema(**overrides)


def test_year_partition_declaration() -> None:
    schema = make_prices_schema(year_column="date", partition_by=("year",))
    assert schema.partition_by == ("year",)
    assert schema.check_partitioning(["symbol"]) == ("symbol",)
    with pytest.raises(ValueError):
        schema.check_partitioning(["volume"])


def test_arrow_schema_types(prices_schema: TableSchema) -> None:
    arrow = prices_schema.arrow_schema()
    assert [str(f.type) for f in arrow] == [
        "string",
        "date32[day]",
        "double",
        "int64",
        "timestamp[us, tz=UTC]",
    ]
    assert not arrow.field("symbol").nullable


def test_fingerprint_tracks_columns(prices_schema: TableSchema) -> None:
    same = make_prices_schema(description="other text")
    changed = make_prices_schema(
        columns=(*prices_schema.columns, Column(name="vendor", dtype=ColumnType.STRING))
    )
    assert prices_schema.fingerprint() == same.fingerprint()
    assert prices_schema.fingerprint() != changed.fingerprint()


def test_registry_register_get_copy(prices_schema: TableSchema) -> None:
    registry = SchemaRegistry()
    assert registry.register(prices_schema) is prices_schema
    registry.register(make_prices_schema())  # equal schema again: no-op
    assert "prices_daily" in registry
    assert registry.names() == ("prices_daily",)
    with pytest.raises(ValueError, match="prices_daily"):
        registry.register(make_prices_schema(partition_by=()))
    with pytest.raises(DataError, match="nope"):
        registry.get("nope")
    copy = registry.copy()
    copy.register(
        TableSchema(name="other", columns=(_SYMBOL,), primary_key=("symbol",), static=True)
    )
    assert "other" not in registry
    assert copy.get("prices_daily") is prices_schema


def test_registry_migrations() -> None:
    registry = SchemaRegistry()
    registry.register(make_prices_schema(version=2))

    def step(df: pd.DataFrame) -> pd.DataFrame:
        return df

    registry.add_migration("prices_daily", 1, step)
    assert registry.migration("prices_daily", 1) is step
    assert registry.migration("prices_daily", 2) is None
    with pytest.raises(ValueError):
        registry.add_migration("prices_daily", 1, step)  # already registered
    with pytest.raises(ValueError):
        registry.add_migration("prices_daily", 2, step)  # must be below the current version
    with pytest.raises(DataError):
        registry.add_migration("nope", 1, step)


def test_default_registry_is_a_registry() -> None:
    assert isinstance(DEFAULT_REGISTRY, SchemaRegistry)


# --- write validation -------------------------------------------------------------------------


def test_validate_frame_returns_arrow_in_schema_order(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    frame = prices_frame()[["volume", "available_at", "symbol", "close", "date"]]
    table = validate_frame(prices_schema, frame)
    assert table.schema.equals(prices_schema.arrow_schema())
    assert table.num_rows == 4


def test_validate_frame_missing_available_at_fails(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    with pytest.raises(DataError, match="available_at"):
        validate_frame(prices_schema, prices_frame().drop(columns="available_at"))


def test_validate_frame_extra_or_missing_columns_fail(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    with pytest.raises(DataError, match="vendor"):
        validate_frame(prices_schema, prices_frame().assign(vendor="x"))
    with pytest.raises(DataError, match="volume"):
        validate_frame(prices_schema, prices_frame().drop(columns="volume"))


def test_null_available_at_rejected(prices_schema: TableSchema, prices_frame: PricesFrame) -> None:
    frame = prices_frame()
    frame.loc[0, "available_at"] = pd.NaT
    with pytest.raises(DataError, match="available_at"):
        validate_frame(prices_schema, frame)


def test_non_nullable_nan_rejected(prices_schema: TableSchema, prices_frame: PricesFrame) -> None:
    frame = prices_frame()
    frame.loc[1, "close"] = np.nan
    with pytest.raises(DataError, match="close"):
        validate_frame(prices_schema, frame)


@pytest.mark.parametrize("value", [np.inf, -np.inf])
def test_infinite_float_rejected(
    prices_schema: TableSchema, prices_frame: PricesFrame, value: float
) -> None:
    frame = prices_frame()
    frame.loc[0, "close"] = value
    with pytest.raises(DataError, match="close"):
        validate_frame(prices_schema, frame)


@pytest.mark.parametrize(
    ("column", "values"),
    [
        ("volume", ["1", "2", "3", "4"]),  # ints as strings
        ("volume", [1.0, 2.0, 3.0, 4.0]),  # floats in an int column
        ("date", ["2024-01-02"] * 4),  # string dates
        ("close", [True, False, True, False]),  # bools in a float column
        ("symbol", [1, 2, 3, 4]),  # ints in a string column
        ("volume", [True, False, True, False]),  # bools in an int column
    ],
)
def test_wrong_dtypes_rejected(
    prices_schema: TableSchema, prices_frame: PricesFrame, column: str, values: list[object]
) -> None:
    frame = prices_frame()
    frame[column] = values
    with pytest.raises(DataError, match=column):
        validate_frame(prices_schema, frame)


def test_timestamp_column_normalised_to_utc(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    table = validate_frame(prices_schema, prices_frame())
    first = table.column("available_at")[0].as_py()
    # 2024-01-02 16:15 New York (EST, UTC-5) is 21:15 UTC
    assert first == datetime(2024, 1, 2, 21, 15, tzinfo=UTC)


def test_naive_timestamp_rejected(prices_schema: TableSchema, prices_frame: PricesFrame) -> None:
    frame = prices_frame()
    frame["available_at"] = frame["available_at"].dt.tz_localize(None)
    with pytest.raises(DataError, match="available_at"):
        validate_frame(prices_schema, frame)


def test_object_timestamps_rejected(prices_schema: TableSchema, prices_frame: PricesFrame) -> None:
    frame = prices_frame()
    frame["available_at"] = frame["available_at"].astype(object)
    with pytest.raises(DataError, match="available_at"):
        validate_frame(prices_schema, frame)


def test_sub_microsecond_timestamp_rejected(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    frame = prices_frame()
    frame["available_at"] = frame["available_at"].dt.as_unit("ns") + pd.Timedelta(1, unit="ns")
    with pytest.raises(DataError, match="available_at"):
        validate_frame(prices_schema, frame)


def test_date_column_accepts_dates_and_midnight_datetime64(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    frame = prices_frame()
    as_dt64 = frame.assign(date=pd.to_datetime(frame["date"].map(date.isoformat)))
    from_objects = validate_frame(prices_schema, frame).column("date")
    from_dt64 = validate_frame(prices_schema, as_dt64).column("date")
    assert from_objects.equals(from_dt64)
    assert from_objects[0].as_py() == date(2024, 1, 2)

    with pytest.raises(DataError, match="date"):
        validate_frame(
            prices_schema, frame.assign(date=pd.Series([datetime(2024, 1, 2)] * 4, dtype=object))
        )
    with pytest.raises(DataError, match="date"):
        validate_frame(prices_schema, as_dt64.assign(date=as_dt64["date"] + pd.Timedelta(hours=1)))
    with pytest.raises(DataError, match="date"):
        validate_frame(prices_schema, as_dt64.assign(date=as_dt64["date"].dt.tz_localize(UTC)))


def test_nullable_columns_accept_nulls() -> None:
    schema = TableSchema(
        name="t",
        columns=(
            _SYMBOL,
            Column(name="note", dtype=ColumnType.STRING, nullable=True),
            Column(name="score", dtype=ColumnType.FLOAT64, nullable=True),
            Column(name="n", dtype=ColumnType.INT64, nullable=True),
            Column(name="flag", dtype=ColumnType.BOOL, nullable=True),
            _AVAIL,
        ),
        primary_key=("symbol",),
    )
    frame = pd.DataFrame(
        {
            "symbol": ["A", "B"],
            "note": ["x", None],
            "score": [1.5, np.nan],
            "n": pd.Series([1, None], dtype="Int64"),
            "flag": pd.Series([True, None], dtype="boolean"),
            "available_at": pd.to_datetime(["2024-01-02", "2024-01-03"]).tz_localize(UTC),
        }
    )
    table = validate_frame(schema, frame)
    for name in ("note", "score", "n", "flag"):
        assert table.column(name).null_count == 1, name
    assert table.column("n").type == pa.int64()


def test_non_nullable_int_rejects_missing(
    prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    frame = prices_frame()
    frame["volume"] = pd.Series([1, None, 3, 4], dtype="Int64")
    with pytest.raises(DataError, match="volume"):
        validate_frame(prices_schema, frame)
