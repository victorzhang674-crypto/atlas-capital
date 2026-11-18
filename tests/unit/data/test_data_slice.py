"""Stage 29 Task 5: `slice_lake` keeps only the rows a point-in-time run can read for a few
symbols, and a `DataView` on the slice gives the same tool evidence as on the full lake."""

import json
import sys
import time as time_module
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from structlog.testing import capture_logs

_RESEARCH = Path(__file__).resolve().parents[1] / "agents" / "research"
if str(_RESEARCH) not in sys.path:
    sys.path.append(str(_RESEARCH))

from fundamental_world import AS_OF, CONFIG, FundamentalWorld, build_fundamental_world  # noqa: E402

from atlas.agents.tools import fundamental_tools as ft  # noqa: E402
from atlas.core.errors import DataError  # noqa: E402
from atlas.core.pit import AsOf  # noqa: E402
from atlas.core.storage import (  # noqa: E402
    DEFAULT_REGISTRY,
    Column,
    ColumnType,
    Lake,
    SchemaRegistry,
    TableSchema,
)
from atlas.data.slice import DERIVED_TABLES, MANIFEST, date_column, slice_lake, slice_tables  # noqa: E402
from atlas.data.view import DataView  # noqa: E402

POINT = AsOf.for_session(AS_OF)
IT = ("AAA", "BBB", "CCC", "DDD")


@pytest.fixture(scope="module")
def sworld(tmp_path_factory: pytest.TempPathFactory) -> FundamentalWorld:
    return build_fundamental_world(tmp_path_factory.mktemp("slice-world"))


def test_default_tables_are_the_source_tables_with_a_symbol(sworld: FundamentalWorld) -> None:
    names = slice_tables(sworld.dirty)
    assert names == (
        "corporate_actions",
        "fundamentals_facts",
        "index_membership",
        "instruments",
        "prices_daily",
    )
    assert not set(names) & DERIVED_TABLES


def test_named_tables_are_checked(sworld: FundamentalWorld) -> None:
    assert slice_tables(sworld.dirty, ["prices_daily"]) == ("prices_daily",)
    with pytest.raises(ValueError, match="unknown table"):
        slice_tables(sworld.dirty, ["nope"])
    with pytest.raises(ValueError, match="never been written"):
        slice_tables(sworld.dirty, ["news"])
    with pytest.raises(ValueError, match="no tables"):
        slice_tables(sworld.dirty, [" "])


def test_date_columns() -> None:
    assert date_column(DEFAULT_REGISTRY.get("prices_daily")) == "date"
    assert date_column(DEFAULT_REGISTRY.get("fundamentals_facts")) == "period_end"
    assert date_column(DEFAULT_REGISTRY.get("corporate_actions")) == "ex_date"
    assert date_column(DEFAULT_REGISTRY.get("instruments")) is None
    # Static tables are never trimmed: a membership that started before `since` may still hold.
    assert date_column(DEFAULT_REGISTRY.get("index_membership")) is None


def test_since_keeps_memberships_that_started_earlier(
    sworld: FundamentalWorld, tmp_path: Path
) -> None:
    slice_lake(sworld.clean, tmp_path / "s", IT, POINT, since=date(2023, 1, 1))
    members = Lake(tmp_path / "s", registry=sworld.clean.registry).read_table("index_membership")
    assert sorted(members["symbol"]) == list(IT)  # all started in 2010
    assert members["start_date"].min() == date(2010, 1, 4)


def test_a_slice_keeps_the_symbols_rows_known_at_the_as_of(
    sworld: FundamentalWorld, tmp_path: Path
) -> None:
    out = tmp_path / "slice"
    report = slice_lake(sworld.dirty, out, ["aaa", "CCC"], POINT)
    sliced = Lake(out, registry=sworld.dirty.registry)
    for name in report.rows:
        frame = sliced.read_table(name)
        assert set(frame["symbol"]) <= {"AAA", "CCC"}, name
        if "available_at" in frame.columns:
            assert (frame["available_at"] < POINT.cutoff).all(), name
    prices = sliced.read_table("prices_daily")
    assert prices["date"].max() == date(2024, 2, 29)  # later bars left out
    assert (
        len(sliced.read_table("corporate_actions")) == 1
    )  # CCC's split; AAA's late one is not known
    assert report.symbols == ("AAA", "CCC") and report.total == sum(report.rows.values())
    manifest = json.loads((out / MANIFEST).read_text())
    assert manifest["symbols"] == ["AAA", "CCC"] and manifest["as_of"] == "2024-03-01"
    assert manifest["tables"] == report.rows and manifest["since"] is None
    assert str(sworld.dirty.root) not in (out / MANIFEST).read_text()  # no source path


