"""Tests for `curated/_meta.json` (Stage 06, tasks 2 and 5)."""

import json
from pathlib import Path

import pandas as pd
import pytest
from storage_helpers import PricesFrame, make_prices_schema

from atlas.core.errors import DataError
from atlas.core.storage import Column, ColumnType, Lake, SchemaRegistry, TableSchema
from atlas.core.storage.meta import MetaStore, TableMeta

_META = TableMeta(version=1, partition_by=("symbol",), fingerprint="ab" * 32)


def test_meta_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "_meta.json"
    MetaStore(path).put("prices_daily", _META)
    MetaStore(path).put("other", TableMeta(version=3, partition_by=(), fingerprint="cd" * 32))
    reloaded = MetaStore(path)
    assert reloaded.get("prices_daily") == _META
    assert set(reloaded.tables()) == {"prices_daily", "other"}
    assert reloaded.get("nope") is None


def test_meta_missing_file_is_empty(tmp_path: Path) -> None:
    store = MetaStore(tmp_path / "_meta.json")
    assert store.tables() == {}
    assert store.get("prices_daily") is None


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps({"format": 99, "tables": {}}),
        json.dumps({"format": 1, "tables": {"t": {"version": "one"}}}),
        json.dumps([1, 2]),
    ],
)
def test_meta_corrupt_file_raises(tmp_path: Path, text: str) -> None:
    path = tmp_path / "_meta.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DataError, match=r"_meta\.json"):
        MetaStore(path).tables()


def test_meta_write_is_atomic_json(tmp_path: Path) -> None:
    path = tmp_path / "_meta.json"
    MetaStore(path).put("prices_daily", _META)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body == {
        "format": 1,
        "tables": {
            "prices_daily": {"version": 1, "partition_by": ["symbol"], "fingerprint": "ab" * 32}
        },
    }
    assert [p.name for p in tmp_path.iterdir()] == ["_meta.json"]  # no temp files left


# --- schema versions and migrations (task 5) ----------------------------------------------


def _write_v1(root: Path, frame: pd.DataFrame) -> Lake:
    lake = Lake(root, registry=SchemaRegistry([make_prices_schema()]))
    lake.write_table("prices_daily", frame)
    return lake


def _v3_schema() -> TableSchema:
    base = make_prices_schema().columns
    return make_prices_schema(
        version=3,
        columns=(
            *base,
            Column(name="vendor", dtype=ColumnType.STRING),
            Column(name="adj_close", dtype=ColumnType.FLOAT64),
        ),
    )


