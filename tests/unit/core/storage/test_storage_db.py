"""Tests for the DuckDB `Database`: views over the lake and parameterised queries (task 4)."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import pytest
from storage_helpers import PricesFrame, make_prices_schema

from atlas.core.pit import AsOf, pit_filter
from atlas.core.storage import Database, Lake, TableSchema, get_database
from atlas.core.storage.db import DB_FILENAME


@pytest.fixture
def db(lake: Lake) -> Iterator[Database]:
    database = Database(lake)
    yield database
    database.close()


def test_database_file_lives_in_the_lake(lake: Lake, db: Database) -> None:
    assert db.path == lake.root / DB_FILENAME
    assert db.path.is_file()
    assert db.lake is lake


def test_query_over_lake_view(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    out = db.query("select * from prices_daily where symbol = ? order by date", ["AAPL"])
    assert list(out["symbol"]) == ["AAPL", "AAPL"]
    assert list(out["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert list(out["close"]) == [100.0, 101.0]


def test_named_params(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    out = db.query(
        "select count(*) as n from prices_daily where symbol = $sym and close > $min",
        {"sym": "MSFT", "min": 102.5},
    )
    assert out["n"].tolist() == [1]


def test_query_params_are_not_sql(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    out = db.query("select * from prices_daily where symbol = ?", ["AAPL' OR '1'='1"])
    assert out.empty
    assert len(db.query("select * from prices_daily")) == 4


def test_query_rejects_multiple_statements(db: Database) -> None:
    with pytest.raises(ValueError, match="one"):
        db.query("select 1; drop table audit_events")
    assert db.query("select count(*) as n from audit_events")["n"].tolist() == [0]
    with pytest.raises(ValueError, match="one"):
        db.query("")


@pytest.mark.parametrize(
    "sql",
    [
        "insert into audit_events (event_id) values ('x')",
        "delete from audit_events",
        "update audit_events set actor = 'x'",
        "drop table audit_events",
        "create table t (a int)",
        "copy audit_events to 'out.csv'",
        "attach 'other.duckdb' as other",
        "pragma version",
        "set threads = 1",
    ],
)
def test_query_rejects_writes(db: Database, sql: str) -> None:
    with pytest.raises(ValueError, match="SELECT"):
        db.query(sql)
    assert db.query("select count(*) as n from audit_events")["n"].tolist() == [0]


def test_views_see_new_writes(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(symbols=("AAPL",)))
    assert len(db.query("select * from prices_daily")) == 2
    lake.write_table("prices_daily", prices_frame(symbols=("NVDA",)))  # new partition
    lake.write_table(
        "prices_daily",
        prices_frame(symbols=("AAPL",), days=(date(2024, 1, 2),), close=9.0),
        mode="upsert",
    )
    out = db.query("select symbol, close from prices_daily order by symbol, date")
    assert out["symbol"].tolist() == ["AAPL", "AAPL", "NVDA", "NVDA"]
    assert out["close"].tolist()[0] == 9.0


def test_refresh_sees_writes_from_another_lake_object(
    lake: Lake, db: Database, prices_frame: PricesFrame
) -> None:
    other = Lake(lake.root, registry=lake.registry)  # e.g. another process
    other.write_table("prices_daily", prices_frame())
    db.refresh()
    assert len(db.query("select * from prices_daily")) == 4


def test_unwritten_table_view_is_empty_and_typed(db: Database, prices_schema: TableSchema) -> None:
    out = db.query("select * from prices_daily")
    assert out.empty
    assert list(out.columns) == list(prices_schema.column_names)
    types = db.query(
        "select column_name, data_type from information_schema.columns "
        "where table_name = 'prices_daily' order by ordinal_position"
    )
    assert types["data_type"].tolist() == [
        "VARCHAR",
        "DATE",
        "DOUBLE",
        "BIGINT",
        "TIMESTAMP WITH TIME ZONE",
    ]


def test_query_timestamps_are_utc(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame(days=(date(2024, 1, 2), date(2024, 1, 3))))
    out = db.query("select * from prices_daily")
    assert str(out["available_at"].dtype) == "datetime64[us, UTC]"
    assert out["available_at"].min().isoformat() == "2024-01-02T21:15:00+00:00"
    visible = pit_filter(out, AsOf.for_session(date(2024, 1, 3)))
    assert set(visible["date"]) == {date(2024, 1, 2)}


def test_views_are_not_persisted(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    with Database(lake) as database:
        assert len(database.query("select * from prices_daily")) == 4
        path = database.path
    con = duckdb.connect(str(path))
    try:
        views = con.execute("select view_name from duckdb_views() where not internal").fetchall()
        tables = con.execute("select table_name from duckdb_tables()").fetchall()
    finally:
        con.close()
    assert views == []
    assert tables == [("audit_events",)]


def test_get_database_is_one_per_process(lake: Lake) -> None:
    first = get_database(lake)
    try:
        assert get_database(lake) is first
        assert get_database(Lake(lake.root, registry=lake.registry)) is first
    finally:
        first.close()
    second = get_database(lake)
    try:
        assert second is not first
    finally:
        second.close()


def test_closed_database_refuses_queries(lake: Lake) -> None:
    database = Database(lake)
    database.close()
    database.close()  # idempotent
    with pytest.raises(ValueError, match="closed"):
        database.query("select 1")


def test_read_only_database(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    Database(lake).close()  # creates the file and audit_events
    with Database(lake, read_only=True) as reader:
        assert len(reader.query("select * from prices_daily")) == 4
        assert reader.query("select count(*) as n from audit_events")["n"].tolist() == [0]


def test_unpartitioned_and_year_partitioned_views(lake: Lake, prices_frame: PricesFrame) -> None:
    lake.registry.register(make_prices_schema(name="flat", partition_by=()))
    lake.registry.register(
        make_prices_schema(name="by_year", year_column="date", partition_by=("year",))
    )
    lake.write_table("flat", prices_frame())
    lake.write_table("by_year", prices_frame(days=(date(2023, 12, 29), date(2024, 1, 2))))
    with Database(lake) as database:
        assert database.query("select count(*) as n from flat")["n"].tolist() == [4]
        by_year = database.query("select * from by_year order by symbol, date")
        assert "year" not in by_year.columns  # no hive column leaks into the view
        assert len(by_year) == 4


def test_views_follow_registry_changes(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.registry.register(make_prices_schema(name="late"))  # registered after the db opened
    lake.write_table("late", prices_frame())
    assert db.query("select count(*) as n from late")["n"].tolist() == [4]
    lake.registry.register(make_prices_schema(name="later"))  # no write: generation unchanged
    assert db.query("select count(*) as n from later")["n"].tolist() == [0]


# --- Phase 0 checkpoint, Tier 3: no file access outside the lake ------------------------------


def _outside_files(tmp_path: Path) -> dict[str, Path]:
    """A CSV and a Parquet file in a sibling of the lake whose name starts like the lake's."""
    outside = tmp_path / "lake2"
    outside.mkdir()
    csv = outside / "secret.csv"
    csv.write_text("a\n1\n")
    parquet = outside / "secret.parquet"
    duckdb.sql(f"COPY (SELECT 1 AS a) TO '{parquet}'")
    return {"csv": csv, "parquet": parquet}