def test_since_trims_dated_tables_only(sworld: FundamentalWorld, tmp_path: Path) -> None:
    report = slice_lake(sworld.clean, tmp_path / "s", ["AAA"], POINT, since=date(2023, 1, 1))
    sliced = Lake(tmp_path / "s", registry=sworld.clean.registry)
    assert sliced.read_table("prices_daily")["date"].min() >= date(2023, 1, 1)
    assert sliced.read_table("fundamentals_facts")["period_end"].min() >= date(2023, 1, 1)
    assert len(sliced.read_table("instruments")) == 1
    assert report.since == date(2023, 1, 1)


def test_a_slice_needs_a_new_folder_and_symbols(sworld: FundamentalWorld, tmp_path: Path) -> None:
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(DataError, match="not empty"):
        slice_lake(sworld.clean, tmp_path / "full", ["AAA"], POINT)
    with pytest.raises(ValueError, match="no symbols"):
        slice_lake(sworld.clean, tmp_path / "a", [], POINT)
    with pytest.raises(ValueError, match="not one string"):
        slice_lake(sworld.clean, tmp_path / "b", "AAA", POINT)  # type: ignore[arg-type]


def test_the_source_is_not_written(sworld: FundamentalWorld, tmp_path: Path) -> None:
    before = sorted(
        (p.relative_to(sworld.clean.root), p.stat().st_mtime_ns)
        for p in sworld.clean.root.rglob("*")
        if p.is_file()
    )
    slice_lake(sworld.clean, tmp_path / "s", ["AAA"], POINT)
    after = sorted(
        (p.relative_to(sworld.clean.root), p.stat().st_mtime_ns)
        for p in sworld.clean.root.rglob("*")
        if p.is_file()
    )
    assert after == before


def test_the_tools_read_the_same_on_a_slice_of_the_sector(
    sworld: FundamentalWorld, tmp_path: Path
) -> None:
    """The slice of AAA's sector peers gives AAA's prefetch exactly as the full lake does."""
    slice_lake(sworld.dirty, tmp_path / "s", IT, POINT)
    sliced = DataView(Lake(tmp_path / "s", registry=sworld.dirty.registry), AS_OF, CONFIG)
    assert ft.prefetch(sliced, "AAA") == ft.prefetch(sworld.view("dirty"), "AAA")


def test_a_symbols_list_table_keeps_the_articles_of_the_symbols(tmp_path: Path) -> None:
    """`news` has a `symbols` list, no `symbol` column; `since` trims its timestamp key."""
    from view_helpers import build_world

    lake = Lake(tmp_path / "world", registry=DEFAULT_REGISTRY.copy())
    build_world(lake, future=True)
    report = slice_lake(
        lake, tmp_path / "s", ["AAA"], POINT, tables=["news"], since=date(2024, 2, 28)
    )
    news = Lake(tmp_path / "s", registry=lake.registry).read_table("news")
    assert list(news["article_id"]) == ["alpaca-2"]  # alpaca-1 before since; 10, 12 not yet known
    assert all("AAA" in symbols for symbols in news["symbols"])
    assert (news["published_at"] >= datetime(2024, 2, 28, tzinfo=news["published_at"].dt.tz)).all()
    assert report.rows == {"news": 1}


# Mutation-testing follow-up (audit A9): contracts the tests above left unpinned.

_UTC_MIDNIGHT = datetime(2024, 2, 28, tzinfo=UTC)