def _add_vendor(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(vendor="yfinance")


def _add_adj_close(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(adj_close=df["close"] * 0.5)


def test_first_write_records_version(
    lake: Lake, prices_schema: TableSchema, prices_frame: PricesFrame
) -> None:
    lake.write_table("prices_daily", prices_frame())
    body = json.loads((lake.curated_dir / "_meta.json").read_text(encoding="utf-8"))
    assert body["tables"]["prices_daily"] == {
        "version": 1,
        "partition_by": ["symbol"],
        "fingerprint": prices_schema.fingerprint(),
    }


def test_version_mismatch_blocks_read_and_write(tmp_path: Path, prices_frame: PricesFrame) -> None:
    _write_v1(tmp_path / "lake", prices_frame())
    newer = Lake(tmp_path / "lake", registry=SchemaRegistry([_v3_schema()]))
    with pytest.raises(DataError, match="migrate"):
        newer.read_table("prices_daily")
    batch = prices_frame().assign(vendor="x", adj_close=1.0)
    with pytest.raises(DataError, match="migrate"):
        newer.write_table("prices_daily", batch, mode="upsert")


def test_migrate_applies_chain(tmp_path: Path, prices_frame: PricesFrame) -> None:
    old = _write_v1(tmp_path / "lake", prices_frame())
    before = old.read_table("prices_daily")
    registry = SchemaRegistry([_v3_schema()])
    registry.add_migration("prices_daily", 1, _add_vendor)
    registry.add_migration("prices_daily", 2, _add_adj_close)
    lake = Lake(tmp_path / "lake", registry=registry)

    assert lake.migrate("prices_daily") == 3
    out = lake.read_table("prices_daily")
    assert list(out.columns) == [*before.columns, "vendor", "adj_close"]
    pd.testing.assert_frame_equal(out[list(before.columns)], before)
    assert set(out["vendor"]) == {"yfinance"}
    assert (out["adj_close"] == out["close"] * 0.5).all()
    meta = MetaStore(lake.curated_dir / "_meta.json").get("prices_daily")
    assert meta == TableMeta(
        version=3, partition_by=("symbol",), fingerprint=_v3_schema().fingerprint()
    )
    assert sorted(p.parent.name for p in lake.table_files("prices_daily")) == [
        "symbol=AAPL",
        "symbol=MSFT",
    ]

    stats = [p.stat().st_mtime_ns for p in lake.table_files("prices_daily")]
    assert lake.migrate("prices_daily") == 3  # already current: nothing rewritten
    assert [p.stat().st_mtime_ns for p in lake.table_files("prices_daily")] == stats


def test_migrate_missing_step_fails_cleanly(tmp_path: Path, prices_frame: PricesFrame) -> None:
    old = _write_v1(tmp_path / "lake", prices_frame())
    before = old.read_table("prices_daily")
    registry = SchemaRegistry([_v3_schema()])
    registry.add_migration("prices_daily", 1, _add_vendor)  # no step from version 2
    lake = Lake(tmp_path / "lake", registry=registry)
    with pytest.raises(DataError, match="version 2"):
        lake.migrate("prices_daily")
    assert MetaStore(lake.curated_dir / "_meta.json").get("prices_daily").version == 1  # type: ignore[union-attr]
    pd.testing.assert_frame_equal(old.read_table("prices_daily"), before)


def test_migrate_step_producing_bad_rows_fails_cleanly(
    tmp_path: Path, prices_frame: PricesFrame
) -> None:
    old = _write_v1(tmp_path / "lake", prices_frame())
    before = old.read_table("prices_daily")
    registry = SchemaRegistry([_v3_schema()])
    registry.add_migration("prices_daily", 1, _add_vendor)
    registry.add_migration("prices_daily", 2, lambda df: df)  # forgets adj_close
    lake = Lake(tmp_path / "lake", registry=registry)
    with pytest.raises(DataError, match="adj_close"):
        lake.migrate("prices_daily")
    pd.testing.assert_frame_equal(old.read_table("prices_daily"), before)


def test_migrate_unwritten_table_is_a_noop(lake: Lake) -> None:
    assert lake.migrate("prices_daily") == 1
    assert not lake.has_table("prices_daily")


def test_registered_version_older_than_data_fails(
    tmp_path: Path, prices_frame: PricesFrame
) -> None:
    v3 = Lake(tmp_path / "lake", registry=SchemaRegistry([_v3_schema()]))
    v3.write_table("prices_daily", prices_frame().assign(vendor="x", adj_close=1.0))
    older = Lake(tmp_path / "lake", registry=SchemaRegistry([make_prices_schema()]))
    with pytest.raises(DataError, match="newer"):
        older.read_table("prices_daily")
    with pytest.raises(DataError, match="newer"):
        older.migrate("prices_daily")


def test_schema_change_without_version_bump_fails(
    tmp_path: Path, prices_frame: PricesFrame
) -> None:
    _write_v1(tmp_path / "lake", prices_frame())
    edited = make_prices_schema(
        columns=(*make_prices_schema().columns, Column(name="vendor", dtype=ColumnType.STRING))
    )
    lake = Lake(tmp_path / "lake", registry=SchemaRegistry([edited]))
    with pytest.raises(DataError, match="fingerprint"):
        lake.read_table("prices_daily")
    with pytest.raises(DataError, match="fingerprint"):
        lake.write_table("prices_daily", prices_frame().assign(vendor="x"), mode="upsert")