@pytest.mark.parametrize(
    "sql",
    [
        "select * from read_csv(?)",
        "select * from read_text(?)",
        "select * from read_parquet(?)",
        "select * from read_blob(?)",
    ],
)
@pytest.mark.parametrize("read_only", [False, True], ids=["read-write", "read-only"])
def test_query_cannot_read_files_outside_the_lake(
    lake: Lake, tmp_path: Path, sql: str, read_only: bool
) -> None:
    files = _outside_files(tmp_path)
    target = files["parquet"] if "parquet" in sql else files["csv"]
    Database(lake).close()
    with Database(lake, read_only=read_only) as db, pytest.raises(duckdb.PermissionException):
        db.query(sql, [str(target)])
    with Database(lake, read_only=read_only) as db, pytest.raises(duckdb.PermissionException):
        db.query(sql, [str(lake.root / ".." / "lake2" / target.name)])


def test_query_still_reads_lake_files(lake: Lake, db: Database, prices_frame: PricesFrame) -> None:
    lake.write_table("prices_daily", prices_frame())
    glob = str(lake.table_path("prices_daily") / "**" / "*.parquet")
    assert len(db.query("select * from read_parquet(?)", [glob])) == 4
    assert len(db.query("select * from prices_daily")) == 4


def test_file_access_setting_cannot_be_reopened(db: Database) -> None:
    con = db._connection()
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")
    settings = db.query("select value from duckdb_settings() where name = 'enable_external_access'")
    assert settings["value"].tolist() == ["false"]