def _custom_lake(root: Path) -> Lake:
    """A lake on its own registry (none of these tables is in `DEFAULT_REGISTRY`): `ticks`
    (a `symbol` column), `articles` (a `symbols` list, timestamp key), `rates` (neither) and
    `mentions` (both: the `symbol` column decides)."""
    ts, day = ColumnType.TIMESTAMP, ColumnType.DATE
    known = Column(name="available_at", dtype=ts)
    schemas = [
        TableSchema(
            name="ticks",
            columns=(
                Column(name="symbol", dtype=ColumnType.STRING),
                Column(name="date", dtype=day),
                Column(name="value", dtype=ColumnType.FLOAT64),
                known,
            ),
            primary_key=("symbol", "date"),
        ),
        TableSchema(
            name="articles",
            columns=(
                Column(name="article_id", dtype=ColumnType.STRING),
                Column(name="published_at", dtype=ts),
                Column(name="symbols", dtype=ColumnType.STRING_LIST),
                known,
            ),
            primary_key=("published_at", "article_id"),
            year_column="published_at",  # what `since` trims on, as in `news`
        ),
        TableSchema(
            name="rates",
            columns=(
                Column(name="date", dtype=day),
                Column(name="value", dtype=ColumnType.FLOAT64),
                known,
            ),
            primary_key=("date",),
        ),
        TableSchema(
            name="mentions",
            columns=(
                Column(name="symbol", dtype=ColumnType.STRING),
                Column(name="date", dtype=day),
                Column(name="symbols", dtype=ColumnType.STRING_LIST),
                known,
            ),
            primary_key=("symbol", "date"),
        ),
    ]
    lake = Lake(root, registry=SchemaRegistry(schemas))
    seen = datetime(2024, 2, 29, 12, tzinfo=UTC)
    lake.write_table(
        "ticks",
        pd.DataFrame(
            {
                "symbol": ["AAA", "BBB"],
                "date": [date(2024, 2, 28)] * 2,
                "value": [1.0, 2.0],
                "available_at": [seen] * 2,
            }
        ),
    )
    lake.write_table(
        "articles",
        pd.DataFrame(
            {
                "article_id": ["midnight", "before", "other"],
                "published_at": [
                    _UTC_MIDNIGHT,
                    _UTC_MIDNIGHT - timedelta(microseconds=1),
                    _UTC_MIDNIGHT,
                ],
                "symbols": [["AAA", "ZZZ"], ["AAA"], ["BBB"]],
                "available_at": [seen] * 3,
            }
        ),
    )
    lake.write_table(
        "rates",
        pd.DataFrame(
            {
                "date": [date(2024, 2, 27), date(2024, 2, 28)],
                "value": [5.0, 5.25],
                "available_at": [seen] * 2,
            }
        ),
    )
    lake.write_table(
        "mentions",
        pd.DataFrame(
            {
                "symbol": ["AAA"],
                "date": [date(2024, 2, 28)],
                "symbols": [["BBB"]],
                "available_at": [seen],
            }
        ),
    )
    return lake


def test_default_tables_take_a_symbols_list_and_skip_tables_without_symbols(
    tmp_path: Path,
) -> None:
    assert slice_tables(_custom_lake(tmp_path / "src")) == ("articles", "mentions", "ticks")


def test_a_slice_keeps_the_source_registry_and_copies_a_named_table_whole(
    tmp_path: Path,
) -> None:
    source = _custom_lake(tmp_path / "src")
    out = tmp_path / "s"
    report = slice_lake(
        source,
        out,
        ["AAA"],
        POINT,
        since=date(2024, 2, 28),
        tables=["ticks", "articles", "rates", "mentions"],
    )
    sliced = Lake(out, registry=source.registry)
    assert list(sliced.read_table("ticks")["symbol"]) == ["AAA"]
    # `since` keeps an article stamped exactly at 00:00 UTC of that day, not one a µs earlier.
    assert list(sliced.read_table("articles")["article_id"]) == ["midnight"]
    assert list(sliced.read_table("rates")["date"]) == [date(2024, 2, 28)]  # no symbol: whole
    assert len(sliced.read_table("mentions")) == 1  # its `symbol` decides, not `symbols`
    assert report.rows == {"ticks": 1, "articles": 1, "rates": 1, "mentions": 1}
    assert report.out == out and report.as_of == POINT


def test_the_manifest_text_and_the_log(
    sworld: FundamentalWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "America/New_York")  # the cutoff is written in UTC regardless
    time_module.tzset()
    try:
        with capture_logs() as logs:
            report = slice_lake(
                sworld.clean, tmp_path / "s", ["AAA"], POINT, tables=["instruments", "prices_daily"]
            )
    finally:
        monkeypatch.undo()
        time_module.tzset()
    expected = {
        "as_of": "2024-03-01",
        "cutoff": POINT.cutoff.astimezone(UTC).isoformat(),
        "since": None,
        "symbols": ["AAA"],
        "tables": report.rows,
    }
    assert expected["cutoff"].endswith("+00:00")
    text = (tmp_path / "s" / MANIFEST).read_bytes().decode("utf-8")
    assert text == json.dumps(expected, indent=2, sort_keys=True) + "\n"
    events = [e for e in logs if e["event"] == "data.slice.table"]
    assert [(e["table"], e["rows"]) for e in events] == list(report.rows.items())


def test_the_error_messages_are_exact(sworld: FundamentalWorld, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^no tables given$"):
        slice_tables(sworld.clean, [" "])
    with pytest.raises(ValueError, match=r"^no symbols given$"):
        slice_lake(sworld.clean, tmp_path / "a", [], POINT)
    with pytest.raises(
        ValueError, match=r"^symbols must be a sequence of tickers, not one string$"
    ):
        slice_lake(sworld.clean, tmp_path / "b", "AAA", POINT)  # type: ignore[arg-type]
